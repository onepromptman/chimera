"""Graph arc — the planner-emitted-DAG runtime (frontier rebuild, ADR-009).

The one arc whose shape is data: a planner node (read-only fence, frontier
tier) emits a GraphPlan — phases of role-fenced nodes, reads restricted to
strictly earlier phases — and graph.admit() clamps it against the operator's
levers BEFORE any work node runs. Nodes within a phase fan out in parallel
(the driving session runs them as concurrent Agent calls); a barrier sits
between phases; every output is schema-gated and persisted, so "which node
broke" is a read of committed state, not a tracing rig.

Loops live in code, bounded, never in the plan:
  - admission refusal  -> ONE re-plan lap; the refusal text (which names the
    lever that would widen the posture) is fed back to the planner
  - verify refutation  -> critique->rewrite laps in _submit_verify,
    bounded by the CHIMERA_GRAPH_REPAIR_LAPS lever
  - each node is an agent's own tool loop — the convergence plane the DAG
    deliberately does not model

State machine:
  plan -> run (phase 0..N with barriers) -> wrap -> verify -> done | halted

Null tolerance: `node:` and `verify:` labels degrade (a lost gather is fewer
candidates; the fan-in and the terminal panel judge what survived); `plan`
and `wrap` are the two calls nothing can compensate for, so they halt.

At a terminal PASS the run is compressed into an L2 abstract (run_abstract:
ask, shape, the artifact's BLUF, what the panel refuted en route, degrade and
repair tallies) and APPENDED as run-summary-<n>, so a retry cannot overwrite
what the previous attempt learned. The write is fail-open — memory is a
bonus, the verdict is the contract.

The duck-typed arc surface (start / load / save / pending_calls / submit /
expire_timeouts / verify_verdict + state.phase) is what cli.py and the parity
suites dispatch over; a second arc ships by presenting the same one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from .. import arc_memory, graph, runner
from ..agents import resolve_models
from ..levers import GraphLevers, graph_levers, mutant_cadence
from ..models import (
    WRAP_BODY_MAX,
    AgentCall,
    AuditTrail,
    CriticOpinion,
    GraphArtifact,
    GraphNodeOutput,
    GraphPlan,
    GraphShape,
    MutantRun,
    TaskRecord,
    VerifyResult,
    _Strict,
)
from ..verify import lite, mutants, schema_gate
from ._common import ARC_STATE_FILE, PriorsSeed, priors_block

# Work nodes get a longer leash than the 300s default: a maker or executor
# slice can be a real build-and-test loop (mirrors build's passthrough shape,
# shorter ceiling — a graph slice is one slice, not a whole build).
# plan and wrap share the node ceiling (audit OP-15): both are frontier-tier
# tool loops, and the 300s default meant a container reclaim mid-plan HALTED
# the task — expiry on plan/wrap is still the halting class, just no longer
# hair-triggered.
CALL_CEILINGS: dict[str, int] = {"node": 1800, "plan": 1800, "wrap": 1800}

# frontmatter + render overhead the panel window absorbs above the body
# ceiling (first real run, 2026-09-04: a 52k body lost its Open findings to a
# 48k cap and the lap was refuted for what the critics could not see)
WRAP_BODY_HEADROOM = 2_000
# terminal verify panel payload window: the wrap body ceiling
# (models.WRAP_BODY_MAX — refused at the schema gate above it since
# 2026-09-16, after disclosure alone was ignored by four wraps in a row) plus
# the render overhead. Truncation past this is MARKED in the prompt, never
# silent (audit OP-12); with the gate in front it is a fallback no gated
# artifact can reach, and a test pins that a body AT the ceiling renders
# inside the window.
VERIFY_PAYLOAD_MAX = WRAP_BODY_MAX + WRAP_BODY_HEADROOM

ARTIFACT_FILE = "graph-output.md"

# Verify-stage finalize key set (label format per verify/lite.py). The trigger
# is a KEY SET, not a count: a count cannot tell 2 valid opinions from 1 valid
# + 1 key collision, and it does not survive the None a recoverable null slots.
# Panel dropped 3 -> 2 critics 2026-09-16 (S1: ANGLE_ROLES = ("critic",
# "executor")) — see graph.py's _VERIFY_PANEL for the evidence.
VERIFY_KEYS = frozenset({"verify:critic1", "verify:critic2"})

# The seeded-mutant control's label (C15). Deliberately NOT a member of
# VERIFY_KEYS: the verdict's key set never grows, so the control can never
# become a third opinion, a quorum member, or a veto. It is routed away from
# `verify_opinions` at the first branch of `_submit_verify` and lands on
# `state.mutant` instead. The finalize gate WAITS for it; the verdict never
# changes because of it.
MUTANT_KEY = f"verify:{lite.MUTANT_LABEL_SUFFIX}"

# One file per control dispatch, beside verification.json (see
# `_write_mutant_record`). The push-boundary tripwire
# (tests/test_push_boundary.py) admits exactly `mutants/run-<n>.json`.
MUTANT_RECORD_DIR = "mutants"

# state.verify_opinions is reset on every repair lap, so the critique that
# caused the lap is gone by the time the run summary is written. A bounded copy
# lands on state.verify_notes — what a critic refuted is the single most useful
# thing a later run can read (audit 2026-09-02, §5.2 proposal §1).
VERIFY_NOTE_LIMIT = 5
VERIFY_NOTE_CHARS = 300


class GraphArcError(RuntimeError):
    pass


GraphStage = Literal["plan", "run", "wrap", "verify", "done", "halted"]


class GraphArcState(_Strict):
    """Persisted state for one graph run. Written after every submit."""

    spec_id: str
    slug: str
    ask: str
    context: str | None = None
    # operator's G1 pick; None = planner proposes. Typed as the Literal so a
    # hand-edited state file with a garbled shape fails validation at load
    # (tick's corrupt-state guard parks it) instead of KeyError-ing later
    shape: GraphShape | None = None
    stage: GraphStage = "plan"
    plan: GraphPlan | None = None
    plan_repairs: int = 0  # bounded re-plan laps taken (graph.MAX_PLAN_REPAIRS)
    # the admission refusal + the refused plan's skeleton, fed back into the
    # next plan call (plan_skeleton) so the re-plan can diff, not re-derive
    plan_brief: str | None = None
    phase_index: int = 0
    outputs: dict[str, GraphNodeOutput | None] = Field(default_factory=dict)
    artifact: GraphArtifact | None = None
    verify_opinions: dict[str, object] = Field(default_factory=dict)
    verify_repairs: int = 0
    repair_brief: str | None = None
    # What the panel actually refuted, kept across the verify_opinions reset a
    # repair lap performs, so the L2 run summary can carry the critique rather
    # than a lap counter (record_verify_notes bounds it).
    verify_notes: list[str] = Field(default_factory=list)
    # exec_repairs / repair_queue / deferred_repairs / node_repair_briefs:
    # retained for record compatibility; exec-repair machinery removed
    # 2026-09-16 (tasks/20260916-optimize-dag/artifacts/graph-output.md — the
    # edge appeared in 0 of 4 real plans). The arc no longer reads or writes
    # these; they stay declared so a pre-existing committed arc-state.json
    # still deserializes (state models are extra="forbid").
    exec_repairs: dict[str, int] = Field(default_factory=dict)
    repair_queue: list[str] = Field(default_factory=list)
    deferred_repairs: list[str] = Field(default_factory=list)
    node_repair_briefs: dict[str, str] = Field(default_factory=dict)
    failure: str | None = None
    audit: AuditTrail = Field(default_factory=AuditTrail)
    log: list[str] = Field(default_factory=list)
    first_issued: dict[str, str] = Field(default_factory=dict)
    # The model each node was ACTUALLY dispatched on, recorded at issue time.
    # Models resolve at call time, so without this a lever change between a
    # maker's tick and its checker's tick lets the checker derive against a
    # model that never ran — maker≠checker holds on paper and collapses in
    # the transcript. Checkers derive from this record when it exists.
    node_models: dict[str, str] = Field(default_factory=dict)
    priors: PriorsSeed | None = None
    # The seeded-mutant control for THIS run (C15), or None when the run is
    # uncredentialed. Defaulted so every arc-state.json written before the
    # control existed still loads (state models are extra="forbid").
    mutant: MutantRun | None = None

    @property
    def phase(self) -> str:
        return {
            "plan": "plan",
            "run": "run",
            "wrap": "wrap",
            "verify": "verify",
            "done": "complete",
            "halted": "failed",
        }[self.stage]


# ---------------------------------------------------------------------------
# run_abstract — what a terminal run tells the NEXT run (L2 body)
# ---------------------------------------------------------------------------

ABSTRACT_ASK_CHARS = 200
ABSTRACT_BLUF_CHARS = 400


def first_paragraph(body: str, limit: int = ABSTRACT_BLUF_CHARS) -> str:
    """The artifact's BLUF: its first non-heading, non-empty paragraph.

    Wrap artifacts open with a "## BLUF" heading by convention, so the useful
    sentence is the first block that is neither a heading nor a horizontal
    rule. Returns "" when the body carries no such block.
    """
    for block in (body or "").split("\n\n"):
        text = " ".join(block.split())
        if not text or text.startswith("#") or set(text) <= {"-", "="}:
            continue
        return text[:limit]
    return ""


def _credential_line(state: GraphArcState) -> str:
    """What this run's control measured, for the L2 abstract.

    `mutant-caught (quantity, fatal)` or the honest `panel-uncredentialed`. A
    later run reading "caught" learns the panel caught a SEEDED defect of that
    class — never that the artifact was verified."""
    credential = lite.credential(state.mutant)
    if state.mutant is None or state.mutant.defect is None:
        return credential
    detail: list[str] = [state.mutant.defect.operator]
    if state.mutant.graded_fatal:
        detail.append("fatal")
    return f"{credential} ({', '.join(detail)})"


def run_abstract(state: GraphArcState) -> str:
    """One arc run, compressed into what a later run can act on."""
    plan = state.plan
    shape = state.shape
    if plan is not None:
        shape_line = (
            f"{shape or 'planner-proposed'} "
            f"({len(plan.phases)} phases, "
            f"{sum(len(ph.nodes) for ph in plan.phases)} nodes)"
        )
    else:
        shape_line = f"{shape or 'planner-proposed'} (no plan recorded)"

    lines = [
        f"ASK: {' '.join(state.ask.split())[:ABSTRACT_ASK_CHARS]}",
        f"SHAPE: {shape_line}",
        f"CREDENTIAL: {_credential_line(state)}",
    ]
    bluf = first_paragraph(state.artifact.body) if state.artifact is not None else ""
    lines.append(f"BLUF: {bluf}" if bluf else "BLUF: (no artifact body recorded)")
    if state.verify_notes:
        lines.append("REFUTED EN ROUTE:")
        lines += [f"- {note}" for note in state.verify_notes]
    degraded = sum(1 for v in state.outputs.values() if v is None)
    lines.append(
        f"RUN: outcome={state.phase} degraded_nodes={degraded} "
        f"plan_repairs={state.plan_repairs} verify_repairs={state.verify_repairs}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Arc plumbing — lived in arcs/_common.py until 2026-09-03, one caller each
# ---------------------------------------------------------------------------


def expired_labels(
    state: Any,
    calls: Sequence[AgentCall],
    *,
    ceilings: Mapping[str, int] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Stamped labels among *calls* older than their ceiling. The ceiling for a
    label is ceilings.get(label.split(":", 1)[0], runner.PER_BRANCH_TIMEOUT_S)."""
    ceilings = CALL_CEILINGS if ceilings is None else ceilings
    now = now or datetime.now(UTC)
    expired: list[str] = []
    for call in calls:
        stamp = state.first_issued.get(call.label)
        if stamp is None:
            continue
        issued = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        ceiling = ceilings.get(call.label.split(":", 1)[0], runner.PER_BRANCH_TIMEOUT_S)
        if (now - issued).total_seconds() > ceiling:
            expired.append(call.label)
    return expired


def record_verify_notes(state: Any, opinions: Sequence[Any]) -> None:
    """Append this lap's refuting reasons to state.verify_notes, bounded."""
    notes = getattr(state, "verify_notes", None)
    if notes is None:
        return
    for opinion in opinions:
        if len(notes) >= VERIFY_NOTE_LIMIT:
            return
        if opinion is None or not getattr(opinion, "refuted", False):
            continue
        reason = " ".join(str(getattr(opinion, "reason", "") or "").split())
        severity = getattr(opinion, "severity", None) or "unrated"
        notes.append(f"[{severity}] {reason[:VERIFY_NOTE_CHARS]}")


def repair_brief(opinions: Sequence[Any], attempt: int) -> str:
    """Turn the refuting verify opinions into a rewrite brief for the wrap
    maker. Instead of discarding the critique when verify fails, feed it back
    so the next wrap resolves it (Gohel loop pattern #1)."""
    refuting = [o for o in opinions if o is not None and getattr(o, "refuted", False)]
    if refuting:
        bullets = "\n".join(f"- [{o.severity}] {o.reason}" for o in refuting)
    else:
        bullets = (
            "- (panel refused to substantiate the artifact; strengthen sourcing "
            "and internal consistency)"
        )
    return (
        f"PRIOR DRAFT (repair attempt {attempt}) WAS REFUTED by the verify panel. "
        "Produce a corrected artifact that resolves every point below. Keep all "
        "inputs verbatim — do not re-grade or invent evidence:\n"
        f"{bullets}"
    )


def plan_skeleton(plan: GraphPlan) -> str:
    """The shape a refused plan actually had — one line per phase, node ids
    and roles. Fed back with the refusal so the re-plan lap can DIFF against
    what it emitted instead of re-deriving it blind (the planner call it costs
    is the most expensive call in the run)."""
    return "\n".join(
        f"- phase {i + 1} {phase.name!r}: "
        + ", ".join(f"{node.id}({node.role})" for node in phase.nodes)
        for i, phase in enumerate(plan.phases)
    )


def load_task_record(task_dir: Path) -> TaskRecord | None:
    """Read tasks/<id>/task.json into a TaskRecord, or None if absent/corrupt.

    Fail-open on exactly (ValidationError, OSError) — a real bug elsewhere is
    not swallowed. Returns None rather than raising so start() can surface its
    own arc-specific error."""
    record_path = task_dir / "task.json"
    if not record_path.exists():
        return None
    try:
        return TaskRecord.model_validate_json(record_path.read_text())
    except (ValidationError, OSError):
        return None


# ---------------------------------------------------------------------------
# Prompt builders (plan + wrap; node prompts live in graph.py)
# ---------------------------------------------------------------------------


_SHAPE_BRIEFS = {
    "straight": "one single-node lane per phase (make -> check -> ...); no fan-out",
    "diamond": "one fan-out phase of parallel lenses, then a fan-in judge/synthesis node",
    "pipeline": "parallel per-unit maker nodes, then an integrate node reading all units",
}


def _plan_prompt(
    slug: str,
    ask: str,
    context: str | None,
    levers: GraphLevers,
    plan_brief: str = "",
    priors: str = "",
    shape: str | None = None,
    mutant_due: bool = False,
) -> str:
    context_section = f"\nCONTEXT:\n{context}\n" if context else ""
    shape_section = (
        f"\nTHE OPERATOR PINNED THE SHAPE AT G1: {shape.upper()} — "
        f"{_SHAPE_BRIEFS[shape]}. Instantiate exactly this shape (admission "
        "enforces it); your freedom is the node briefs, tiers, and width "
        "within the posture.\n"
        if shape
        else ""
    )
    repair_section = (
        f"\nYOUR PREVIOUS PLAN WAS REFUSED AT ADMISSION:\n{plan_brief}\n"
        "Re-plan within the posture (or the named lever must be raised by the "
        "operator — you cannot raise it).\n"
        if plan_brief
        else ""
    )
    priors_section = f"\n{priors}\n" if priors else ""
    overhead = graph.overhead_calls(levers.repair_laps, 1 if mutant_due else 0)
    # disclosed, never hidden: the planner is told the machinery it plans
    # alongside, and on a credentialed run that machinery is one call larger
    mutant_section = " + one seeded-mutant control call" if mutant_due else ""
    return f"""Plan one graph run (slug={slug}). Decompose THE ASK into a
phase-structured graph of role-fenced nodes.

THE ASK:
{ask}
{context_section}{shape_section}{repair_section}{priors_section}
THE POSTURE ADMISSION WILL ENFORCE ON THIS RUN (plan within it, it is not a
suggestion):
- at most {levers.width_max} nodes per phase, {levers.phases_max} phases
- fixed machinery of {overhead} calls beyond your nodes (plan, wrap, 2-critic
  verify panel, one repair allowance{mutant_section})

Return JSON matching GraphPlan."""


def _wrap_prompt(slug: str, plan: GraphPlan, digest: str, repair_brief: str = "") -> str:
    repair_section = f"{repair_brief}\n\n" if repair_brief else ""
    n_phases = len(plan.phases)
    n_nodes = sum(len(p.nodes) for p in plan.phases)
    body_ceiling = WRAP_BODY_MAX
    return f"""Consolidate the node outputs below into this run's final
GraphArtifact (slug={slug}).

{repair_section}TASK GOAL:
{plan.goal}

NODE OUTPUTS (phase order):
{digest}

FRONTMATTER VALUES FOR THIS RUN: slug: {slug}; phases: {n_phases}; nodes:
{n_nodes}; created: the current time as ISO-8601 UTC (YYYY-MM-DDTHH:MM:SSZ);
status: "complete", or "partial" if a degraded node left a real gap.

BODY CEILING: a body over {body_ceiling:,} characters is REFUSED at submit (the
schema gate names the overage and you are re-run once); the verify panel reads
at most {VERIFY_PAYLOAD_MAX:,} characters of the rendered artifact. Keep the
body under {body_ceiling:,} characters — cut restatement, never evidence or
open findings.

Return JSON matching GraphArtifact."""


# ---------------------------------------------------------------------------
# GraphArc — public surface (Research/Reflect-compatible)
# ---------------------------------------------------------------------------


class GraphArc:
    """Resumable state machine for the graph arc."""

    ARTIFACT_FILENAME = ARTIFACT_FILE

    def __init__(self, task_dir: Path):
        self.task_dir = Path(task_dir)
        self.state_path = self.task_dir / ARC_STATE_FILE

    # ----- state load/save -------------------------------------------------

    def save(self, state: GraphArcState | None = None) -> Path:
        if state is None:
            raise GraphArcError("save requires a state")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2))
        return self.state_path

    def load(self) -> GraphArcState:
        if not self.state_path.exists():
            raise GraphArcError(f"no arc state at {self.state_path} — run start first")
        state = GraphArcState.model_validate_json(self.state_path.read_text())
        shape = self._authoritative_shape(state)
        if state.plan is not None:
            try:
                # structural re-admission (audit OP-8): a hand-widened
                # persisted plan fails HERE instead of re-issuing calls
                # admission never saw; lever clamps are deliberately not
                # re-applied (posture may legitimately change mid-task)
                graph.check_admitted(state.plan, shape)
            except graph.GraphAdmissionError as exc:
                raise GraphArcError(
                    f"persisted plan no longer passes admission structure: {exc}"
                ) from None
        return state

    def _authoritative_shape(self, state: GraphArcState) -> GraphShape | None:
        """The operator's G1 pick, read from the TASK RECORD — not from arc
        state. Re-admitting a persisted plan against a shape that lives in the
        same hand-editable file as the plan is no check at all: nulling
        `shape` in arc-state.json makes `_check_shape` a no-op and a widened
        plan reloads clean. The task record is the G1 artifact, so it is the
        authority; a disagreement is tampering and refuses loudly.

        Fail-open on a MISSING record only (mirrors `_fresh_state`: no record
        means no pick, and the planner proposes freely within the levers)."""
        record = load_task_record(self.task_dir)
        if record is None:
            return state.shape
        pinned = record.spec.shape
        if state.shape != pinned:
            raise GraphArcError(
                f"arc state pins shape {state.shape!r} but the G1 task record "
                f"pins {pinned!r} — the operator's pick is the authority and "
                "arc-state.json does not get to relax it. Restore the state "
                "file or re-run G1 intake to change the pick."
            )
        return pinned

    # ----- lifecycle -------------------------------------------------------

    def start(self, task_id: str, slug: str, ask: str, context: str | None) -> GraphArcState:
        if self.state_path.exists():
            return self.load()
        return self._fresh_state(task_id, slug, ask, context)

    def _fresh_state(self, task_id: str, slug: str, ask: str, context: str | None) -> GraphArcState:
        # The operator's G1 shape pick rides the task record; read it fail-open
        # (a missing record means no pick — the planner proposes freely).
        record = load_task_record(self.task_dir)
        shape = record.spec.shape if record is not None else None
        state = GraphArcState(spec_id=task_id, slug=slug, ask=ask, context=context, shape=shape)
        state.priors = priors_block("graph", ask)
        state.log.append(f"graph-arc start slug={slug}")
        if state.priors.rows:
            state.log.append(f"PRIORS_CONSUMED rows={state.priors.rows}")
        self.save(state)
        return state

    def expire_timeouts(self, state: GraphArcState) -> list[str]:
        calls = self.pending_calls(state)
        expired = expired_labels(state, calls)
        for label in expired:
            state = self._handle_null(state, label, kind="timeout") or state
        if expired:
            self.save(state)
        return expired

    def verify_verdict(self, state: GraphArcState) -> VerifyResult:
        """The terminal verdict, read back from verification.json."""
        verification_path = self.task_dir / "verification.json"
        if not verification_path.exists():
            raise GraphArcError("verify_verdict called before verification.json was written")
        return VerifyResult.model_validate_json(verification_path.read_text())

    # ----- pending calls ---------------------------------------------------

    def pending_calls(self, state: GraphArcState | None = None) -> list[AgentCall]:
        if state is None:
            state = self.load()
        if state.stage == "plan":
            calls = [self._plan_call(state)]
        elif state.stage == "run":
            assert state.plan is not None
            calls = graph.phase_calls(
                state.plan,
                state.phase_index,
                ask=state.ask,
                outputs=state.outputs,
                dispatched=state.node_models,
            )
        elif state.stage == "wrap":
            calls = [self._wrap_call(state)]
        elif state.stage == "verify":
            calls = self._verify_calls(state)
        else:
            calls = []  # done or halted
        # First-issue stamp per pending label, keep-earliest: the expiry clock
        # starts once, not on every re-list. Persisted by the caller (cli.py).
        for call in calls:
            state.first_issued.setdefault(call.label, call.issued_at)
        # Record the dispatched model for every node call. Persisted by the
        # same caller save that persists the first-issued stamps (cli.py).
        # First write wins: a node re-issued after a lever change keeps the
        # model its checker already derived against, so the pair stays honest.
        for call in calls:
            if call.label.startswith("node:") and call.model:
                state.node_models.setdefault(call.label.split(":", 1)[1], call.model)
        return calls

    def _plan_call(self, state: GraphArcState) -> AgentCall:
        subagent, reason = graph.subagent_for("planner")
        priors_text = state.priors.block if state.priors else ""
        return AgentCall(
            label="plan",
            prompt=_plan_prompt(
                state.slug,
                state.ask,
                state.context,
                graph_levers(),
                plan_brief=state.plan_brief or "",
                priors=priors_text,
                shape=state.shape,
                mutant_due=self._mutant_due(),
            ),
            schema_name="GraphPlan",
            # the planner rides the JUDGE tier (default = maker alias): the
            # whole run's shape is one call — the place a higher tier pays
            model=resolve_models().judge,
            phase="plan",
            subagent_type=subagent,
            selection_reason=reason,
        )

    def _wrap_call(self, state: GraphArcState) -> AgentCall:
        assert state.plan is not None
        subagent, reason = graph.subagent_for("maker")
        return AgentCall(
            label="wrap",
            prompt=_wrap_prompt(
                state.slug,
                state.plan,
                graph.outputs_digest(state.plan, state.outputs),
                repair_brief=state.repair_brief or "",
            ),
            schema_name="GraphArtifact",
            model=resolve_models().maker,
            phase="wrap",
            subagent_type=subagent,
            selection_reason=reason,
        )

    def _verify_calls(self, state: GraphArcState) -> list[AgentCall]:
        assert state.artifact is not None
        body = self._render_artifact(state.artifact)
        if len(body) > VERIFY_PAYLOAD_MAX:
            # never truncate the gated artifact SILENTLY — the panel must know
            # it judged a prefix (audit OP-12); cap sized for frontier critics
            total = len(body)
            body = body[:VERIFY_PAYLOAD_MAX] + (
                f"\n\n[chimera: artifact truncated for the verify panel at "
                f"{VERIFY_PAYLOAD_MAX} of {total} chars]"
            )
        calls = lite.critic_calls("verify", body, phase="verify")
        # pending means pending: a critic whose opinion (or null) already
        # landed is not re-issued (the v6 stage arcs re-listed the whole
        # panel; a driving session pumping calls one at a time then spun).
        pending = [c for c in calls if c.label not in state.verify_opinions]
        pending += self._mutant_calls(state, body)
        return pending

    def _mutant_calls(self, state: GraphArcState, body: str) -> list[AgentCall]:
        """The seeded-mutant control, if this run has one still outstanding.

        The payload is the SAME body the panel reads (truncation marker and
        all), with one recorded defect re-applied to it — so the only thing
        that differs between this call and the angle it rides is the artifact
        itself. A control that cannot be built degrades to `not-measured`
        rather than wedging the finalize gate behind a call nobody can issue
        (the degrade is persisted by the same caller save that persists the
        first-issue stamps; if the panel had already completed, the ordinary
        `arc submit <id> verify:mutant1 --null` re-triggers finalize).
        """
        run = state.mutant
        if run is None or run.status != "pending" or run.defect is None:
            return []
        try:
            payload = mutants.apply(run.defect, body)
        except Exception as exc:
            state.mutant = run.model_copy(
                update={"status": "not-measured", "note": f"apply failed: {type(exc).__name__}"}
            )
            state.log.append(f"MUTANT_NOT_MEASURED apply failed: {type(exc).__name__}")
            return []
        return [lite.mutant_call("verify", payload, run.defect.angle, phase="verify")]

    # ----- submit ----------------------------------------------------------

    def submit(
        self,
        state: GraphArcState,
        label: str,
        payload: object | None = None,
        kind: str = "null",
    ) -> GraphArcState:
        """Accept one agent submission. *payload* None = a degraded call."""
        if payload is None:
            return self._handle_null(state, label, kind)

        payload_json = payload if isinstance(payload, str) else json.dumps(payload)
        try:
            # the 250-call runtime ceiling (runner.AGENT_CALL_CEILING) is
            # enforced HERE, at the one door every submission walks through —
            # it went dead when the v6 arcs' issue_call sites were deleted
            # (2026-08-28 adversarial audit, OP-4)
            runner.issue_call(state.audit, label)
        except runner.CeilingExceeded as exc:
            state.stage = "halted"
            state.failure = str(exc)
            state.log.append(f"CEILING label={label}: run aborted at the call ceiling")
            self.save(state)
            return state
        state.first_issued.pop(label, None)

        if label == "plan":
            return self._submit_plan(state, payload_json)
        if label.startswith("node:"):
            return self._submit_node(state, label, payload_json)
        if label == "wrap":
            return self._submit_wrap(state, payload_json)
        if label.startswith("verify:"):
            return self._submit_verify(state, label, payload_json)
        raise GraphArcError(f"unknown submit label: {label}")

    def _handle_null(self, state: GraphArcState, label: str, kind: str) -> GraphArcState:
        """Route one degraded submission (null | timeout | threw).

        `node:` and `verify:` degrade: a null critic slots as None (the
        thin-panel check below `<2 valid` fails the run rather than treating a
        missing opinion as anything else) and a null work node slots as None
        (the fan-in and the terminal panel judge what survived). `plan` and
        `wrap` are the two calls nothing can compensate for, so they halt.

        Counter semantics follow runner.py: record_null does NOT touch
        agent_calls_attempted, so the attempted increment happens here —
        through issue_call, so the 250-call ceiling binds on the null path too
        (audit OP-4)."""
        try:
            runner.issue_call(state.audit, label)
        except runner.CeilingExceeded as exc:
            runner.record_null(state.audit, label, kind=kind)
            state.stage = "halted"
            state.failure = str(exc)
            state.log.append(f"CEILING label={label}: run aborted at the call ceiling")
            self.save(state)
            return state
        runner.record_null(state.audit, label, kind=kind)
        state.log.append(f"NULL_AGENT label={label} kind={kind}")
        state.first_issued.pop(label, None)
        if label.startswith("verify:"):
            return self._submit_verify(state, label, None)
        if label.startswith("node:"):
            return self._slot_node(state, label, None)
        state.stage = "halted"
        state.failure = f"null submission at label {label!r} (primary maker)"
        self.save(state)
        return state

    # ----- plan ------------------------------------------------------------

    def _submit_plan(self, state: GraphArcState, payload_json: str) -> GraphArcState:
        if state.stage != "plan":
            raise GraphArcError(f"plan submission but arc is in stage {state.stage}")
        plan = schema_gate.validate_as(GraphPlan, json.loads(payload_json))
        try:
            graph.admit(plan, graph_levers(), shape=state.shape)
        except graph.GraphAdmissionError as exc:
            if state.plan_repairs < graph.MAX_PLAN_REPAIRS:
                state.plan_repairs += 1
                state.plan_brief = f"{exc}\n\nTHE PLAN YOU EMITTED:\n{plan_skeleton(plan)}"
                state.log.append(f"PLAN_REFUSED attempt={state.plan_repairs}: {exc}")
                self.save(state)
                return state
            state.stage = "halted"
            state.failure = (
                f"graph arc halted: plan refused at admission after "
                f"{state.plan_repairs} re-plan(s): {exc}"
            )
            self.save(state)
            return state
        state.plan = plan
        state.plan_brief = None  # consumed
        state.stage = "run"
        state.phase_index = 0
        state.log.append(
            f"PLAN_ADMITTED phases={len(plan.phases)} "
            f"nodes={sum(len(p.nodes) for p in plan.phases)} "
            f"estimated_calls="
            f"{graph.estimated_calls(plan, graph_levers().repair_laps, 1 if self._mutant_due() else 0)}"
        )
        self.save(state)
        return state

    # ----- run (phase barriers) ---------------------------------------------

    def _submit_node(self, state: GraphArcState, label: str, payload_json: str) -> GraphArcState:
        node_id = label.split(":", 1)[1]
        out = schema_gate.validate_as(GraphNodeOutput, json.loads(payload_json))
        if out.node_id != node_id:
            raise GraphArcError(
                f"node payload declares node_id {out.node_id!r} but was submitted "
                f"for label {label!r} — refusing the mismatch"
            )
        return self._slot_node(state, label, out)

    def _slot_node(
        self, state: GraphArcState, label: str, out: GraphNodeOutput | None
    ) -> GraphArcState:
        if state.stage != "run":
            raise GraphArcError(f"node submission {label!r} but arc is in stage {state.stage}")
        assert state.plan is not None
        node_id = label.split(":", 1)[1]
        current = state.plan.phases[state.phase_index]
        current_ids = {n.id for n in current.nodes}
        if node_id not in current_ids:
            known = node_id in {n.id for p in state.plan.phases for n in p.nodes}
            raise GraphArcError(
                f"node {node_id!r} is not in the current phase "
                f"{current.name!r}"
                + (" (submitted out of phase order)" if known else " (unknown node id)")
            )
        if node_id in state.outputs:
            raise GraphArcError(f"node {node_id!r} already submitted")
        state.outputs[node_id] = out
        if out is None:
            state.log.append(
                f"NODE_DEGRADED {node_id} phase={state.plan.phases[state.phase_index].name}"
            )
        # Barrier: advance only when every node of the current phase landed.
        while state.phase_index < len(state.plan.phases) and all(
            n.id in state.outputs for n in state.plan.phases[state.phase_index].nodes
        ):
            state.phase_index += 1
        if state.phase_index >= len(state.plan.phases):
            state.stage = "wrap"
        self.save(state)
        return state

    # ----- wrap + verify ----------------------------------------------------

    def _submit_wrap(self, state: GraphArcState, payload_json: str) -> GraphArcState:
        if state.stage != "wrap":
            raise GraphArcError(f"wrap submission but arc is in stage {state.stage}")
        artifact = schema_gate.validate_as(GraphArtifact, json.loads(payload_json))
        state.artifact = artifact
        rendered = self._render_artifact(artifact)
        artifacts_dir = self.task_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / ARTIFACT_FILE).write_text(rendered, encoding="utf-8")
        state.repair_brief = None  # consumed; re-set only on re-refutation
        state.stage = "verify"
        # Schedule the seeded-mutant control ONCE per run, here — the moment
        # the artifact the panel will read exists. Both guards matter: a run
        # that already has a control keeps it, and `verify_repairs == 0` means
        # a repair lap re-wraps without ever re-dispatching one.
        if state.mutant is None and state.verify_repairs == 0:
            self._schedule_mutant(state, rendered)
        self.save(state)
        return state

    # ----- the seeded-mutant control (C15) ---------------------------------

    def _mutant_due(self) -> bool:
        """Is the NEXT verdict a credentialed one?

        Derived from the committed verdict population (`mutants.due`), so the
        cadence survives a restart without a counter file of its own.
        Fail-open: an unreadable queue reports "no" and the run proceeds
        uncredentialed."""
        try:
            return mutants.due(self.task_dir.parent, mutant_cadence())
        except Exception:
            return False

    def _schedule_mutant(self, state: GraphArcState, rendered: str) -> None:
        """Seed one defect into a COPY of the artifact, or record why not.

        Never raises and never blocks: not due -> no control at all (the
        verdict reads `panel-uncredentialed`); due but nothing seedable, or a
        defect past the panel's payload window -> `not-measured`. The seeding
        is pure Python (verify/mutants.py), so the maker never authors its own
        credential."""
        try:
            if not self._mutant_due():
                return
            defect = mutants.seed(rendered, self.task_dir.parent.parent)
        except Exception as exc:  # a control must never break the run it measures
            self._not_measured(state, f"seed failed: {type(exc).__name__}")
            return
        if defect is None:
            self._not_measured(state, mutants.NO_OPERATOR_NOTE)
        elif not mutants.within_payload(defect, VERIFY_PAYLOAD_MAX):
            self._not_measured(state, mutants.PAST_WINDOW_NOTE)
        else:
            state.mutant = MutantRun(status="pending", defect=defect, label=MUTANT_KEY)
            state.log.append(
                f"MUTANT_SEEDED operator={defect.operator} angle={defect.angle} "
                f"class={defect.defect_class}"
            )

    def _not_measured(self, state: GraphArcState, note: str) -> None:
        base = state.mutant or MutantRun(label=MUTANT_KEY)
        state.mutant = base.model_copy(
            update={"status": "not-measured", "note": note[:500], "opinion": None}
        )
        state.log.append(f"MUTANT_NOT_MEASURED {note[:120]}")

    def _record_mutant(self, state: GraphArcState, payload_json: str | None) -> None:
        """Grade the control — and NEVER raise.

        `caught` means the angle refuted the seeded variant; `missed` means it
        did not. Both are facts about the PANEL, recorded beside the verdict
        and changing nothing about it (verify/lite.credential). Every failure
        path — a null, a timeout, a payload that does not validate, a control
        that was never scheduled — is `not-measured`, because an unanswerable
        control is not evidence in either direction.
        """
        run = state.mutant
        if run is None or run.status != "pending":
            # nothing is outstanding: a stray or duplicate submit cannot mint a
            # credential. No control scheduled stays `panel-uncredentialed`; a
            # control already graded keeps what it measured.
            state.log.append(f"MUTANT_IGNORED no pending control for {MUTANT_KEY}")
            return
        base = run
        try:
            if payload_json is None:
                raise ValueError("null or timed-out control call")
            opinion = schema_gate.validate_as(CriticOpinion, json.loads(payload_json))
        except Exception as exc:
            self._not_measured(state, f"{type(exc).__name__}: {exc}")
        else:
            caught = opinion.refuted
            state.mutant = base.model_copy(
                update={
                    "status": "caught" if caught else "missed",
                    "opinion": opinion,
                    "graded_fatal": opinion.severity == "fatal",
                    "note": None,
                }
            )
            operator = base.defect.operator if base.defect else "unknown"
            state.log.append(
                f"MUTANT_{'CAUGHT' if caught else 'MISSED'} operator={operator} "
                f"severity={opinion.severity or 'unrated'}"
            )
        self._write_mutant_record(state)

    def _write_mutant_record(self, state: GraphArcState) -> None:
        """Persist one control dispatch to tasks/<id>/mutants/run-<n>.json.

        n = 1 + the records already there (the `append_run_summary`
        convention), so a retry never clobbers what a previous attempt
        measured. Written through the same door verification.json uses, so the
        CLI's existing per-task checkpoint commits it with no cli.py change.
        Fail-open: an unwritable record is a lost datum, never a lost run.
        """
        if state.mutant is None:
            return
        try:
            records = self.task_dir / MUTANT_RECORD_DIR
            records.mkdir(parents=True, exist_ok=True)
            attempt = 1 + len(list(records.glob("run-*.json")))
            (records / f"run-{attempt}.json").write_text(
                state.mutant.model_dump_json(indent=2), encoding="utf-8"
            )
        except OSError as exc:
            state.log.append(f"MUTANT_RECORD_DEGRADED: {type(exc).__name__}")

    def _submit_verify(
        self, state: GraphArcState, label: str, payload_json: str | None
    ) -> GraphArcState:
        """The whole verify tail, in one function (it was four, one caller
        each, until 2026-09-03).

        Slots one critic opinion — or None, for a recoverable null — through
        the schema gate and saves. Until both VERIFY_KEYS are present that is
        the entire story. Once the panel is complete it re-reads the
        freshest PERSISTED state (the 2026-07-02 fatal-revert fix: never
        finalize a stale in-memory object), computes the lite verdict and
        finalizes one of three ways:

          - passed -> stage 'done', the fail-open L2 run summary,
            verification.json;
          - genuinely refuted (>=2 valid critics) with repair budget left ->
            the critique becomes state.repair_brief, the panel resets and the
            arc rewinds to wrap: ONE bounded critique -> rewrite lap (Gohel
            patterns #1/#2), no verification.json yet — it is not terminal;
          - thin panel (<2 valid, nothing a rewrite can fix) or budget
            exhausted -> stage 'halted' + verification.json.
        """
        if state.stage != "verify":
            raise GraphArcError(f"verify submission '{label}' but arc is in stage {state.stage}")
        if label == MUTANT_KEY:
            # routed away from the verdict at the FIRST branch: the control's
            # opinion never reaches verify_opinions, so it can never become a
            # third opinion, an open finding, or a veto
            self._record_mutant(state, payload_json)
        elif payload_json is None:
            state.verify_opinions[label] = None
        else:
            opinion = schema_gate.validate_as(CriticOpinion, json.loads(payload_json))
            state.verify_opinions[label] = opinion.model_dump()
        state.first_issued.pop(label, None)
        self.save(state)
        if not VERIFY_KEYS.issubset(state.verify_opinions):
            return state
        if state.mutant is not None and state.mutant.status == "pending":
            # the control has not landed; the verdict WAITS for it, and does
            # not change because of it. Writing a verdict now would carry a
            # credential field that misstates what was measured.
            return state

        opinions: list[CriticOpinion | None] = []
        for i in (1, 2):
            raw = state.verify_opinions.get(f"verify:critic{i}")
            opinions.append(CriticOpinion.model_validate(raw) if raw else None)

        state = self.load()
        if state.stage != "verify":
            raise GraphArcError(f"finalize_verify in wrong stage: {state.stage}")
        # the credential rides the verdict; `passed` is still computed from the
        # two real critics alone (verify/lite.verdict)
        result = lite.verdict("lite", opinions, mutant=state.mutant)
        verification_path = self.task_dir / "verification.json"

        if result.passed:
            state.stage = "done"
            # a PASS can still carry a non-fatal refutation (fatal-only veto)
            # — record it the same way the repair branch does, or the L2
            # abstract's "REFUTED EN ROUTE" block is empty even though the
            # panel refuted something.
            if any(o is not None and o.refuted for o in opinions):
                record_verify_notes(state, opinions)
            try:
                # the L2 run summary is a bonus, the verdict is the contract:
                # a locked or corrupt DB must never strand a PASS mid-finalize
                # (audit 2026-09-02, P2-12)
                self._summarize_to_memory(state)
            except Exception as exc:
                state.log.append(f"MEMORY_WRITE_DEGRADED on_pass: {type(exc).__name__}")
            verification_path.write_text(result.model_dump_json(indent=2))
            self.save(state)
            return state

        genuine = result.valid_critic_count >= 2
        if genuine and state.verify_repairs < graph_levers().repair_laps:
            record_verify_notes(state, opinions)
            state.verify_repairs += 1
            state.repair_brief = repair_brief(opinions, state.verify_repairs)
            state.verify_opinions = {}  # fresh panel for the next lap
            state.stage = "wrap"
            state.log.append(
                f"VERIFY_REPAIR attempt={state.verify_repairs} "
                f"({result.unrefuted_count}/{result.valid_critic_count} unrefuted) -> re-wrap"
            )
            self.save(state)
            return state

        state.stage = "halted"
        if not genuine:
            state.failure = (
                f"graph arc halted: lite verify failed — thin panel "
                f"({result.valid_critic_count} valid critic(s), cannot repair)"
            )
        else:
            state.failure = (
                f"graph arc halted: lite verify failed after {state.verify_repairs} "
                f"repair(s) ({result.unrefuted_count}/{result.valid_critic_count} unrefuted)"
            )
        verification_path.write_text(result.model_dump_json(indent=2))
        self.save(state)
        return state

    # ----- rendering + memory ---------------------------------------------

    def _render_artifact(self, artifact: GraphArtifact) -> str:
        fm = artifact.frontmatter
        lines = [
            "---",
            f"arc: {fm.arc}",
            f"slug: {fm.slug}",
            f"created: {fm.created}",
            f"phases: {fm.phases}",
            f"nodes: {fm.nodes}",
            f"status: {fm.status}",
            "---",
            "",
            artifact.body,
            "",
        ]
        return "\n".join(lines)

    def _summarize_to_memory(self, state: GraphArcState) -> None:
        """Write the PASS run summary to layer 2 as an ABSTRACT, not counters.

        Counters ("phases=2 nodes=3 degraded_nodes=0") are telemetry: a later
        run reading them learns nothing it can act on (audit 2026-09-02, §5.2).
        Everything here already exists in state — the ask, the wrap BLUF, the
        shape, what the critics refuted on the way through, and the degrade /
        repair tallies as a one-line footer.
        """
        arc_memory.append_run_summary(
            arc_kind="graph",
            arc_id=state.spec_id,
            summary=run_abstract(state),
            tags="terminal",
        )

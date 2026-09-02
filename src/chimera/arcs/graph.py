"""Graph arc — the planner-emitted-DAG runtime (frontier rebuild).

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
  - verify refutation  -> critique->rewrite laps via the shared
    finalize_with_repair, bounded by the CHIMERA_GRAPH_REPAIR_LAPS lever
  - each node is an agent's own tool loop — the convergence plane the DAG
    deliberately does not model

State machine:
  plan -> run (phase 0..N with barriers) -> wrap -> verify -> done | halted

Null tolerance: `node:` and `verify:` labels degrade (a lost gather is fewer
candidates; the fan-in and the terminal panel judge what survived); `plan`
and `wrap` are the two calls nothing can compensate for, so they halt.

Mirrors the ReflectArc/ResearchArc duck-typed surface (start / initialize /
load / save / pending_calls / submit / expire_timeouts / verify_verdict +
state.phase) so cli.py and the parity suites dispatch over it unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .. import arc_memory, graph, runner
from ..agents import resolve_models
from ..levers import GraphLevers, graph_levers
from ..models import (
    AgentCall,
    AuditTrail,
    GraphArtifact,
    GraphNodeOutput,
    GraphPlan,
    GraphShape,
    TaskSpec,
    _Strict,
)
from ..verify import lite, schema_gate
from ._common import (
    ARC_STATE_FILE,
    PriorsSeed,
    accumulate_verify_opinion,
    arc_load,
    arc_save,
    dispatch_null,
    expired_labels,
    finalize_verify_with_repair,
    load_task_record,
    priors_block,
    read_verify_result,
    stamp_first_issued,
)

# Work nodes get a longer leash than the 300s default: a maker or executor
# slice can be a real build-and-test loop (mirrors build's passthrough shape,
# shorter ceiling — a graph slice is one slice, not a whole build).
# plan and wrap share the node ceiling (audit OP-15): both are frontier-tier
# tool loops, and the 300s default meant a container reclaim mid-plan HALTED
# the task — expiry on plan/wrap is still the halting class, just no longer
# hair-triggered.
CALL_CEILINGS: dict[str, int] = {"node": 1800, "plan": 1800, "wrap": 1800}

# terminal verify panel payload cap — truncation past this is MARKED in the
# prompt, never silent (audit OP-12); sized for frontier-tier critics
VERIFY_PAYLOAD_MAX = 48_000

ARTIFACT_FILE = "graph-output.md"


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
    plan_brief: str | None = None  # admission refusal fed back into the next plan call
    phase_index: int = 0
    outputs: dict[str, GraphNodeOutput | None] = Field(default_factory=dict)
    artifact: GraphArtifact | None = None
    verify_opinions: dict[str, object] = Field(default_factory=dict)
    verify_repairs: int = 0
    repair_brief: str | None = None
    # executor→maker repair lap (approved 2026-08-28): an executor landing
    # PAUSE re-runs the maker node(s) it read, then itself — sequentially via
    # repair_queue, bounded per executor by CHIMERA_GRAPH_REPAIR_LAPS
    exec_repairs: dict[str, int] = Field(default_factory=dict)
    repair_queue: list[str] = Field(default_factory=list)
    # Sibling executors whose PAUSE arrived while a lap was in flight. They
    # get their own lap when the queue drains — a PAUSE that never earns a
    # repair because it lost a race is a repair lap that only works for the
    # first executor in a phase.
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
    budget_overhead = graph.overhead_calls(levers.repair_laps)
    return f"""You are chimera's graph planner (slug={slug}). Decompose the ask into
a phase-structured graph of role-fenced nodes and return it as a GraphPlan.

THE ASK:
{ask}
{context_section}{shape_section}{repair_section}{priors_section}
THE CONTRACT — the DAG is data, the loop is code:
- phases run in order with a barrier between them; nodes WITHIN a phase run in
  parallel, each in a fresh context
- a node's `reads` may name node ids from STRICTLY earlier phases only — that
  is what its prompt will contain; plan the information flow explicitly
- a bounded revision is a shape, not a cycle: make -> check -> revise is three
  phases; unbounded convergence (the verify repair lap) is the engine's job,
  not the plan's

ROLES (the fence is the capability — pick the weakest role that can do the slice):
- researcher: read + web, no write. Gathers and cites; every claim anchored.
- maker: writes artifacts, no shell, no network.
- executor: shell (tests/checks), no write.
- critic: read-only + web. Asks ONE question of an artifact, once.
- judge: read-only. Scores candidates / merges verdicts; declares a winner.
(planner is you; do not plan further planner nodes.)

TIER DIAL per node: "frontier" (deep synthesis, judgment, authorship) or
"fast" (breadth-first gathering, routine transforms). Checker nodes ignore
the dial — their model derives distinct from the maker they read.

SHAPE LIBRARY (compose freely within the posture; these cover most work):
- pipeline: single nodes in sequence — deterministic transforms
- diamond: N parallel lenses -> one fan-in judge/synthesis node
- map: same brief over K disjoint slices -> a merge node that reads all K
- generate-verify: maker -> critic(s) -> (optional) revise maker

RULES ADMISSION WILL ENFORCE (plan within them, they are not suggestions):
- at most {levers.width_max} nodes per phase, {levers.phases_max} phases
- 2+ same-role researcher/maker nodes in one phase MUST all be read by one
  later node (parallel candidates need a fan-in, or disagreements land
  unexamined)
- every critic/judge node must read at least one node, and may not read
  producers of mixed tiers (maker≠checker must stay derivable)
- whole-run call budget {levers.call_budget}: your nodes + {budget_overhead} calls of fixed
  machinery (plan, wrap, verify panel, repair allowance). Plan the SMALLEST
  graph that covers the ask — single-node phases are fine, 2-3 phases is the
  normal case, breadth only where independent perspectives genuinely raise
  quality.

Return JSON matching GraphPlan: {{"goal": str (restate the ask), "rationale":
str (why this shape, one paragraph), "phases": [{{"name": slug, "nodes":
[{{"id": slug, "role": str, "brief": str, "tier": "frontier"|"fast",
"reads": [node-id]}}]}}]}}"""


def _wrap_prompt(
    slug: str, plan: GraphPlan, digest: str, repair_brief: str = ""
) -> str:
    repair_section = f"{repair_brief}\n\n" if repair_brief else ""
    n_phases = len(plan.phases)
    n_nodes = sum(len(p.nodes) for p in plan.phases)
    return f"""You are chimera's graph wrap maker (slug={slug}). Consolidate the node
outputs below into the final GraphArtifact. You merge, you never author: every
claim must trace to a node output (keep its anchors). Where a checker node
flagged a failure, resolve it from the surviving evidence or state it plainly
as an open finding — never paper over it. Degraded nodes are marked; do not
invent their content.

{repair_section}TASK GOAL:
{plan.goal}

NODE OUTPUTS (phase order):
{digest}

Produce:
- frontmatter (GraphFrontmatter): slug: {slug}; created: ISO-8601 UTC (current
  time, YYYY-MM-DDTHH:MM:SSZ); phases: {n_phases}; nodes: {n_nodes};
  status: "complete" (or "partial" if a degraded node left a real gap).
- body: markdown. BLUF first (the one-sentence answer to the goal), then the
  consolidated result, then an "open findings" section for anything a checker
  flagged that remains unresolved.

Return JSON matching GraphArtifact: {{"frontmatter": {{...}}, "body": str}}"""


def _phase_ordered(state: GraphArcState, ids: set[str]) -> list[str]:
    """*ids* in plan order — earlier phases first, so a repair lap re-runs
    producers before the nodes that read them."""
    assert state.plan is not None
    return [
        n.id for ph in state.plan.phases for n in ph.nodes if n.id in ids
    ]


def _downstream_landed(state: GraphArcState, seeds: set[str]) -> list[str]:
    """Already-landed nodes that read *seeds* transitively, in phase order.

    Reads reference strictly earlier phases, so the read graph is a DAG and
    this closure terminates. Seeds themselves are excluded — the caller
    handles those; this is the blast radius around them."""
    assert state.plan is not None
    index = {n.id: n for ph in state.plan.phases for n in ph.nodes}
    order = [n.id for ph in state.plan.phases for n in ph.nodes]
    tainted = set(seeds)
    changed = True
    while changed:
        changed = False
        for nid in order:
            if nid in tainted:
                continue
            if set(index[nid].reads) & tainted:
                tainted.add(nid)
                changed = True
    return [
        nid
        for nid in order
        if nid in tainted and nid not in seeds and nid in state.outputs
    ]


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
        return arc_save(self.state_path, state, GraphArcError)

    def load(self) -> GraphArcState:
        state = arc_load(self.state_path, GraphArcState, GraphArcError)
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

    def start(
        self, task_id: str, slug: str, ask: str, context: str | None
    ) -> GraphArcState:
        if self.state_path.exists():
            return self.load()
        return self._fresh_state(task_id, slug, ask, context)

    def initialize(self, spec: TaskSpec) -> GraphArcState:
        if self.state_path.exists():
            return self.load()
        return self._fresh_state(spec.id, spec.slug, spec.ask, spec.context)

    def _fresh_state(
        self, task_id: str, slug: str, ask: str, context: str | None
    ) -> GraphArcState:
        # The operator's G1 shape pick rides the task record; read it fail-open
        # (a missing record means no pick — the planner proposes freely).
        record = load_task_record(self.task_dir)
        shape = record.spec.shape if record is not None else None
        state = GraphArcState(
            spec_id=task_id, slug=slug, ask=ask, context=context, shape=shape
        )
        state.priors = priors_block("graph", ask)
        state.log.append(f"graph-arc start slug={slug}")
        if state.priors.rows:
            state.log.append(f"PRIORS_CONSUMED rows={state.priors.rows}")
        self.save(state)
        return state

    def expire_timeouts(self, state: GraphArcState) -> list[str]:
        calls = self.pending_calls(state)
        expired = expired_labels(state, calls, ceilings=CALL_CEILINGS)
        for label in expired:
            state = self._handle_null(state, label, kind="timeout") or state
        if expired:
            self.save(state)
        return expired

    def verify_verdict(self, state: GraphArcState):
        return read_verify_result(self.task_dir, GraphArcError)

    # ----- pending calls ---------------------------------------------------

    def pending_calls(self, state: GraphArcState | None = None) -> list[AgentCall]:
        if state is None:
            state = self.load()
        if state.stage == "plan":
            calls = [self._plan_call(state)]
        elif state.stage == "run":
            assert state.plan is not None
            if state.repair_queue:
                # an exec-repair lap runs SEQUENTIALLY: maker(s) first, then
                # the executor re-checks — one call pending at a time
                calls = [self._repair_call(state, state.repair_queue[0])]
            else:
                calls = graph.phase_calls(
                    state.plan,
                    state.phase_index,
                    ask=state.ask,
                    outputs=state.outputs,
                    # the L2 priors seed reaches the WORK nodes, not just the
                    # planner (audit roadmap #10 — the param was dead before)
                    priors=state.priors.block if state.priors else "",
                    dispatched=state.node_models,
                )
        elif state.stage == "wrap":
            calls = [self._wrap_call(state)]
        elif state.stage == "verify":
            calls = self._verify_calls(state)
        else:
            calls = []  # done or halted
        stamp_first_issued(state, calls)
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
            ),
            schema_name="GraphPlan",
            # the planner rides the JUDGE tier (default = maker alias): the
            # whole run's shape is one call — the place a higher tier pays
            model=resolve_models().judge,
            phase="plan",
            subagent_type=subagent,
            selection_reason=reason,
        )

    def _repair_call(self, state: GraphArcState, node_id: str) -> AgentCall:
        assert state.plan is not None
        for phase in state.plan.phases:
            for node in phase.nodes:
                if node.id == node_id:
                    return graph.node_call(
                        state.plan,
                        phase.name,
                        node,
                        ask=state.ask,
                        outputs=state.outputs,
                        repair_note=state.node_repair_briefs.get(node_id, ""),
                        dispatched=state.node_models,
                    )
        raise GraphArcError(f"repair queue references unknown node {node_id!r}")

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
        return [c for c in calls if c.label not in state.verify_opinions]

    # ----- submit ----------------------------------------------------------

    def submit(
        self,
        state=None,
        label=None,
        payload=None,
        kind: str = "null",
    ) -> GraphArcState:
        """Accept one agent submission. Dual-form, matching the other arcs."""
        if isinstance(state, str):
            actual_payload = label
            label = state
            state = self.load()
            payload = actual_payload
        if state is None:
            state = self.load()
        assert label is not None, "submit requires a label"

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

    def _route_null(self, state: GraphArcState, label: str) -> GraphArcState:
        """dispatch_null's recoverable router: a null verify critic slots as
        None (proportional majority tolerates it); a null work node slots as
        None (the fan-in and the terminal panel judge what survived)."""
        if label.startswith("verify:"):
            return self._submit_verify(state, label, None)
        return self._slot_node(state, label, None)

    def _handle_null(self, state: GraphArcState, label: str, kind: str) -> GraphArcState:
        return dispatch_null(
            state,
            label,
            kind,
            recoverable=("node:", "verify:"),
            route=self._route_null,
            save=self.save,
        )

    # ----- plan ------------------------------------------------------------

    def _submit_plan(self, state: GraphArcState, payload_json: str) -> GraphArcState:
        if state.stage != "plan":
            raise GraphArcError(f"plan submission but arc is in stage {state.stage}")
        plan = schema_gate.validate("GraphPlan", json.loads(payload_json))
        try:
            graph.admit(plan, graph_levers(), shape=state.shape)
        except graph.GraphAdmissionError as exc:
            if state.plan_repairs < graph.MAX_PLAN_REPAIRS:
                state.plan_repairs += 1
                state.plan_brief = str(exc)
                state.log.append(
                    f"PLAN_REFUSED attempt={state.plan_repairs}: {exc}"
                )
                self.save(state)
                return state
            state.stage = "halted"
            state.failure = (
                f"graph arc halted: plan refused at admission after "
                f"{state.plan_repairs} re-plan(s): {exc}"
            )
            self.save(state)
            return state
        warning = graph.judge_tier_warning(plan)
        if warning:
            state.log.append(warning)
        state.plan = plan
        state.plan_brief = None  # consumed
        state.stage = "run"
        state.phase_index = 0
        state.log.append(
            f"PLAN_ADMITTED phases={len(plan.phases)} "
            f"nodes={sum(len(p.nodes) for p in plan.phases)} "
            f"estimated_calls={graph.estimated_calls(plan, graph_levers().repair_laps)}"
        )
        self.save(state)
        return state

    # ----- run (phase barriers) ---------------------------------------------

    def _submit_node(self, state: GraphArcState, label: str, payload_json: str) -> GraphArcState:
        node_id = label.split(":", 1)[1]
        out = schema_gate.validate("GraphNodeOutput", json.loads(payload_json))
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
            raise GraphArcError(
                f"node submission {label!r} but arc is in stage {state.stage}"
            )
        assert state.plan is not None
        node_id = label.split(":", 1)[1]
        # A repair-lap head bypasses the current-phase check: the maker being
        # repaired lives in an EARLIER phase (reads reference strictly earlier
        # phases), and its id was removed from outputs when the lap queued.
        repairing = bool(state.repair_queue) and node_id == state.repair_queue[0]
        if not repairing:
            if node_id in state.repair_queue:
                raise GraphArcError(
                    f"node {node_id!r} is queued for repair but not at the "
                    f"head — a lap runs in order; submit "
                    f"{state.repair_queue[0]!r} first"
                )
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
        if repairing:
            state.repair_queue.pop(0)
            state.node_repair_briefs.pop(node_id, None)
            state.log.append(
                f"NODE_REPAIRED {node_id}"
                if out is not None
                else f"NODE_REPAIR_DEGRADED {node_id}"
            )
            if not state.repair_queue:
                self._drain_deferred_repair(state)
        elif out is None:
            state.log.append(
                f"NODE_DEGRADED {node_id} "
                f"phase={state.plan.phases[state.phase_index].name}"
            )
        self._maybe_trigger_exec_repair(state, node_id, out)
        # Barrier: advance only when every node of the current phase landed.
        while state.phase_index < len(state.plan.phases) and all(
            n.id in state.outputs for n in state.plan.phases[state.phase_index].nodes
        ):
            state.phase_index += 1
        if state.phase_index >= len(state.plan.phases):
            state.stage = "wrap"
        self.save(state)
        return state

    def _drain_deferred_repair(self, state: GraphArcState) -> None:
        """The lap just drained — give the next deferred sibling PAUSE its own
        lap. A deferred node that is no longer landed was invalidated by the
        lap that just ran and will re-trigger on its own when it re-lands, so
        it is dropped here rather than double-queued."""
        while state.deferred_repairs:
            candidate = state.deferred_repairs.pop(0)
            out = state.outputs.get(candidate)
            if out is None or not out.recommendation.startswith("PAUSE"):
                continue
            self._maybe_trigger_exec_repair(state, candidate, out)
            if state.repair_queue:
                return

    def _maybe_trigger_exec_repair(
        self, state: GraphArcState, node_id: str, out: GraphNodeOutput | None
    ) -> None:
        """The executor→maker repair lap (approved 2026-08-28): make → check →
        revise embedded as a bounded loop in CODE, never as phases. An
        executor landing PAUSE re-runs the maker node(s) it read (carrying
        the executor's findings), then itself — up to
        CHIMERA_GRAPH_REPAIR_LAPS laps per executor. On exhaustion the PAUSE
        stands and rides the digest: flags never block (80/20)."""
        if out is None or not out.recommendation.startswith("PAUSE"):
            return
        assert state.plan is not None
        index = {n.id: n for p in state.plan.phases for n in p.nodes}
        node = index.get(node_id)
        if node is None or node.role != "executor":
            return  # a non-executor PAUSE has no repair edge; it rides the digest
        maker_reads = [r for r in node.reads if index[r].role == "maker"]
        if not maker_reads:
            return  # nothing upstream to repair
        if state.repair_queue:
            # A lap is already in flight for a sibling executor. This PAUSE is
            # neither silently forgotten NOR permanently skipped: it is
            # DEFERRED and gets its own lap when the queue drains (audit R-3
            # made this a log line; a log is not a repair). Note the common
            # case never reaches here — a sibling reading the SAME maker is
            # invalidated by that maker's repair below and re-runs in the lap.
            if node_id not in state.deferred_repairs:
                state.deferred_repairs.append(node_id)
            state.log.append(
                f"EXEC_REPAIR_DEFERRED {node_id}: a lap is already in flight "
                f"(head {state.repair_queue[0]!r}) — queued for its own lap"
            )
            return
        laps = state.exec_repairs.get(node_id, 0)
        if laps >= graph_levers().repair_laps:
            state.log.append(
                f"EXEC_REPAIR_EXHAUSTED {node_id} laps={laps} — PAUSE stands, rides the digest"
            )
            return
        state.exec_repairs[node_id] = laps + 1
        findings = out.output

        # Everything downstream of a repaired maker is now stale. A sibling
        # that already landed PROCEED did so against maker content that no
        # longer exists — leaving its verdict in `outputs` ships an approval
        # of a deleted artifact. Invalidate the transitive closure and re-run
        # it in the same lap, in phase order (makers first, readers after).
        stale = _downstream_landed(state, set(maker_reads))
        for maker_id in maker_reads:
            state.outputs.pop(maker_id, None)
            state.node_models.pop(maker_id, None)
            state.node_repair_briefs[maker_id] = (
                f"Executor node '{node_id}' ran this work and reported failures "
                f"(repair lap {laps + 1}):\n{findings}"
            )
        for stale_id in stale:
            state.outputs.pop(stale_id, None)
            state.node_models.pop(stale_id, None)
            if stale_id == node_id:
                state.node_repair_briefs[stale_id] = (
                    f"Re-run your checks against the repaired upstream work "
                    f"(repair lap {laps + 1})."
                )
            else:
                state.node_repair_briefs[stale_id] = (
                    f"Upstream node(s) {', '.join(maker_reads)} were repaired "
                    f"after executor '{node_id}' reported failures (repair lap "
                    f"{laps + 1}). Your previous result judged content that no "
                    "longer exists — re-run against the repaired work."
                )
        state.repair_queue = _phase_ordered(state, set(maker_reads) | set(stale))
        state.log.append(
            f"EXEC_REPAIR node={node_id} lap={laps + 1} "
            f"makers={','.join(maker_reads)} "
            f"invalidated={','.join(i for i in stale if i != node_id) or 'none'}"
        )

    # ----- wrap + verify ----------------------------------------------------

    def _submit_wrap(self, state: GraphArcState, payload_json: str) -> GraphArcState:
        if state.stage != "wrap":
            raise GraphArcError(f"wrap submission but arc is in stage {state.stage}")
        artifact = schema_gate.validate("GraphArtifact", json.loads(payload_json))
        state.artifact = artifact
        artifacts_dir = self.task_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / ARTIFACT_FILE).write_text(
            self._render_artifact(artifact), encoding="utf-8"
        )
        state.repair_brief = None  # consumed; re-set only on re-refutation
        state.stage = "verify"
        self.save(state)
        return state

    def _submit_verify(
        self, state: GraphArcState, label: str, payload_json: str | None
    ) -> GraphArcState:
        return accumulate_verify_opinion(
            state,
            label,
            payload_json,
            error_cls=GraphArcError,
            finalize=self.finalize_verify,
            save=self.save,
            load=self.load,
        )

    def finalize_verify(self, opinions: list) -> GraphArcState:
        return finalize_verify_with_repair(
            self.task_dir,
            self.load,
            opinions,
            error_cls=GraphArcError,
            arc_name="graph",
            save=self.save,
            on_pass=self._summarize_to_memory,
            max_repairs=graph_levers().repair_laps,
        )

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
        plan = state.plan
        shape = (
            f"phases={len(plan.phases)} nodes={sum(len(p.nodes) for p in plan.phases)}"
            if plan is not None
            else "no plan recorded"
        )
        degraded = sum(1 for v in state.outputs.values() if v is None)
        summary = (
            f"phase={state.phase} {shape} degraded_nodes={degraded} "
            f"plan_repairs={state.plan_repairs} verify_repairs={state.verify_repairs}"
        )
        arc_memory.summarize_run(
            arc_kind="graph",
            arc_id=state.spec_id,
            summary=summary,
            tags="terminal",
        )

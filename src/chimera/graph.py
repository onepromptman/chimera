"""Graph runtime — admission + compilation for planner-emitted DAGs.

The synthesis of the DAG-vs-loop debate (ADR-009,
decisions/plans/2026-08-28-frontier-graph-rebuild.md): **the DAG is data, the
loop is code**. A GraphPlan is phase-structured and a node may read only
nodes from strictly earlier phases, so cycles are unrepresentable rather
than checked. The bounded back-edges (verify critique->rewrite, one re-plan
lap on admission refusal) live in arcs/graph.py with static bounds — never
in the plan.

This module is pure logic over data:

  - admit(plan, levers)  — the gate every plan passes BEFORE any node runs.
    Refusal is loud and names the lever that would widen it; admission never
    silently narrows a plan (a false positive an operator can see beats a
    silent mutation nobody reviews). Estimated call count is REPORTED, not
    enforced here (2026-09-16) — the only binding ceiling left is the runner's
    hard AGENT_CALL_CEILING, checked at every submit door.
  - estimated_calls()    — the deterministic call-count estimate, logged and
    disclosed to the planner; no longer a lever admit() clamps against.
  - node_model()         — per-node model resolution. Checker nodes derive
    DISTINCT-BY-CONSTRUCTION from the producers they read (the existing
    derive_research_critic_model rule, generalized); a checker reading
    producers of both tiers is refused at admission because no alias distinct
    from both exists.
  - checker_brief()      — THE prompt builder for checker nodes. Its
    signature is exactly (ask, rubric, artifacts): maker transcripts are
    structurally unavailable (the arc stores node outputs only), so a checker
    cannot inherit the maker's blind spots. This is the input-set invariant;
    tests/test_graph_arc.py holds it.
  - phase_calls()        — compile one phase's unsubmitted nodes to
    AgentCalls (parallel within the phase, barrier between phases).

Reads NO environment: levers arrive as data from levers.graph_levers()
(tests assert this module never touches os.environ).
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from . import routing
from .agents import (
    MakerCheckerViolation,
    ResolvedModels,
    derive_research_critic_model,
    resolve_models,
)
from .levers import GraphLevers
from .models import (
    GRAPH_CHECKER_ROLES,
    GRAPH_PRODUCER_ROLES,
    AgentCall,
    GraphNode,
    GraphNodeOutput,
    GraphPlan,
)
from .roles import ROSTER_NAME, fence_for


class GraphAdmissionError(RuntimeError):
    """A plan the current levers refuse. The message names the lever (or the
    structural rule) so the refusal is actionable, not mysterious."""


# The one bounded re-plan lap (admission refusal -> feed the refusal back to
# the planner once). A constant, not a lever: convergence niceties don't get
# operator dials. No longer pre-charged in overhead_calls() (2026-09-16): it
# has fired 0 times across 4 real runs, so estimating for it inflated a
# REPORTED number for behavior that doesn't happen; the re-plan BEHAVIOR
# (admission refusal -> one feedback lap) is unchanged.
MAX_PLAN_REPAIRS = 1

# Terminal machinery every graph run carries regardless of shape: the plan
# call, the wrap call, and the verify panel. Panel dropped 3 -> 2 critics
# 2026-09-16 (tasks/20260916-optimize-dag/artifacts/graph-output.md): the
# dropped "general REFUTE" angle was the only non-executing, non-verifying
# one — frozen run record (tests/data/): four panels, 12 committed opinions,
# 0 graded fatal, 3 runs failed on material-only refutations whose authors
# wrote the conclusion survives.
_PLAN_CALLS = 1
_WRAP_CALLS = 1
_VERIFY_PANEL = 2  # must equal len(verify.lite.ANGLE_ROLES)
# The seeded-mutant control (C15), on a credentialed run only: ONE extra call,
# dispatched at lap 0 and never re-dispatched by a repair lap — hence a
# separate term, outside the per-lap multiplier below.
_MUTANT_CALLS = 1


def overhead_calls(repair_laps: int, mutant_calls: int = 0) -> int:
    """Fixed machinery every run carries beyond its own nodes: plan + wrap +
    verify panel + the repair allowance (each lap re-runs wrap and a fresh
    panel), plus the seeded-mutant control when this run is a credentialed one
    (`mutant_calls=1`; it defaults to 0, so every pre-existing caller's number
    is unchanged). REPORTED only (log + planner prompt) since 2026-09-16 —
    admit() no longer clamps a plan's estimate against it; the runner's hard
    AGENT_CALL_CEILING is the one binding ceiling left."""
    return (
        _PLAN_CALLS
        + _WRAP_CALLS
        + _VERIFY_PANEL
        + repair_laps * (_WRAP_CALLS + _VERIFY_PANEL)
        + mutant_calls * _MUTANT_CALLS
    )


def estimated_calls(plan: GraphPlan, repair_laps: int, mutant_calls: int = 0) -> int:
    """Deterministic worst-case agent-call estimate for one run: the fixed
    overhead plus every planned node. No longer adds an executor->maker
    repair allowance (2026-09-16): that edge appeared in 0 of 4 real plans
    and its machinery was removed from arcs/graph.py (S4)."""
    return overhead_calls(repair_laps, mutant_calls) + sum(len(p.nodes) for p in plan.phases)


def _node_index(plan: GraphPlan) -> dict[str, tuple[int, GraphNode]]:
    return {node.id: (i, node) for i, phase in enumerate(plan.phases) for node in phase.nodes}


def _producer_models(
    plan: GraphPlan,
    node: GraphNode,
    models: ResolvedModels,
    dispatched: Mapping[str, str] | None = None,
) -> set[str]:
    """Resolved models of the non-checker nodes this checker reads.

    A DENY-list, not an allow-list: every role that is not itself a checker
    counts as a producer here. An allow-list silently drops any role added to
    GraphRole later — the read stops contributing a model, the checker falls
    through to the judge tier, and maker≠checker collapses without a single
    test going red. Checker reads are excluded deliberately: a judge merging
    critic verdicts derives from the judge tier, not from its critics."""
    index = _node_index(plan)
    out: set[str] = set()
    for read in node.reads:
        _, target = index[read]
        if target.role not in GRAPH_CHECKER_ROLES:
            # Prefer the model the producer was ACTUALLY dispatched on. Models
            # resolve at call time, so a lever change between the producer's
            # tick and its checker's tick would otherwise have the checker
            # derive against a model that never ran — distinct on paper, equal
            # in the transcript. The dispatch record is the ground truth.
            recorded = dispatched.get(target.id) if dispatched else None
            out.add(recorded or _worker_model(target, models))
    return out


def _worker_model(node: GraphNode, models: ResolvedModels) -> str:
    return models.maker if node.tier == "frontier" else models.research


def node_model(
    plan: GraphPlan, node: GraphNode, dispatched: Mapping[str, str] | None = None
) -> str:
    """Model for one node, resolved at CALL time (agents.resolve_models).
    Non-checkers follow the tier dial. Checkers derive distinct-by-
    construction from the producers they read; with no producer reads (a
    judge merging critic verdicts) they run the JUDGE tier — default the
    maker alias, raisable via CHIMERA_JUDGE_MODEL, because the fan-in is the
    one place a cheap tier costs the most."""
    models = resolve_models()
    if node.role not in GRAPH_CHECKER_ROLES:
        return _worker_model(node, models)
    read_models = _producer_models(plan, node, models, dispatched)
    if not read_models:
        return models.judge
    if len(read_models) > 1:
        # admit() refuses mixed-tier producer reads; reaching here means the
        # plan bypassed admission — a domain error, not a bare unpack crash
        raise GraphAdmissionError(
            f"checker {node.id!r} reads producers on mixed tiers "
            f"({sorted(read_models)}) — this plan was never admitted"
        )
    (maker_model,) = read_models
    return derive_research_critic_model(maker_model, models)


def _check_shape(plan: GraphPlan, shape: str | None) -> None:
    """The operator's G1 pick, enforced in BOTH directions; a garbled pick
    refuses loudly rather than silently unpinning (audit OP-7)."""
    if shape is None:
        return
    if shape not in ("straight", "diamond", "pipeline"):
        raise GraphAdmissionError(
            f"unknown shape pick {shape!r} — valid picks are 'straight', "
            "'diamond', 'pipeline' (models.GraphShape); fix the task record "
            "or re-run G1 intake with a valid --shape"
        )
    widths = [len(ph.nodes) for ph in plan.phases]
    if shape == "straight" and any(w > 1 for w in widths):
        raise GraphAdmissionError(
            f"the operator pinned shape 'straight' at G1, but the plan fans "
            f"out (phase widths {widths}) — re-plan as one single-node lane "
            "per phase, or the operator re-pins the shape"
        )
    if shape in ("diamond", "pipeline") and all(w < 2 for w in widths):
        raise GraphAdmissionError(
            f"the operator pinned shape {shape!r} at G1, but the plan never "
            f"fans out (phase widths {widths}) — plan parallel "
            f"{'lenses' if shape == 'diamond' else 'units'} with a fan-in, "
            "or the operator re-pins the shape"
        )


def _check_structure(plan: GraphPlan) -> None:
    """Pure-structure invariants: role fences resolve, reads reference known
    nodes in strictly earlier phases, checkers read something, and same-role
    fan-outs have a fan-in. Environment-free, so it can re-run at LOAD time
    against a persisted plan (audit OP-8)."""
    index = _node_index(plan)
    for i, phase in enumerate(plan.phases):
        for node in phase.nodes:
            fence_for(
                node.role
            )  # unknown role is unrepresentable via the Literal, but keep the fence lookup on the hot path
            if node.role == "planner":
                raise GraphAdmissionError(
                    f"node {node.id!r} has role 'planner' — the planner EMITS "
                    "the plan, it is never a node inside one. A planner node "
                    "also runs the maker tier while sitting outside the "
                    "producer set, so any checker reading it derives the same "
                    "model and maker≠checker collapses. Re-plan with a "
                    "researcher or maker node."
                )
            for read in node.reads:
                if read not in index:
                    raise GraphAdmissionError(f"node {node.id!r} reads unknown node {read!r}")
                read_phase, _ = index[read]
                if read_phase >= i:
                    raise GraphAdmissionError(
                        f"node {node.id!r} (phase {phase.name!r}) reads {read!r} "
                        "from the same or a later phase — reads must reference "
                        "strictly earlier phases (cycles are unrepresentable, "
                        "and a same-phase read races its own barrier)"
                    )
            if node.role in GRAPH_CHECKER_ROLES and not node.reads:
                raise GraphAdmissionError(
                    f"checker node {node.id!r} reads nothing — a critic or "
                    "judge with no artifact to judge is dead weight"
                )

    # Fan-in guard: 2+ same-role producers in one phase must ALL be read by a
    # single later node — silent duplication (two nodes solve the same slice,
    # disagree, both land unexamined) gets a structural home.
    for i, phase in enumerate(plan.phases):
        by_role: dict[str, list[str]] = {}
        for node in phase.nodes:
            if node.role in GRAPH_PRODUCER_ROLES:
                by_role.setdefault(node.role, []).append(node.id)
        for role, ids in by_role.items():
            if len(ids) < 2:
                continue
            merged = any(
                set(ids).issubset(set(later.reads))
                for later_phase in plan.phases[i + 1 :]
                for later in later_phase.nodes
            )
            if not merged:
                raise GraphAdmissionError(
                    f"phase {phase.name!r} fans out {len(ids)} {role} nodes "
                    f"({', '.join(ids)}) but no later node reads all of them — "
                    "parallel candidates need a fan-in (judge/synthesis) node, "
                    "or the disagreements land unexamined"
                )


def _check_tiers(plan: GraphPlan) -> None:
    """Mixed-tier means mixed RESOLVED models, judged against the CURRENT
    model levers — deliberately NOT part of check_admitted: model posture may
    legitimately change mid-task, and node_model raises its own domain error
    if a drifted plan reaches it."""
    models = resolve_models()
    for phase in plan.phases:
        for node in phase.nodes:
            if node.role in GRAPH_CHECKER_ROLES and node.reads:
                read_models = _producer_models(plan, node, models)
                if len(read_models) > 1:
                    raise GraphAdmissionError(
                        f"checker node {node.id!r} reads producers of both tiers; "
                        "no model is distinct from both, so maker≠checker cannot "
                        "hold — split the check per tier"
                    )
                # Counting distinct producer models is not the same as proving
                # a distinct checker model EXISTS (audit R-2). Run the real
                # derivation here so an impossible posture — MAKER == CRITIC —
                # refuses into the re-plan lap at plan time, instead of raising
                # MakerCheckerViolation at the checker's phase, after the
                # maker's calls are already spent.
                if read_models:
                    (produced_on,) = read_models
                    try:
                        derive_research_critic_model(produced_on, models)
                    except MakerCheckerViolation as exc:
                        raise GraphAdmissionError(
                            f"checker node {node.id!r} has no admissible model: {exc}"
                        ) from None


def check_admitted(plan: GraphPlan, shape: str | None = None) -> GraphPlan:
    """Structural re-validation for a PERSISTED plan (audit OP-8): the shape
    pick and the structure invariants, WITHOUT the lever clamps (the posture
    may legitimately differ from plan time) and without the tier check (model
    levers are call-time). A hand-widened arc-state.json fails here at load
    instead of re-issuing calls admission never saw."""
    _check_shape(plan, shape)
    _check_structure(plan)
    return plan


def admit(plan: GraphPlan, levers: GraphLevers, shape: str | None = None) -> GraphPlan:
    """The admission gate. Raises GraphAdmissionError on refusal; returns the
    plan UNCHANGED on admission (never narrows silently). Every refusal that
    a lever could widen names that lever; a shape refusal names the G1 pick.

    *shape* is the operator's G1 pick (models.GraphShape): the framework only
    recommends, the operator decides, and the pick is ENFORCED here — a plan
    that ignores it is refused into the re-plan lap, not quietly accepted."""
    _check_shape(plan, shape)
    if len(plan.phases) > levers.phases_max:
        raise GraphAdmissionError(
            f"plan has {len(plan.phases)} phases; the current posture admits "
            f"{levers.phases_max}. Set CHIMERA_GRAPH_PHASES to widen this "
            "deliberately, or re-plan with fewer, denser phases."
        )
    for phase in plan.phases:
        if len(phase.nodes) > levers.width_max:
            raise GraphAdmissionError(
                f"phase {phase.name!r} has {len(phase.nodes)} nodes; the current "
                f"posture admits {levers.width_max} per phase. Set "
                "CHIMERA_GRAPH_WIDTH to widen this deliberately, or narrow the "
                "fan-out."
            )

    _check_structure(plan)
    _check_tiers(plan)
    return plan


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

# The SHAPE of GraphNodeOutput lives in the installed role prompts
# (agents._NODE_OUTPUT_SHAPE), which every node call already dispatches to via
# subagent_type. A call states only WHICH schema it owes and the one value the
# role prompt cannot know: this node's id.
_OUTPUT_CONTRACT = "Return JSON matching GraphNodeOutput; its node_id MUST be exactly {node_id!r}."

_DEGRADED_MARKER = "(node degraded — no output returned; do not invent its content)"


def _artifact_block(artifacts: dict[str, str | None]) -> str:
    parts = []
    for node_id, text in artifacts.items():
        body = text if text is not None else _DEGRADED_MARKER
        parts.append(f"### upstream node `{node_id}`\n{body}")
    return "\n\n".join(parts)


def checker_brief(ask: str, rubric: str, artifacts: dict[str, str | None], node_id: str) -> str:
    """THE prompt builder for checker-role nodes (input-set invariant).

    The signature is the fence: a checker sees exactly the ask, its own
    rubric, the read artifacts, and its own node id (needed to satisfy the
    output contract — the checker's identity, not maker context). No plan
    rationale, no maker reasoning, no sibling chatter — a reviewer sharing
    the maker's context inherits its blind spots, so the maker's context is
    not reachable from here.

    The checker discipline and the GraphNodeOutput shape are the critic/judge
    role prompts' job (agents._CHECKER_DISCIPLINE); the call still NAMES the
    schema it owes, because a checker never told the contract answers in prose,
    fails the gate, and degrades to --null (2026-08-28 adversarial audit,
    OP-1)."""
    return (
        f"THE ASK:\n{ask}\n\n"
        f"YOUR RUBRIC:\n{rubric}\n\n"
        f"ARTIFACTS UNDER REVIEW:\n{_artifact_block(artifacts)}\n\n"
        + _OUTPUT_CONTRACT.format(node_id=node_id)
    )


def worker_brief(
    goal: str,
    node: GraphNode,
    artifacts: dict[str, str | None],
) -> str:
    """Prompt builder for producer/executor nodes: the goal, the node's own
    brief, and its read artifacts.

    Role identity, the researcher's citation discipline and the
    GraphNodeOutput shape are the installed role prompt's job (agents.ROSTER,
    dispatched via subagent_type); the call carries only what is specific to
    it and names the schema it owes.

    No priors block either: the L2 seed goes to the PLANNER alone, which folds
    what matters into the node briefs it authors. Re-sending 1,500 identical
    chars to every node was up to 15 copies of one paragraph per run."""
    upstream = f"\n\nUPSTREAM INPUTS:\n{_artifact_block(artifacts)}" if artifacts else ""
    return (
        f"TASK GOAL:\n{goal}\n\n"
        f"YOUR SLICE:\n{node.brief}{upstream}\n\n" + _OUTPUT_CONTRACT.format(node_id=node.id)
    )


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def subagent_for(role: str) -> tuple[str | None, str]:
    """Roster mapping for a role, validated like reflect.py validates its
    picks. Falls back to a recorded generalist pick when no member fits."""
    name = ROSTER_NAME.get(role)
    if name is None:
        return None, f"graph {role} node — no roster member holds this fence yet"
    ok, reason = routing.validate_selection(
        name, frozenset({"Read"}), routing.registry_from_agents()
    )
    if not ok:
        return None, f"graph {role} node — roster pick {name!r} failed validation: {reason}"
    return name, f"graph {role} node → {name}: {reason}"


def _read_outputs(
    node: GraphNode, outputs: dict[str, GraphNodeOutput | None]
) -> dict[str, str | None]:
    artifacts: dict[str, str | None] = {}
    for read in node.reads:
        out = outputs.get(read)
        artifacts[read] = out.output if out is not None else None
    return artifacts


def node_call(
    plan: GraphPlan,
    phase_name: str,
    node: GraphNode,
    *,
    ask: str,
    outputs: dict[str, GraphNodeOutput | None],
    dispatched: Mapping[str, str] | None = None,
) -> AgentCall:
    """Compile one node to an AgentCall. Checker prompts go through
    checker_brief and ONLY checker_brief."""
    artifacts = _read_outputs(node, outputs)
    if node.role in GRAPH_CHECKER_ROLES:
        prompt = checker_brief(ask, node.brief, artifacts, node.id)
    else:
        prompt = worker_brief(plan.goal, node, artifacts)
    subagent, reason = subagent_for(node.role)
    return AgentCall(
        label=f"node:{node.id}",
        prompt=prompt,
        schema_name="GraphNodeOutput",
        model=node_model(plan, node, dispatched),
        phase=phase_name,
        subagent_type=subagent,
        selection_reason=reason,
    )


def phase_calls(
    plan: GraphPlan,
    phase_index: int,
    *,
    ask: str,
    outputs: dict[str, GraphNodeOutput | None],
    dispatched: Mapping[str, str] | None = None,
) -> list[AgentCall]:
    """AgentCalls for the current phase's not-yet-submitted nodes. The barrier
    is structural: only this phase's nodes compile, and they compile only
    against earlier-phase outputs. No node prompt carries the L2 priors seed —
    it reaches the planner alone, which writes the node briefs."""
    phase = plan.phases[phase_index]
    return [
        node_call(
            plan,
            phase.name,
            node,
            ask=ask,
            outputs=outputs,
            dispatched=dispatched,
        )
        for node in phase.nodes
        if node.id not in outputs
    ]


def outputs_digest(plan: GraphPlan, outputs: dict[str, GraphNodeOutput | None]) -> str:
    """Phase-ordered digest of every node output — the wrap maker's input."""
    parts = []
    for phase in plan.phases:
        for node in phase.nodes:
            out = outputs.get(node.id)
            if out is None:
                body = _DEGRADED_MARKER
            else:
                body = out.output
                if out.sources:
                    body += f"\n\nsources: {json.dumps(out.sources)}"
            parts.append(f"## [{phase.name}] {node.id} ({node.role}, tier={node.tier})\n{body}")
    return "\n\n".join(parts)

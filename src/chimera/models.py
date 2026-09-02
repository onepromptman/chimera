"""All chimera schemas, as Pydantic v2 models — the single contract surface.

v7 consolidation: the kernel contracts (queue, intake, verify,
AgentCall) plus the graph runtime's payloads. The eight retired arcs' payload
schemas were deleted with their arcs; TaskSpec keeps their per-arc fields so
records written before the consolidation still load.

Every payload is validated by verify/schema_gate.py before any state
transition; every model is extra="forbid".
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared scalar constraints
# ---------------------------------------------------------------------------

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"

Confidence = Field(ge=0, le=100)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Task queue (v6)
# ---------------------------------------------------------------------------

# "graph" is the ONE live arc (v7 consolidation). The other names are
# retired but stay in the Literal so task records written before the
# consolidation still load for status/history/archive; the CLI refuses to
# dispatch them.
ArcName = Literal[
    "research", "design", "proposal", "build", "reflect", "n8n", "comms",
    "gemini", "graph",
]

RETIRED_ARCS: frozenset[str] = frozenset(
    {"research", "design", "proposal", "build", "reflect", "n8n", "comms", "gemini"}
)

# One door: every new task is a graph task; the planner picks the shape.
LAUNCH_ARCS: tuple[ArcName, ...] = ("graph",)

# The operator's G1 shape pick (rev-2 design, 2026-08-28): the framework only
# recommends — a five-agent design pass with an adversarial validator rejected
# every automatic (model-driven) shape router, in both directions. None means
# the planner proposes freely within the levers; a named shape is enforced at
# admission (graph.admit), not merely suggested.
GraphShape = Literal["straight", "diamond", "pipeline"]

TaskState = Literal[
    "awaiting-input",
    "ready",
    "running",
    "awaiting-signoff",
    "failed",
    "done",
    "archived",
]

class TaskSpec(_Strict):
    """Immutable description of one task, fixed at G1 intake."""

    id: str = Field(pattern=r"^[0-9]{8}-[a-z0-9]+(-[a-z0-9]+)*$")
    slug: str = Field(pattern=SLUG_PATTERN)
    ask: str = Field(min_length=1)
    arc: ArcName
    context: str | None = None
    shape: "GraphShape | None" = None  # operator's G1 pick; None = planner proposes
    issue_number: int | None = None
    # Legacy per-arc fields (retired arcs): kept ONLY so records
    # written before the v7 consolidation still validate. New intake never
    # sets them.
    upstream: str | None = None
    target_repo: str | None = None
    n8n_target: str | None = None
    audience: str | None = None
    voice: str | None = None
    output_kind: str | None = None
    external_ok: bool = False
    manifest: str | None = None
    created: str = Field(default_factory=utcnow_iso)


class Transition(_Strict):
    from_state: TaskState | None  # None for task creation
    to_state: TaskState
    at: str = Field(default_factory=utcnow_iso)
    by: str
    note: str | None = None


class TaskRecord(_Strict):
    """The durable per-task state file (tasks/<id>/task.json)."""

    spec: TaskSpec
    state: TaskState
    claimed_by: str | None = None
    claimed_at: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    history: list[Transition] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# G1 intake (ask-once)
# ---------------------------------------------------------------------------

SocraticOutcome = Literal["proceed-top", "ask", "parallel-ab"]


class IntakeQuestion(_Strict):
    id: str = Field(pattern=r"^q[0-9]+$")
    question: str = Field(min_length=1)
    answer: str = ""


class IntakeQuestions(_Strict):
    """Serialized to tasks/<id>/questions.yaml exactly once (ask-once)."""

    task_id: str
    posted_at: str = Field(default_factory=utcnow_iso)
    questions: list[IntakeQuestion] = Field(min_length=1, max_length=5)


# ---------------------------------------------------------------------------
# Runner audit trail (hardening counters)
# ---------------------------------------------------------------------------


class AuditTrail(_Strict):
    agent_calls_attempted: int = 0
    agent_calls_returned_null: int = 0
    agent_calls_timed_out: int = 0
    agent_calls_threw: int = 0
    agent_calls_ceiling_exceeded: int = 0
    by_label: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class CriticOpinion(_Strict):
    refuted: bool = Field(description="true if wrong/unsupported/uncertain; default true on doubt")
    reason: str
    severity: Literal["fatal", "material", "cosmetic"] | None = None

# lite is the only verification tier. The --tournament flag was deleted
# 2026-07-02 (audit F1: stored but read nowhere); re-add a tier here on
# first real high-stakes need, wired end-to-end or not at all.
VerifyMode = Literal["lite"]


class VerifyResult(_Strict):
    """Written to tasks/<id>/verification.json by the verify gate.

    queue.transition() refuses awaiting-signoff -> done unless this file
    exists AND passed is true. Workers cannot self-declare done.
    """

    mode: VerifyMode
    passed: bool
    maker_model: str
    critic_model: str
    opinions: list[CriticOpinion]
    valid_critic_count: int
    unrefuted_count: int
    at: str = Field(default_factory=utcnow_iso)


# ---------------------------------------------------------------------------
# Step output contract (every step emits confidence + recommendation)
# ---------------------------------------------------------------------------

StepRecommendation = Literal["PROCEED", "PAUSE — SURFACE TO OPERATOR"]


class StepOutput(_Strict):
    confidence: int = Confidence
    recommendation: StepRecommendation


# Below this confidence, the digest flags the step for the operator (async — the arc
# keeps running; the flag rides the task's single Issue thread + digest file).
CONFIDENCE_FLAG_THRESHOLD = 70


# ---------------------------------------------------------------------------
# Artifact frontmatter
# ---------------------------------------------------------------------------

ArtifactStatus = Literal["complete", "partial"]


# ---------------------------------------------------------------------------
# Agent call descriptors (what the driving session executes)
# ---------------------------------------------------------------------------


class AgentCall(_Strict):
    """One pending agent invocation for the driving cloud session.

    The session runs this with its native Agent tool (subscription-funded),
    then feeds the JSON result back via `chimera arc submit`. `schema_name`
    names the model in this module that the payload must validate against.
    """

    label: str
    prompt: str
    schema_name: str
    model: str
    phase: str
    # Explicit specialist selection (Phase 1). When set, the driving session MUST
    # invoke Agent(subagent_type=..., model=...) explicitly — never auto-delegate.
    # None means a general-purpose pick, which is only legitimate with a recorded
    # selection_reason (mandatory + justified-exception policy; a REFUTE critic
    # reviews unjustified generalist picks). Defaults keep extra="forbid" non-breaking.
    subagent_type: str | None = None
    selection_reason: str | None = None
    selection_confidence: int | None = None
    issued_at: str = Field(default_factory=utcnow_iso)


# ---------------------------------------------------------------------------
# Graph arc payloads — the planner-emitted DAG runtime (frontier rebuild,
# the graph-runtime design).
#
# The DAG is DATA, the loop is CODE: a GraphPlan is a list of phases, and a
# node may read only nodes from STRICTLY earlier phases — cycles are
# unrepresentable, not checked. The two sanctioned back-edges (verify
# critique->rewrite, one bounded re-plan on admission refusal) live in arc
# code with static bounds, never in the plan. Schema maxima here are the HARD
# caps; the softer operator posture is enforced by graph.admit() against
# levers.graph_levers() (both refusable, both named in the error).
# ---------------------------------------------------------------------------

# Six roles over four distinct grants. The fence
# table itself lives in roles.py, keyed by this Literal; a test asserts the
# two stay in lockstep.
GraphRole = Literal["planner", "researcher", "maker", "executor", "critic", "judge"]

# The checker roles: read-only nodes whose prompt is built from exactly
# {ask, rubric, read artifacts} (graph.checker_brief — the input-set
# invariant) and whose model derives distinct from the maker they read.
GRAPH_CHECKER_ROLES: frozenset[str] = frozenset({"critic", "judge"})

# The producer roles: nodes that author candidate content. Two-plus same-role
# producers in one phase must all be read by one later node (the fan-in
# guard — silent duplication gets a structural home).
GRAPH_PRODUCER_ROLES: frozenset[str] = frozenset({"researcher", "maker"})

# Per-node cost/latency dial. "frontier" resolves to the maker alias (opus),
# "fast" to the research alias (sonnet); checker nodes ignore the dial and
# derive distinct-by-construction from the node they read.
GraphTier = Literal["frontier", "fast"]

# Hard caps (schema-level; the levers' soft defaults sit below these).
GRAPH_HARD_MAX_WIDTH = 8
GRAPH_HARD_MAX_PHASES = 10


class GraphNode(_Strict):
    """One node of a planned graph: a role (fence), a brief, a tier dial, and
    the earlier-phase node ids whose outputs feed it."""

    id: str = Field(pattern=SLUG_PATTERN)
    role: GraphRole
    brief: str = Field(min_length=1)
    tier: GraphTier = "frontier"
    reads: list[str] = Field(default_factory=list)

    @field_validator("reads")
    @classmethod
    def _reads_are_slugs(cls, v: list[str]) -> list[str]:
        for r in v:
            if not re.fullmatch(SLUG_PATTERN, r):
                raise ValueError(f"read target {r!r} is not a valid node id slug")
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate read targets: {v}")
        return v


class GraphPhase(_Strict):
    """One barrier-delimited phase. Nodes within a phase run in parallel."""

    name: str = Field(pattern=SLUG_PATTERN)
    nodes: list[GraphNode] = Field(min_length=1, max_length=GRAPH_HARD_MAX_WIDTH)


class GraphPlan(_Strict):
    """Emitted by the planner node; admitted by graph.admit() before any work
    node runs; persisted in arc state so the run is auditable and resumable."""

    goal: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    phases: list[GraphPhase] = Field(min_length=1, max_length=GRAPH_HARD_MAX_PHASES)

    @model_validator(mode="after")
    def _unique_ids_and_phase_names(self) -> "GraphPlan":
        names = [p.name for p in self.phases]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate phase names: {names}")
        ids = [n.id for p in self.phases for n in p.nodes]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate node ids across phases: {dupes}")
        return self


class GraphNodeOutput(_Strict):
    """Uniform envelope every graph node returns. Checker nodes carry their
    findings in `output` like everyone else — in-graph checkers inform the
    downstream nodes and the wrap; only the terminal verify gate binds."""

    node_id: str = Field(pattern=SLUG_PATTERN)
    output: str = Field(min_length=1)
    sources: list[str] = Field(default_factory=list)
    confidence: int = Confidence
    recommendation: StepRecommendation


class GraphFrontmatter(_Strict):
    arc: Literal["graph"] = "graph"
    slug: str = Field(pattern=SLUG_PATTERN)
    created: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    phases: int = Field(ge=1, le=GRAPH_HARD_MAX_PHASES)
    nodes: int = Field(ge=1)
    status: ArtifactStatus


class GraphArtifact(_Strict):
    """Final graph arc output (graph-output.md frontmatter + body), packaged
    for verify + signoff."""

    frontmatter: GraphFrontmatter
    body: str = Field(min_length=1)


SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "CriticOpinion": CriticOpinion,
    "StepOutput": StepOutput,
    "GraphNode": GraphNode,
    "GraphPhase": GraphPhase,
    "GraphPlan": GraphPlan,
    "GraphNodeOutput": GraphNodeOutput,
    "GraphFrontmatter": GraphFrontmatter,
    "GraphArtifact": GraphArtifact,
}
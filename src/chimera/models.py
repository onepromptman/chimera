"""All chimera schemas, as Pydantic v2 models — the single contract surface.

v7 consolidation (ADR-010): the kernel contracts (queue, intake, verify,
AgentCall) plus the graph runtime's payloads. The eight retired arcs' payload
schemas were deleted with their arcs, and TaskSpec's nine per-arc fields
followed on 2026-09-02 — a record written before the consolidation now
REFUSES to load (extra="forbid") until `Queue.migrate_task_records()` strips
them (queue.REMOVED_SPEC_FIELDS); nothing is silently tolerated and dropped.

Every payload is validated by verify/schema_gate.py before any state
transition; every model is extra="forbid".
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared scalar constraints
# ---------------------------------------------------------------------------

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"

Confidence = Field(ge=0, le=100)


def utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Task queue (v6)
# ---------------------------------------------------------------------------

# "graph" is the ONE live arc (v7 consolidation, ADR-010). The other names are
# retired but stay in the Literal so task records written before the
# consolidation still load for status/history/archive; the CLI refuses to
# dispatch them.
ArcName = Literal[
    "research",
    "design",
    "proposal",
    "build",
    "reflect",
    "n8n",
    "comms",
    "gemini",
    "graph",
]

RETIRED_ARCS: frozenset[str] = frozenset(
    {"research", "design", "proposal", "build", "reflect", "n8n", "comms", "gemini"}
)

# The operator's G1 shape pick (rev-2 design, 2026-08-28): the framework only
# recommends — a five-agent design pass with an adversarial validator rejected
# every automatic (model-driven) shape router, in both directions. None means
# the planner proposes freely within the levers; a named shape is enforced at
# admission (graph.admit), not merely suggested.
GraphShape = Literal["straight", "diamond", "pipeline"]

TaskState = Literal[
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
    shape: GraphShape | None = None  # operator's G1 pick; None = planner proposes
    issue_number: int | None = None
    # The nine per-arc fields the retired pipelines carried (brand_context,
    # upstream, target_repo, n8n_target, audience, voice, output_kind,
    # external_ok, manifest) were deleted 2026-09-02 (P2-3 / Q8). No live code
    # read them after ADR-010; they survived only to let pre-consolidation
    # records validate, and `Queue.migrate_task_records()` now strips them
    # instead (queue.REMOVED_SPEC_FIELDS). extra="forbid" means an unmigrated record
    # REFUSES to load rather than silently dropping a field it can't honor.
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
# Runner audit trail (workflow.js v2.1 hardening counters)
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

    @model_validator(mode="after")
    def _refuted_requires_a_graded_severity(self) -> CriticOpinion:
        # severity is now load-bearing (fatal-only veto): an ungraded
        # refutation cannot be checked against blocks() and would silently
        # escape it. An unrefuted opinion still carries severity=None fine —
        # there is nothing to grade.
        if self.refuted and self.severity is None:
            raise ValueError("refuted=true requires severity to be set (fatal/material/cosmetic)")
        return self


# ---------------------------------------------------------------------------
# Seeded-mutant panel credential (C15). One deterministic defect, seeded by
# pure Python into a COPY of the artifact and graded by one extra panel call:
# the maker never authors its own credential. A "caught" mutant is a floor
# raiser — it says the panel caught a seeded defect of a searched class — and
# never a certificate. The credential NEVER gates: see verify/mutants.py.
# ---------------------------------------------------------------------------


class SeededDefect(_Strict):
    """The one defect a mutant run seeds, recorded so `mutants.apply()` can
    re-apply it deterministically without re-searching the artifact."""

    operator: Literal["path-anchor", "open-finding", "quantity"]
    defect_class: str  # "citation-path" | "deleted-disclosure" | "load-bearing-number"
    offset: int = Field(ge=0)  # char offset of the mutation in the rendered artifact
    original: str = Field(max_length=200)
    mutated: str = Field(max_length=200)
    expected_catch: str  # prose: what a catching panel must say
    angle: int = Field(ge=1, le=2)  # which verify angle is expected to catch it


class MutantRun(_Strict):
    """One control dispatch, from schedule to grade.

    `status` is the whole story: `pending` (scheduled, not landed), `caught`
    (the angle refuted the seeded variant), `missed` (it did not), and
    `not-measured` for EVERY failure path — no operator applied, a null, a
    timeout, a garbage payload. Nothing here changes a verdict.
    """

    status: Literal["pending", "caught", "missed", "not-measured"] = "pending"
    defect: SeededDefect | None = None
    label: str = "verify:mutant1"
    opinion: CriticOpinion | None = None
    graded_fatal: bool = False
    note: str | None = None  # why not-measured
    at: str = Field(default_factory=utcnow_iso)


PanelCredential = Literal[
    "panel-uncredentialed",
    "mutant-caught",
    "mutant-missed",
    "mutant-not-measured",
]


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
    # opinions that did NOT refute. Informational only (fatal-only veto,
    # 2026-09-16): there is no majority count left for this to feed — a
    # refuted-but-non-fatal opinion is not unrefuted, but it does not fail the
    # run either, it lands in open_findings below.
    unrefuted_count: int
    # refutations the critic graded `cosmetic`. Informational, for the digest —
    # like any other non-fatal refutation, they land in open_findings and
    # change no outcome. Default 0 so records written before the field
    # existed still load.
    advisory_count: int = 0
    # one entry per VALID opinion with refuted=True and severity != "fatal",
    # formatted "[{severity}] {reason}". A fatal refutation vetoes the run
    # instead of landing here (see verify/lite.py). Default so records written
    # before this field existed still load (2026-09-16, fatal-only veto).
    open_findings: list[str] = Field(default_factory=list)
    # The seeded-mutant credential (C15). Both fields default so every record
    # committed before the control existed still loads: an absent credential
    # reads as `panel-uncredentialed`, never as a pass. The credential is a
    # PROPERTY OF THE PANEL, not of the artifact, and gates nothing —
    # `passed` above is computed from the two real critics alone.
    panel_credential: PanelCredential = "panel-uncredentialed"
    mutant: MutantRun | None = None
    at: str = Field(default_factory=utcnow_iso)


# ---------------------------------------------------------------------------
# Step output contract (every node output carries confidence + recommendation)
# ---------------------------------------------------------------------------

StepRecommendation = Literal["PROCEED", "PAUSE — SURFACE TO OPERATOR"]

# Below this confidence, the digest flags the step for the operator (async — the arc
# keeps running; the flag rides the task's single Issue thread + digest file).
CONFIDENCE_FLAG_THRESHOLD = 70


# ---------------------------------------------------------------------------
# Artifact frontmatter (flows/SPEC.md contracts)
# ---------------------------------------------------------------------------

ArtifactStatus = Literal["complete", "partial"]


# `BrandContext` and `AestheticRegister` lived here until 2026-09-02
# (P2-3 / Q8). They were the retired design arc's brand-token carrier, reached
# only through `TaskSpec.brand_context`; both went with that field. Git history
# keeps the token defaults.


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
# decisions/plans/2026-08-28-frontier-graph-rebuild.md).
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
    def _unique_ids_and_phase_names(self) -> GraphPlan:
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


# The wrap body ceiling, gated HERE so an artifact the verify panel could not
# read whole is refused at submit — the driver re-runs the maker once with the
# error appended — instead of being truncated for the panel. Disclosure alone
# was ignored: every wrap of the two 2026-09-16 runs (four artifacts, 52,916
# to 57,171 chars) overran a 46,000 ask, and each panel graded a cut artifact.
# Sized for frontier-tier critics (arcs/graph.VERIFY_PAYLOAD_MAX is this plus
# the render overhead); every committed artifact body loads under it, which a
# test walks, because the gate also runs when an arc-state is LOADED.
WRAP_BODY_MAX = 94_000


class GraphArtifact(_Strict):
    """Final graph arc output (graph-output.md frontmatter + body), packaged
    for verify + signoff."""

    frontmatter: GraphFrontmatter
    body: str = Field(min_length=1)

    @field_validator("body")
    @classmethod
    def _within_the_wrap_ceiling(cls, body: str) -> str:
        # the message is what the maker's one retry is told; pydantic elides
        # the value itself, so the error stays small enough to ride a prompt
        if len(body) > WRAP_BODY_MAX:
            raise ValueError(
                f"body is {len(body):,} chars; the wrap ceiling is {WRAP_BODY_MAX:,} — "
                f"cut {len(body) - WRAP_BODY_MAX:,} chars of restatement, never evidence "
                "or open findings"
            )
        return body


SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "CriticOpinion": CriticOpinion,
    "GraphNode": GraphNode,
    "GraphPhase": GraphPhase,
    "GraphPlan": GraphPlan,
    "GraphNodeOutput": GraphNodeOutput,
    "GraphFrontmatter": GraphFrontmatter,
    "GraphArtifact": GraphArtifact,
}

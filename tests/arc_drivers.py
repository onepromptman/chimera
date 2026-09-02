"""Driver library for the arc parity suites (N2, spec §3).

NOT a test file — deterministic fixtures + a driver for the one live arc
(graph). Each driver exposes ``(arc, state, task_dir)`` at a named
checkpoint; metadata is bundled in ``ARCS``. The parity suites parametrize
over ``ARCS``, so a future second arc ships only by registering here and
going green — the harness shape survives the v7 consolidation even though
the eight fixed arcs did not.

Uniform call surface:

    state = arc.submit(state, label, payload, kind="null")
    calls = arc.pending_calls(state)
    arc.verify_verdict(state)
    state.phase in {"plan"|"run"|"wrap"|"verify", "complete", "failed"}
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from chimera.arcs.graph import GraphArc
from chimera.models import TaskSpec

ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


def ago(seconds: int) -> str:
    """ISO-Z timestamp `seconds` in the past, real wall clock."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(ISO_Z)


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


def _gr_plan_payload() -> dict:
    """A modest diamond: two fast gathers -> one judge fan-in. Within every
    default lever (width 2 <= 3, phases 2 <= 5, estimate well under 40)."""
    return {
        "goal": "answer the demo ask",
        "rationale": "diamond: two independent lenses, one fan-in judge",
        "phases": [
            {"name": "gather", "nodes": [
                {"id": "gather-a", "role": "researcher", "tier": "fast",
                 "brief": "investigate angle a", "reads": []},
                {"id": "gather-b", "role": "researcher", "tier": "fast",
                 "brief": "investigate angle b", "reads": []},
            ]},
            {"name": "merge", "nodes": [
                {"id": "judge-gathers", "role": "judge",
                 "brief": "score both gathers; declare a winner and grafts",
                 "reads": ["gather-a", "gather-b"]},
            ]},
        ],
    }


def _gr_node_payload(node_id: str, confidence: int = 82) -> dict:
    return {"node_id": node_id, "output": f"finding from {node_id}",
            "sources": ["src.md:12"], "confidence": confidence,
            "recommendation": "PROCEED"}


def _gr_wrap_payload(status: str = "complete") -> dict:
    return {
        "frontmatter": {
            "arc": "graph", "slug": "demo-graph", "created": "2026-07-01T18:00:00Z",
            "phases": 2, "nodes": 3, "status": status,
        },
        "body": "## BLUF\n\nConsolidated answer from the winning gather.\n\n## Open findings\n\n(none)",
    }


def graph_fresh(tmp_path: Path):
    arc = GraphArc(tmp_path / "task")
    arc.task_dir.mkdir(parents=True, exist_ok=True)
    spec = TaskSpec(id="20260828-demo-graph", slug="demo-graph",
                     ask="answer the demo ask", arc="graph")
    state = arc.initialize(spec)
    return arc, state, arc.task_dir


def graph_to_verify(tmp_path: Path):
    arc, state, task_dir = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    state = arc.submit(state, "node:gather-a", _gr_node_payload("gather-a"), kind="null")
    state = arc.submit(state, "node:gather-b", _gr_node_payload("gather-b"), kind="null")
    state = arc.submit(state, "node:judge-gathers", _gr_node_payload("judge-gathers"), kind="null")
    state = arc.submit(state, "wrap", _gr_wrap_payload(), kind="null")
    return arc, state, task_dir


# ---------------------------------------------------------------------------
# Cross-arc metadata + shared helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArcHarness:
    arc_kind: str
    fresh: Callable[[Path], tuple[Any, Any, Path]]
    to_verify: Callable[[Path], tuple[Any, Any, Path]]
    first_stage_label: str
    passthrough: tuple[str, int] | None = None  # (label, ceiling_s) for the longest-ceiling call


ARCS: list[ArcHarness] = [
    ArcHarness("graph", graph_fresh, graph_to_verify, "plan"),
]

ARC_IDS: list[str] = [h.arc_kind for h in ARCS]


# ---------------------------------------------------------------------------
# Verify-stage payloads — the "verify:criticN" / CriticOpinion shape.
# ---------------------------------------------------------------------------


def valid_opinion(refuted: bool = False) -> dict:
    return {"refuted": refuted, "reason": "checked", "severity": "cosmetic"}


def malformed_opinion() -> dict:
    """A CriticOpinion payload that must fail schema_gate (extra field forbidden
    by `_Strict`'s `extra="forbid"`)."""
    return {"refuted": False, "reason": "checked", "severity": "cosmetic",
            "bogus_extra_field": True}


# ---------------------------------------------------------------------------
# Timeout-parity helpers — stage arcs stamp first_issued; kept as functions so
# the parity tests stay arc-agnostic if a differently-stamped arc ever returns.
# ---------------------------------------------------------------------------


def get_issued_at(arc_kind: str, state: Any, label: str) -> str | None:
    return state.first_issued.get(label)


def set_issued_at(arc_kind: str, state: Any, label: str, iso_ts: str) -> None:
    state.first_issued[label] = iso_ts

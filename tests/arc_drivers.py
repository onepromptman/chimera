"""Driver library for the arc parity suites (N2, spec §3).

NOT a test file — deterministic fixtures + a driver for the one live arc
(graph, ADR-010). Each driver exposes ``(arc, state, task_dir)`` at a named
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

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from chimera.arcs.graph import GraphArc
from chimera.models import TaskSpec

ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


def ago(seconds: int) -> str:
    """ISO-Z timestamp `seconds` in the past, real wall clock."""
    return (datetime.now(UTC) - timedelta(seconds=seconds)).strftime(ISO_Z)


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
            {
                "name": "gather",
                "nodes": [
                    {
                        "id": "gather-a",
                        "role": "researcher",
                        "tier": "fast",
                        "brief": "investigate angle a",
                        "reads": [],
                    },
                    {
                        "id": "gather-b",
                        "role": "researcher",
                        "tier": "fast",
                        "brief": "investigate angle b",
                        "reads": [],
                    },
                ],
            },
            {
                "name": "merge",
                "nodes": [
                    {
                        "id": "judge-gathers",
                        "role": "judge",
                        "brief": "score both gathers; declare a winner and grafts",
                        "reads": ["gather-a", "gather-b"],
                    },
                ],
            },
        ],
    }


def _gr_node_payload(node_id: str, confidence: int = 82) -> dict:
    return {
        "node_id": node_id,
        "output": f"finding from {node_id}",
        "sources": ["src.md:12"],
        "confidence": confidence,
        "recommendation": "PROCEED",
    }


def _gr_wrap_payload(status: str = "complete") -> dict:
    return {
        "frontmatter": {
            "arc": "graph",
            "slug": "demo-graph",
            "created": "2026-07-01T18:00:00Z",
            "phases": 2,
            "nodes": 3,
            "status": status,
        },
        "body": "## BLUF\n\nConsolidated answer from the winning gather.\n\n## Open findings\n\n(none)",
    }


def graph_fresh(tmp_path: Path):
    arc = GraphArc(tmp_path / "task")
    arc.task_dir.mkdir(parents=True, exist_ok=True)
    spec = TaskSpec(
        id="20260828-demo-graph", slug="demo-graph", ask="answer the demo ask", arc="graph"
    )
    state = arc.start(spec.id, spec.slug, spec.ask, spec.context)
    return arc, state, arc.task_dir


def _gr_mutant_wrap_payload(status: str = "complete") -> dict:
    """A wrap artifact a seed operator can actually bite on.

    The default body carries no citation, no disclosed bullet and no number,
    so `mutants.seed()` finds nothing to seed — deliberately, since most of
    the suite must run uncredentialed. This one carries a bare quantity in the
    body (operator M3), which is the operator that needs no repo fixture."""
    payload = _gr_wrap_payload(status)
    payload["body"] = (
        "## BLUF\n\nThe winning gather covered 4 lanes and held.\n\n## Open findings\n\n(none)"
    )
    return payload


def graph_to_verify(tmp_path: Path):
    arc, state, task_dir = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    state = arc.submit(state, "node:gather-a", _gr_node_payload("gather-a"), kind="null")
    state = arc.submit(state, "node:gather-b", _gr_node_payload("gather-b"), kind="null")
    state = arc.submit(state, "node:judge-gathers", _gr_node_payload("judge-gathers"), kind="null")
    state = arc.submit(state, "wrap", _gr_wrap_payload(), kind="null")
    return arc, state, task_dir


def graph_to_verify_with_mutant(tmp_path: Path, monkeypatch: Any):
    """`graph_to_verify` on a CREDENTIALED run: cadence pinned at 1 (every
    run) and a wrap artifact that seeds. The arc is at the verify stage with
    one pending control alongside the two real critics."""
    monkeypatch.setenv("CHIMERA_VERIFY_MUTANT_CADENCE", "1")
    arc, state, task_dir = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    for node in ("gather-a", "gather-b", "judge-gathers"):
        state = arc.submit(state, f"node:{node}", _gr_node_payload(node), kind="null")
    state = arc.submit(state, "wrap", _gr_mutant_wrap_payload(), kind="null")
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


def valid_opinion(refuted: bool = False, severity: str | None = None) -> dict:
    """A well-formed CriticOpinion.

    Only `fatal` blocks the run (2026-09-16: fatal-only veto + open findings —
    frozen run record in tests/data/: four panels, 12 committed opinions, 0
    graded fatal, 3 runs failed on material-only refutations whose authors
    wrote the conclusion survives).
    So a refuted opinion defaults to `material`: it lands in
    VerifyResult.open_findings and does NOT fail the run. A caller that wants
    a FAILING panel must ask for it explicitly with severity="fatal" — the
    only severity that changes the outcome.
    """
    if severity is None:
        severity = "material" if refuted else "cosmetic"
    return {
        "refuted": refuted,
        "reason": "checked",
        "severity": severity,
    }


def fatal_opinion() -> dict:
    """A refuted opinion graded `fatal` — the one severity that vetoes a run
    on its own, at any valid-opinion count (verify/lite.py `survives()`)."""
    return valid_opinion(refuted=True, severity="fatal")


def malformed_opinion() -> dict:
    """A CriticOpinion payload that must fail schema_gate (extra field forbidden
    by `_Strict`'s `extra="forbid"`)."""
    return {
        "refuted": False,
        "reason": "checked",
        "severity": "cosmetic",
        "bogus_extra_field": True,
    }


# ---------------------------------------------------------------------------
# Timeout-parity helpers — stage arcs stamp first_issued; kept as functions so
# the parity tests stay arc-agnostic if a differently-stamped arc ever returns.
# ---------------------------------------------------------------------------


def get_issued_at(arc_kind: str, state: Any, label: str) -> str | None:
    return state.first_issued.get(label)


def set_issued_at(arc_kind: str, state: Any, label: str, iso_ts: str) -> None:
    state.first_issued[label] = iso_ts

"""What arcs share with the rest of the package.

Written when eight arcs shared a spine; since the v7 consolidation (ADR-010)
the one live arc is graph, and every helper whose only caller was
``arcs/graph.py`` moved INTO it (2026-09-03) — a de-duplication layer with one
consumer is an indirection, not a spine. What is left is what more than one
module reads:

  - ARC_STATE_FILE — the persisted-state filename (arcs/graph.py, cli.py,
    digest.py)
  - PriorsSeed / priors_block() — fail-open seed-read: layer-2 arc rows
    (FTS, then recency) plus layer-1 feedback/project/pattern rows under a
    GENERAL PRIORS sub-header, each read separately guarded and the whole
    block bounded (M5 part 3; audit 2026-09-02 P1-7 + proposal §7). The seed
    rides the PLANNER call only; memory.py documents this module as the
    consumer of its ranked reads.

A second arc ships by presenting the same duck-typed surface arcs/graph.py
does (start / load / save / pending_calls / submit / expire_timeouts /
verify_verdict + state.phase) and registering in tests/arc_drivers.py::ARCS;
whatever the two then genuinely share comes back here.

No imports from arc modules here; circular-import guard.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from ..models import _Strict

# ---------------------------------------------------------------------------
# Shared file-name constant
# ---------------------------------------------------------------------------

ARC_STATE_FILE = "arc-state.json"


# ---------------------------------------------------------------------------
# PriorsSeed / priors_block — fail-open Layer-2 seed-read (M5 part 3)
# ---------------------------------------------------------------------------


class PriorsSeed(_Strict):
    rows: list[int] = Field(default_factory=list)  # consumed memory row ids
    flag: str | None = None  # set on degraded read -> digest
    block: str = ""  # prompt block ("" = no priors)


# The priors block rides the planner prompt; it is a nudge, not the brief.
# Bound it so a fat memory store cannot crowd out the actual ask.
PRIORS_BLOCK_MAX_CHARS = 1500
PRIORS_BODY_MAX_CHARS = 200


def _degraded(exc: Exception) -> str:
    return f"priors seed-read degraded: {type(exc).__name__}"


def _priors_bullet(row: Mapping[str, Any]) -> str:
    body = " ".join((row.get("body") or "").split())
    return f"- {row.get('title')}: {body[:PRIORS_BODY_MAX_CHARS]}"


def priors_block(arc_kind: str, query_text: str, *, k: int = 3) -> PriorsSeed:
    """Layer-2 + layer-1 seed-read. STRICTLY fail-open: absent/empty DB ->
    empty seed; corrupt DB / any exception -> empty seed + flag. Never raises,
    never blocks an arc start.

    Three independent reads, each with its own guard (audit 2026-09-02, P1-7:
    one blanket try meant an FTS syntax error from a reserved word in the ask
    also skipped the working recency fallback, so an ask containing NOT / AND /
    OR / NEAR silently emptied the seed):

      1. arc rows matching the ask (FTS, every token quoted by fts_or_query);
      2. the most recent arc rows, when (1) found nothing OR raised;
      3. layer-1 rows (agent != "arc", feedback/project/pattern) matching the
         same tokens — the operator's own corrections compound into the run.

    Both lists are then ``memory.decision_first``: within each section, rows
    tagged `decision` lead, so an operator-tagged decision is what survives
    PRIORS_BLOCK_MAX_CHARS when the block is truncated.
    """
    from .. import arc_memory, memory  # lazy: keeps arc import time clean

    flag: str | None = None
    fts = memory.fts_or_query(query_text)

    rows: list[dict] = []
    if fts:
        try:
            rows = arc_memory.arc_search(arc_kind=arc_kind, query=fts, limit=k)
        except Exception as exc:  # FTS syntax, locked/corrupt DB, ...
            flag = _degraded(exc)
    if not rows:
        try:
            rows = arc_memory.arc_search(arc_kind=arc_kind, limit=k)  # recency fallback
        except Exception as exc:
            flag = flag or _degraded(exc)
            rows = []

    general: list[dict] = []
    try:
        general = memory.search_general(query=fts, limit=k)
    except Exception as exc:
        flag = flag or _degraded(exc)

    if not rows and not general:
        return PriorsSeed(flag=flag)

    rows = memory.decision_first(rows)
    general = memory.decision_first(general)

    lines: list[str] = []
    if rows:
        lines.append(
            f"PRIORS — lessons from previous {arc_kind} runs (consider, do not follow blindly):"
        )
        lines += [_priors_bullet(r) for r in rows]
    if general:
        if lines:
            lines.append("")
        lines.append("GENERAL PRIORS — operator feedback and project notes:")
        lines += [_priors_bullet(r) for r in general]

    block = "\n".join(lines)[:PRIORS_BLOCK_MAX_CHARS]
    consumed = [r["id"] for r in rows] + [r["id"] for r in general]
    return PriorsSeed(rows=consumed, block=block, flag=flag)

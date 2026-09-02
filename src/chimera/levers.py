"""Autonomy levers — operator-added autonomy, never engine-assumed.

Design rules:

  - every lever defaults to the RESTRICTIVE value: doing nothing is the safe
    posture, and autonomy is something an operator adds deliberately
  - each lever widens exactly ONE rule and is named for its blast radius, so
    it cannot be reached for to get past an unrelated refusal
  - a value that is not exactly the documented form reads as unset — a typo
    is not a decision (`CHIMERA_GRAPH_WIDTH=five` behaves like unset)
  - the environment is read HERE and nowhere else; graph.py and the arcs take
    a GraphLevers value as data (tests assert graph.py reads no environment)
  - hard caps hold regardless of the lever: the ceiling above the ceiling is
    not operator-adjustable

Generically named fields (`bypass`, `skip`, `disable`) are banned — a generic
override would be the first thing reached for and the last thing reviewed.

| Lever                       | Default | Hard cap | Widens                     |
|-----------------------------|---------|----------|----------------------------|
| CHIMERA_GRAPH_WIDTH         | 3       | 8        | nodes per phase            |
| CHIMERA_GRAPH_PHASES        | 5       | 10       | phases per graph           |
| CHIMERA_GRAPH_CALL_BUDGET   | 40      | 250      | estimated calls per run    |
| CHIMERA_GRAPH_REPAIR_LAPS   | 1       | 3        | verify critique→rewrite laps |

(The model levers CHIMERA_MAKER_MODEL / CHIMERA_CRITIC_MODEL /
CHIMERA_RESEARCH_MODEL / CHIMERA_JUDGE_MODEL and the MCP grant lever
CHIMERA_RESEARCH_MCP_TOOLS live in agents.py — resolved at call time, same
philosophy: default-restrictive, strict parse, a typo is not a decision.)
"""

from __future__ import annotations

import os
import re

from pydantic import BaseModel, ConfigDict, Field

from .runner import AGENT_CALL_CEILING

GRAPH_WIDTH_DEFAULT = 3
GRAPH_WIDTH_HARD_MAX = 8

GRAPH_PHASES_DEFAULT = 5
GRAPH_PHASES_HARD_MAX = 10

GRAPH_CALL_BUDGET_DEFAULT = 40
GRAPH_CALL_BUDGET_HARD_MAX = AGENT_CALL_CEILING  # 250 — the runner aborts past this anyway

GRAPH_REPAIR_LAPS_DEFAULT = 1  # mirrors arcs/_common.MAX_VERIFY_REPAIRS
GRAPH_REPAIR_LAPS_HARD_MAX = 3


class GraphLevers(BaseModel):
    """The resolved lever values, passed to graph.admit() as data.

    The hard caps live on the TYPE, not only in the env parser: a GraphLevers
    built in code (a test, a future dispatcher) cannot carry a value the
    operator could never reach through the environment (audit OP-6)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width_max: int = Field(ge=1, le=GRAPH_WIDTH_HARD_MAX)
    phases_max: int = Field(ge=1, le=GRAPH_PHASES_HARD_MAX)
    call_budget: int = Field(ge=1, le=GRAPH_CALL_BUDGET_HARD_MAX)
    repair_laps: int = Field(ge=0, le=GRAPH_REPAIR_LAPS_HARD_MAX)


def _int_lever(name: str, default: int, lo: int, hi: int) -> int:
    """Strict parse: a pure decimal integer inside [lo, hi], or the default.

    Anything else — empty, non-numeric, negative sign, out of range — reads
    as unset. The restrictive default is what you get by mistyping."""
    raw = os.environ.get(name, "")
    if not re.fullmatch(r"[0-9]{1,6}", raw):
        return default
    value = int(raw)
    if not (lo <= value <= hi):
        return default
    return value


def graph_levers() -> GraphLevers:
    """Read the four graph levers from the environment, now. Call-time reads
    so a dispatcher can set a lever for one task without a restart."""
    return GraphLevers(
        width_max=_int_lever("CHIMERA_GRAPH_WIDTH", GRAPH_WIDTH_DEFAULT, 1, GRAPH_WIDTH_HARD_MAX),
        phases_max=_int_lever("CHIMERA_GRAPH_PHASES", GRAPH_PHASES_DEFAULT, 1, GRAPH_PHASES_HARD_MAX),
        call_budget=_int_lever(
            "CHIMERA_GRAPH_CALL_BUDGET", GRAPH_CALL_BUDGET_DEFAULT, 1, GRAPH_CALL_BUDGET_HARD_MAX
        ),
        repair_laps=_int_lever(
            "CHIMERA_GRAPH_REPAIR_LAPS", GRAPH_REPAIR_LAPS_DEFAULT, 0, GRAPH_REPAIR_LAPS_HARD_MAX
        ),
    )

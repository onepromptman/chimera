"""Role fences — the six graph-node roles over four distinct tool grants.

Capability is DERIVED from the tool grant, never declared beside it. A fence
that trusts a self-declared flag is defeated by the declaration (the audited
failure mode: a slot declaring network=False while holding a shell — a shell
IS a network client). Deriving from the grant makes the dangerous compounds
unconstructible rather than checked:

  - write + shell   -> refused at construction (a shell can write anything)
  - write + network -> refused at construction (Write plus curl in one grant)

Known residual (2026-08-28 audit, OP-5): pydantic's `model_copy(update=...)`
and `model_construct` skip validators BY DESIGN, so they can assemble a
compound fence in memory. Direct construction and `model_validate` — the two
doors every live call site uses — both refuse; treat the bypasses as
forbidden in new code.

Six roles, four distinct grants (researcher/critic share read+web;
planner/judge share read-only):

    planner     read-only                 picks the shape, can't touch the work
    researcher  read + web, no write      gathers and cites, can't alter
    maker       write, no shell, no net   authors artifacts
    executor    shell, no write           runs tests/checks
    critic      read + web, no write      refutes; never edits what it judges
    judge       read-only                 scores and merges verdicts

Since the v7 consolidation this table is the single source of tool
grants: agents.ROSTER holds exactly one AgentDef per role and reads its
allowed_tools from FENCES, so the roster and the fences cannot drift
(asserted by tests/test_graph_admission.py).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import get_args

from pydantic import BaseModel, ConfigDict, model_validator

from .models import GraphRole

WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})
SHELL_TOOLS = frozenset({"Bash"})
# Any mcp__* tool is a remote service call — network capability by definition
# (see _is_network_tool); the named set covers the built-in web tools.
NETWORK_TOOLS = frozenset({"WebFetch", "WebSearch"})


def _is_network_tool(name: str) -> bool:
    return name in NETWORK_TOOLS or name.startswith("mcp__")


class FenceViolation(ValueError):
    """A tool grant that compounds write with shell or network."""


def check_grant(role: str, tools: Iterable[str]) -> None:
    """Raise FenceViolation if *tools* compounds write with shell or network.

    The compound rule as a free function so every surface that produces a tool
    grant enforces it — not just RoleFence. agents.AgentDef carries a plain
    `allowed_tools` list that internal_roles() WIDENS with operator-granted
    MCP tools, and that widened list is what gets rendered into the installed
    .md files; validating only the fence leaves the actually-installed grant
    unchecked."""
    tools = tuple(tools)
    can_write = bool(WRITE_TOOLS.intersection(tools))
    if can_write and SHELL_TOOLS.intersection(tools):
        raise FenceViolation(
            f"role {role!r} grants write AND shell — a shell can write "
            "anything, so this compound is unconstructible; split the role"
        )
    if can_write and any(_is_network_tool(t) for t in tools):
        raise FenceViolation(
            f"role {role!r} grants write AND network — exfiltration in "
            "one grant; unconstructible, split the role"
        )


class RoleFence(BaseModel):
    """One role's tool grant. Frozen; capabilities are properties of the
    grant, so a fence cannot disagree with itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: GraphRole
    tools: tuple[str, ...]

    @property
    def can_write(self) -> bool:
        return bool(WRITE_TOOLS.intersection(self.tools))

    @property
    def has_shell(self) -> bool:
        return bool(SHELL_TOOLS.intersection(self.tools))

    @property
    def has_network(self) -> bool:
        return any(_is_network_tool(t) for t in self.tools)

    @model_validator(mode="after")
    def _no_compound_grants(self) -> RoleFence:
        check_grant(self.role, self.tools)
        return self


_READ_ONLY = ("Read", "Grep", "Glob")
_READ_WEB = ("Read", "Grep", "Glob", "WebFetch", "WebSearch")

FENCES: dict[str, RoleFence] = {
    "planner": RoleFence(role="planner", tools=_READ_ONLY),
    "researcher": RoleFence(role="researcher", tools=_READ_WEB),
    "maker": RoleFence(role="maker", tools=("Read", "Grep", "Glob", "Write", "Edit")),
    "executor": RoleFence(role="executor", tools=("Read", "Grep", "Glob", "Bash")),
    "critic": RoleFence(role="critic", tools=_READ_WEB),
    "judge": RoleFence(role="judge", tools=_READ_ONLY),
}

# Roster member per role. Since the v7 consolidation the roster IS the six
# roles (agents.ROSTER is keyed by these names and takes its tool grants from
# FENCES), so the mapping is identity — kept as a mapping because it is the
# one seam where a role could ever resolve to a differently-named subagent,
# and graph.subagent_for validates through it either way.
ROSTER_NAME: dict[str, str | None] = {role: role for role in FENCES}


def fence_for(role: str) -> RoleFence:
    try:
        return FENCES[role]
    except KeyError:
        raise FenceViolation(f"unknown role {role!r} — not in the fence table") from None


def distinct_grants() -> int:
    """Number of distinct tool-grant sets across the fences (four today)."""
    return len({frozenset(f.tools) for f in FENCES.values()})


def _assert_lockstep() -> None:
    """FENCES and ROSTER_NAME cover exactly the GraphRole Literal — import-time
    guard so a role added in models.py cannot exist without a fence."""
    roles = set(get_args(GraphRole))
    if set(FENCES) != roles or set(ROSTER_NAME) != roles:
        raise FenceViolation(
            f"fence table out of lockstep with GraphRole: roles={sorted(roles)} "
            f"fences={sorted(FENCES)} roster={sorted(ROSTER_NAME)}"
        )


_assert_lockstep()

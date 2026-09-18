"""Specialist-pick validation — the live routing core.

What survives here is the one thing the runtime calls: a deterministic check
that a planner-named subagent exists in the roster and holds the tools the
node needs (`graph.subagent_for`). The registry is built from
`chimera.agents.internal_roles()` at call time, so validation sees the same
tool grants the installed role files carry.

The v5/v6 decision path (verb->capability inference, `decide`,
`decide_domain`, the catalogue registries and the bypass tokens) was deleted
2026-09-02 (audit finding P2-3 / Q8). It was reachable only from its own
tests: the PreToolUse hook has been advisory since 2026-07-25 and the
catalogue it ranked against died with ADR-010, so the domain router scored an
empty registry and allowed unconditionally. Git history keeps it; the
properties it claimed to protect are enforced in `roles.FENCES`,
`verify/lite.py` and the maker!=checker suite.
"""

from __future__ import annotations


def registry_from_agents() -> dict[str, frozenset[str]]:
    """Build the routing registry (agent-id -> tool allowlist) from the roster
    as it would be INSTALLED right now — internal_roles() resolves models and
    applies the operator's MCP grant at call time, so validation sees the same
    grants the role files carry."""
    from .agents import internal_roles

    return {
        name: frozenset(d.allowed_tools) for name, d in internal_roles().items() if d.allowed_tools
    }


def validate_selection(
    subagent_type: str,
    required_caps: frozenset[str],
    registry: dict[str, frozenset[str]] | None = None,
) -> tuple[bool, str]:
    """Deterministically validate a planner's specialist pick.

    A pick is valid iff the agent exists in the registry and its tool allowlist
    is a superset of the required capabilities. Returns (ok, reason).
    """
    registry = registry if registry is not None else registry_from_agents()
    allow = registry.get(subagent_type)
    if allow is None:
        return (False, f"unknown specialist '{subagent_type}' (not in registry)")
    if not required_caps.issubset(allow):
        missing = sorted(required_caps - allow)
        return (
            False,
            f"'{subagent_type}' tool allowlist {sorted(allow)} is missing required "
            f"capabilities {missing}",
        )
    return (True, f"'{subagent_type}' supersets required {sorted(required_caps)}")

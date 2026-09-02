"""Capability-based routing — pure decision core.

Extracted from .claude/hooks/router-enforcer.py (validated 4/4 + 7/7 in
V5.1). The hook keeps the Claude Code I/O contract and fail-open behavior;
this module owns the decision logic and the verb->capability table so it is
importable and unit-testable. The v6 registry comes from chimera.agents
(AgentDef.allowed_tools), not from .claude/agents/*.md frontmatter.

Core correctness rule preserved: read-only work is general-purpose
territory — it is NEVER routed to a write-capable specialist just because
the prompt mentions domain keywords.
"""

from __future__ import annotations

import re

READ_ONLY_CAPS: frozenset[str] = frozenset({"Read", "Grep", "Glob"})
ALL_CAPS: frozenset[str] = frozenset({"Read", "Grep", "Glob", "Edit", "Write", "Bash"})

VERB_CAPS: dict[str, frozenset[str]] = {
    # Read-only verbs
    "search": frozenset({"Read", "Grep", "Glob"}),
    "find": frozenset({"Read", "Grep", "Glob"}),
    "look at": frozenset({"Read", "Grep", "Glob"}),
    "read": frozenset({"Read"}),
    "grep": frozenset({"Grep"}),
    "list": frozenset({"Read", "Glob"}),
    "inspect": frozenset({"Read", "Grep"}),
    "investigate": frozenset({"Read", "Grep", "Glob"}),
    "review": frozenset({"Read", "Grep"}),
    "audit": frozenset({"Read", "Grep", "Glob"}),
    "analyze": frozenset({"Read", "Grep"}),
    "summarize": frozenset({"Read"}),
    "report": frozenset({"Read"}),
    "trace": frozenset({"Read", "Grep"}),
    "diff": frozenset({"Read", "Bash"}),
    # Write verbs
    "edit": frozenset({"Read", "Edit"}),
    "modify": frozenset({"Read", "Edit"}),
    "refactor": frozenset({"Read", "Edit", "Write"}),
    "rewrite": frozenset({"Read", "Edit", "Write"}),
    "patch": frozenset({"Read", "Edit"}),
    "write a": frozenset({"Write"}),
    "create a file": frozenset({"Write"}),
    "scaffold": frozenset({"Read", "Write", "Bash"}),
    "generate code": frozenset({"Write"}),
    "author": frozenset({"Write"}),
    "implement": frozenset({"Read", "Edit", "Write"}),
    # Bash / execute verbs
    "run ": frozenset({"Bash"}),
    "execute": frozenset({"Bash"}),
    "install": frozenset({"Bash"}),
    "build": frozenset({"Read", "Edit", "Write", "Bash"}),
    "deploy": frozenset({"Bash"}),
    "test": frozenset({"Read", "Bash"}),
    "curl": frozenset({"Bash"}),
    "ssh": frozenset({"Bash"}),
    "git ": frozenset({"Bash"}),
    # Recall additions (v6.4): common write/build/infra verbs that previously fell
    # through to general-purpose. Trailing spaces where needed to avoid substring
    # false-matches (e.g. "fix " not "prefix"). These are net-new keys; no existing
    # mapping changes, so the V5.1 decide() suite is unaffected.
    "write ": frozenset({"Read", "Write"}),
    "document": frozenset({"Read", "Write"}),
    "provision": frozenset({"Read", "Edit", "Write", "Bash"}),
    "configure": frozenset({"Read", "Edit"}),
    "set up": frozenset({"Read", "Edit", "Write", "Bash"}),
    "migrate": frozenset({"Read", "Edit", "Write"}),
    "wire ": frozenset({"Read", "Edit", "Write"}),
    "update ": frozenset({"Read", "Edit"}),
    "add ": frozenset({"Read", "Edit"}),
    "fix ": frozenset({"Read", "Edit"}),
    "debug": frozenset({"Read", "Edit", "Bash"}),
}

BYPASS_TOKENS: tuple[str, ...] = (
    "[force-general-purpose]",
    "[no-route]",
    "[force-gp]",
)


def infer_required_caps(prompt: str) -> frozenset[str]:
    """Union the capability sets of every verb found in the prompt.

    Empty set means "no clear capability requirement" -> default ALLOW.
    """
    if not prompt:
        return frozenset()
    needle = prompt.lower()
    caps: set[str] = set()
    for verb, verb_caps in VERB_CAPS.items():
        if verb in needle:
            caps |= verb_caps
    return frozenset(caps)


def match_specialist(
    required: frozenset[str], registry: dict[str, frozenset[str]]
) -> list[str]:
    """Specialists whose allowlist is a superset of `required`.

    Full-allowlist agents are skipped — requesting every tool is not a
    specialization. Sorted for deterministic multi-match messages.
    """
    if not required:
        return []
    candidates: list[str] = []
    for name, allowlist in registry.items():
        if not required.issubset(allowlist):
            continue
        if allowlist == ALL_CAPS:
            continue
        candidates.append(name)
    return sorted(candidates)


def decide(
    prompt: str,
    description: str,
    registry: dict[str, frozenset[str]],
) -> tuple[str, str, list[str], frozenset[str]]:
    """Pure decision function. Returns (decision, reason, matches, required_caps).

    decision is one of: allow-bypass, allow-read-only, allow-no-match,
    allow-no-caps-inferred, deny-single, deny-multi.
    """
    haystack = (prompt or "") + "\n" + (description or "")

    if any(token in haystack for token in BYPASS_TOKENS):
        return ("allow-bypass", "bypass token present", [], frozenset())

    required = infer_required_caps(haystack)

    if not required:
        return ("allow-no-caps-inferred", "no action verbs matched", [], required)

    if required.issubset(READ_ONLY_CAPS):
        # The CORE FIX: read-only work is general-purpose territory.
        return ("allow-read-only", "required caps are read-only", [], required)

    matches = match_specialist(required, registry)

    if not matches:
        return ("allow-no-match", "no specialist allowlist matches", [], required)

    if len(matches) == 1:
        spec = matches[0]
        reason = (
            f"ROUTING ENFORCED: this Agent() call requires capabilities "
            f"{sorted(required)} which match the '{spec}' specialist's "
            f"tool allowlist. Re-issue with subagent_type='{spec}'. To bypass, "
            f"include the literal token [force-general-purpose] in your prompt."
        )
        return ("deny-single", reason, matches, required)

    opts = ", ".join(f"'{m}'" for m in matches)
    reason = (
        f"ROUTING ENFORCED (multi-match): required capabilities {sorted(required)} "
        f"match multiple specialists: {opts}. Re-issue with the single most-specific "
        f"subagent_type, or include [force-general-purpose] to bypass."
    )
    return ("deny-multi", reason, matches, required)


def registry_from_agents() -> dict[str, frozenset[str]]:
    """Build the routing registry (agent-id -> tool allowlist) from the roster
    as it would be INSTALLED right now — internal_roles() resolves models and
    applies the operator's MCP grant at call time, so validation sees the same
    grants the role files carry."""
    from .agents import internal_roles

    return {
        name: frozenset(d.allowed_tools)
        for name, d in internal_roles().items()
        if d.allowed_tools
    }


# ---------------------------------------------------------------------------
# Capability axis + explicit, context-conditional specialist selection.
#
# The router above (decide/match_specialist) is the session-boundary *enforcer*
# (it powers the fail-open PreToolUse hook and protects read-only work). It is
# tool-based and skips ALL_CAPS agents. The selection surface below is the
# *selector* the build arc calls to pick the ideal specialist for a subtask:
# it ranks tool-matched candidates by their structured `capabilities` manifest
# against the subtask context, so "write python code" picks `python-dev` over
# the prose `writer`. Capabilities drive the pick; tools gate validity.
# ---------------------------------------------------------------------------


def validate_selection(
    subagent_type: str,
    required_caps: frozenset[str],
    registry: dict[str, frozenset[str]] | None = None,
) -> tuple[bool, str]:
    """Deterministically validate a planner's specialist pick.

    A pick is valid iff the agent exists in the registry and its tool allowlist
    is a superset of the required capabilities. Returns (ok, reason). This does
    NOT apply the ALL_CAPS exclusion — an explicit, named delegation is allowed
    to a full-surface specialist; that heuristic only guards the implicit hook.
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


def _context_tokens(context: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", (context or "").lower()))


# ---------------------------------------------------------------------------
# Session-hook DOMAIN router (v6.4). `decide()` above is a coarse tool-superset
# gate: it cannot tell frontend-developer from backend-developer (identical
# tools), so it leaked general-purpose on every "no tool-unique match". This
# router ranks tool-eligible specialists by how well their *capability manifest*
# overlaps the task context — the same semantic axis the build-arc selector uses —
# and names the best fit. Posture: smart name-and-bounce, else allow. A
# PreToolUse hook cannot rewrite subagent_type, so "name-and-bounce" = deny with
# the specialist named; the session re-fires with it (delivers auto-route intent).
# ---------------------------------------------------------------------------


def catalogue_registries() -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """Build (tool_registry, capability_registry) from the tracked catalogue manifest.

    A catalogue manifest, when present, carries the session-side
    specialists with both a tool allowlist and a structured capability manifest —
    a far wider routing target set than the arc-internal ROSTER. Returns ({}, {})
    if the manifest is not importable (agents/ not on sys.path) so the hook
    degrades to allow rather than crash; the hook puts agents/ on the path first.
    """
    try:
        from catalogue.manifest import CATALOGUE
    except Exception:
        return {}, {}
    tools = {e.name: frozenset(e.tools) for e in CATALOGUE}
    caps = {e.name: frozenset(e.capabilities) for e in CATALOGUE}
    return tools, caps


def decide_domain(
    prompt: str,
    description: str,
    tool_registry: dict[str, frozenset[str]],
    cap_registry: dict[str, frozenset[str]],
) -> tuple[str, str, list[str], frozenset[str]]:
    """Domain-aware routing for a general-purpose Agent call. Pure + unit-testable.

    Returns (decision, reason, matches, required_caps). decision is one of:
    allow-bypass, allow-no-caps-inferred, allow-read-only, allow-no-match,
    allow-no-domain, deny-single, deny-multi. Only deny-* names a specialist to
    bounce to; every allow-* lets general-purpose proceed (justified exception).
    """
    haystack = (prompt or "") + "\n" + (description or "")

    if any(token in haystack for token in BYPASS_TOKENS):
        return ("allow-bypass", "bypass token present", [], frozenset())

    required = infer_required_caps(haystack)
    if not required:
        return ("allow-no-caps-inferred", "no action verbs matched", [], required)
    if required.issubset(READ_ONLY_CAPS):
        return (
            "allow-read-only",
            "required caps are read-only — general-purpose is correct",
            [],
            required,
        )

    # Tool-eligible specialists: allowlist supersets the required tools. No
    # ALL_CAPS exclusion here — capability overlap (not tool breadth) decides the
    # winner, so Bash-capable coders (CODER_BASH == ALL_CAPS) stay eligible.
    candidates = sorted(n for n, tools in tool_registry.items() if required.issubset(tools))
    if not candidates:
        return (
            "allow-no-match",
            f"no specialist tool-allowlist supersets required {sorted(required)} — "
            f"general-purpose is the justified exception",
            [],
            required,
        )

    toks = _context_tokens(haystack)
    scored = sorted(
        ((len(toks & cap_registry.get(c, frozenset())), c) for c in candidates),
        key=lambda x: (-x[0], x[1]),
    )
    best_overlap, best = scored[0]
    if best_overlap == 0:
        return (
            "allow-no-domain",
            f"tool-eligible specialists {candidates} exist but none's domain fits the "
            f"context — general-purpose is the justified exception",
            [],
            required,
        )

    top = [c for overlap, c in scored if overlap == best_overlap]
    if len(top) == 1:
        reason = (
            f"ROUTING ENFORCED: task context best matches the '{best}' specialist "
            f"({best_overlap} domain signal(s); required tools {sorted(required)}). "
            f"Re-issue with subagent_type='{best}'. To bypass, include the literal "
            f"token [force-general-purpose] in your prompt."
        )
        return ("deny-single", reason, [best], required)

    opts = ", ".join(f"'{m}'" for m in top)
    reason = (
        f"ROUTING ENFORCED (multi-match): task context ties across {opts} "
        f"({best_overlap} domain signal(s) each). Re-issue with the single "
        f"most-specific subagent_type, or include [force-general-purpose] to bypass."
    )
    return ("deny-multi", reason, top, required)

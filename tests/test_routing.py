"""Routing decision core — the router-enforcer inline suite, preserved."""

from chimera.routing import decide, registry_from_agents

REGISTRY = {
    "telemetry-analyst": frozenset({"Read", "Grep", "Glob", "Bash"}),
    "python-dev": frozenset({"Read", "Grep", "Glob", "Edit", "Write"}),
    "frontend-dev": frozenset({"Read", "Grep", "Glob", "Edit", "Write"}),
    "deck-designer": frozenset({"Read", "Glob", "Write"}),
}

CASES = [
    (
        "v5-false-positive scenario (read-only transcript mining)",
        "search session transcripts for memory drift in the framework",
        "allow-read-only",
        [],
    ),
    (
        "pure-grep audit (no specialist required)",
        "investigate the agent memory files and review for drift",
        "allow-read-only",
        [],
    ),
    (
        "write-heavy refactor — multi-match by identical allowlists",
        "refactor this python script to use pathlib and type hints",
        "deny-multi",
        ["frontend-dev", "python-dev"],
    ),
    (
        "scaffold needs Write+Bash — no test specialist has both",
        "scaffold a new deck for the quarterly review",
        "allow-no-match",
        [],
    ),
    (
        "bypass token suppresses routing",
        "refactor this python script [force-general-purpose]",
        "allow-bypass",
        [],
    ),
    (
        "no verbs matched — default allow",
        "the project context for chimera framework",
        "allow-no-caps-inferred",
        [],
    ),
    (
        "execute a bash command — only telemetry-analyst has Bash",
        "run pytest against the test suite",
        "deny-single",
        ["telemetry-analyst"],
    ),
]


def test_router_enforcer_suite():
    for label, prompt, expected_decision, expected_matches in CASES:
        decision, _reason, matches, _required = decide(prompt, "", REGISTRY)
        assert decision == expected_decision, label
        assert matches == expected_matches, label


def test_v6_registry_builds_from_roster():
    registry = registry_from_agents()
    # the roster IS the six roles
    assert set(registry) == {"planner", "researcher", "maker", "executor", "critic", "judge"}
    # critics never hold an edit surface — they never edit artifacts
    assert not registry["critic"] & {"Edit", "Write", "Bash"}


# ---------------------------------------------------------------------------
# Phase 1 — capability axis + explicit, context-conditional selection.
# These run against the REAL roster (registry_from_agents), distinct from the
# hand-tuned fixture suite above.
# ---------------------------------------------------------------------------


def test_real_registry_routes_write_verbs_to_the_maker():
    """maker is the one write-holding roster role, so a write verb resolves to
    a single deny (name-and-bounce to maker), never a multi-match."""
    reg = registry_from_agents()
    decision, _reason, matches, _required = decide(
        "refactor this python module to use pathlib", "", reg
    )
    assert decision == "deny-single"
    assert matches == ["maker"]


def test_validate_selection():
    from chimera.routing import validate_selection

    reg = registry_from_agents()
    ok, _ = validate_selection("maker", frozenset({"Read", "Edit", "Write"}), reg)
    assert ok
    ok2, reason2 = validate_selection("judge", frozenset({"Read", "Edit", "Write"}), reg)
    assert not ok2 and "Edit" in reason2 and "Write" in reason2
    ok3, reason3 = validate_selection("does-not-exist", frozenset({"Read"}), reg)
    assert not ok3 and "unknown" in reason3


# ---------------------------------------------------------------------------
# v6.4 — domain-aware session router (decide_domain). The session hook now
# ranks tool-eligible specialists by capability overlap instead of leaking
# general-purpose on every tool-equal multi-match.
# ---------------------------------------------------------------------------

_DOM_TOOLS = {
    "frontend-developer": frozenset({"Read", "Grep", "Glob", "Edit", "Write"}),
    "backend-developer": frozenset({"Read", "Grep", "Glob", "Edit", "Write", "Bash"}),
    "terraform-specialist": frozenset({"Read", "Grep", "Glob", "Edit", "Write"}),
}
_DOM_CAPS = {
    "frontend-developer": frozenset({"frontend", "ui", "react", "tailwind", "component"}),
    "backend-developer": frozenset({"backend", "server", "api", "service"}),
    "terraform-specialist": frozenset({"terraform", "tf", "hcl", "iac", "module"}),
}


def test_decide_domain_names_best_specialist():
    from chimera.routing import decide_domain

    dec, reason, matches, _req = decide_domain(
        "write a react component with tailwind", "", _DOM_TOOLS, _DOM_CAPS
    )
    assert dec == "deny-single"
    assert matches == ["frontend-developer"]
    assert "frontend-developer" in reason


def test_decide_domain_allows_read_only_bypass_and_no_verb():
    from chimera.routing import decide_domain

    assert decide_domain("search and review the code", "", _DOM_TOOLS, _DOM_CAPS)[0] == "allow-read-only"
    assert decide_domain("write a thing [force-general-purpose]", "", _DOM_TOOLS, _DOM_CAPS)[0] == "allow-bypass"
    assert decide_domain("the project context for chimera", "", _DOM_TOOLS, _DOM_CAPS)[0] == "allow-no-caps-inferred"


def test_decide_domain_no_domain_overlap_allows_general_purpose():
    from chimera.routing import decide_domain

    # write task whose context matches no specialist's domain -> justified gp,
    # never force-fit a write specialist.
    dec, *_ = decide_domain("write a poem about the ocean", "", _DOM_TOOLS, _DOM_CAPS)
    assert dec == "allow-no-domain"


def test_decide_domain_no_tool_eligible_allows():
    from chimera.routing import decide_domain

    tools = {"reader": frozenset({"Read", "Grep", "Glob"})}  # no write tools
    caps = {"reader": frozenset({"docs"})}
    dec, *_ = decide_domain("write a new module", "", tools, caps)
    assert dec == "allow-no-match"


def test_decide_domain_tie_is_deny_multi():
    from chimera.routing import decide_domain

    tools = {
        "react-specialist": frozenset({"Read", "Edit", "Write"}),
        "frontend-developer": frozenset({"Read", "Edit", "Write"}),
    }
    caps = {
        "react-specialist": frozenset({"react"}),
        "frontend-developer": frozenset({"react"}),
    }
    dec, _reason, matches, _req = decide_domain("write a react widget", "", tools, caps)
    assert dec == "deny-multi"
    assert set(matches) == {"react-specialist", "frontend-developer"}


def test_catalogue_registries_degrade_to_empty_after_the_catalogue_kill():
    """the v7 consolidation deleted agents/catalogue; the hook's loader must degrade to
    ({}, {}) so the advisory router allows rather than crashes."""
    from chimera.routing import catalogue_registries

    tools, caps = catalogue_registries()
    assert tools == {} and caps == {}

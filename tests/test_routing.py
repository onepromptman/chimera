"""Specialist-pick validation — the live routing core.

The v5/v6 decision path (`decide`, `decide_domain`, `infer_required_caps`,
`match_specialist`, `catalogue_registries`, the bypass tokens) and its case
suite were deleted 2026-09-02 (P2-3 / Q8): the hook has been advisory since
2026-07-25 and the catalogue died with ADR-010, so nothing but those tests
reached them. What remains is what `graph.subagent_for` calls.
"""

from chimera.routing import registry_from_agents, validate_selection


def test_v6_registry_builds_from_roster():
    registry = registry_from_agents()
    # the roster IS the six roles since ADR-010
    assert set(registry) == {"planner", "researcher", "maker", "executor", "critic", "judge"}
    # critics never hold an edit surface — they never edit artifacts
    assert not registry["critic"] & {"Edit", "Write", "Bash"}


def test_validate_selection():
    reg = registry_from_agents()
    ok, _ = validate_selection("maker", frozenset({"Read", "Edit", "Write"}), reg)
    assert ok
    ok2, reason2 = validate_selection("judge", frozenset({"Read", "Edit", "Write"}), reg)
    assert not ok2 and "Edit" in reason2 and "Write" in reason2
    ok3, reason3 = validate_selection("does-not-exist", frozenset({"Read"}), reg)
    assert not ok3 and "unknown" in reason3


def test_validate_selection_defaults_to_the_real_roster():
    """No registry argument -> the live roster, which is what graph passes."""
    ok, _ = validate_selection("critic", frozenset({"Read"}))
    assert ok
    ok2, _ = validate_selection("critic", frozenset({"Write"}))
    assert not ok2

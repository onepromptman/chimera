"""The graph layer's pure logic: fences, levers, admission, budget, model
derivation. State-machine behavior lives in test_graph_arc.py.

Written adversarially, like the autonomy suite that inspired the
levers: the attacker's list first (compound grants, typo'd levers, cycles by
another name), the operator's list second (every refusal names its lever)."""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from chimera import graph, levers, roles
from chimera.agents import ROSTER, resolve_models
from chimera.graph import GraphAdmissionError, admit, estimated_calls, node_model
from chimera.levers import GraphLevers, graph_levers
from chimera.models import GRAPH_CHECKER_ROLES, GraphNode, GraphPlan, GraphRole
from chimera.roles import FENCES, ROSTER_NAME, FenceViolation, RoleFence, fence_for

DEFAULTS = GraphLevers(width_max=3, phases_max=5, call_budget=40, repair_laps=1)


def _plan(phases: list[dict]) -> GraphPlan:
    return GraphPlan(goal="demo goal", rationale="demo shape", phases=phases)


def _node(id: str, role: str = "researcher", tier: str = "fast", reads: list[str] | None = None) -> dict:
    return {"id": id, "role": role, "tier": tier, "brief": f"do {id}", "reads": reads or []}


def _diamond() -> GraphPlan:
    return _plan([
        {"name": "gather", "nodes": [_node("gather-a"), _node("gather-b")]},
        {"name": "merge", "nodes": [_node("judge-gathers", role="judge", reads=["gather-a", "gather-b"])]},
    ])


# ---------------------------------------------------------------------------
# Fences — the compounds are unconstructible, and the table is honest
# ---------------------------------------------------------------------------


def test_write_plus_shell_is_unconstructible():
    # pydantic surfaces the model_validator's FenceViolation as a
    # ValidationError; the point is the compound cannot be built at all.
    with pytest.raises(ValidationError, match="write AND shell"):
        RoleFence(role="maker", tools=("Read", "Write", "Bash"))


def test_write_plus_network_is_unconstructible():
    with pytest.raises(ValidationError, match="write AND network"):
        RoleFence(role="maker", tools=("Write", "WebFetch"))


def test_mcp_tools_classify_as_network_so_write_plus_mcp_is_unconstructible():
    """An mcp__* tool is a remote service call — network by definition. The
    MCP grant lever can therefore only ever widen the read+web fences."""
    with pytest.raises(ValidationError, match="write AND network"):
        RoleFence(role="maker", tools=("Read", "Write", "mcp__exa__web_search_exa"))
    fence = RoleFence(
        role="researcher",
        tools=("Read", "Grep", "Glob", "WebFetch", "WebSearch", "mcp__exa__web_search_exa"),
    )
    assert fence.has_network and not fence.can_write and not fence.has_shell


def test_every_fence_in_the_table_constructs_and_derives_honestly():
    for role, fence in FENCES.items():
        assert fence.role == role
        # capability is derived, so a fence cannot disagree with itself
        assert fence.can_write == bool({"Write", "Edit", "NotebookEdit"} & set(fence.tools))
        assert fence.has_shell == ("Bash" in fence.tools)


def test_checker_and_planner_roles_cannot_write():
    for role in ("planner", "critic", "judge"):
        assert not FENCES[role].can_write, f"{role} must not hold a write tool"


def test_executor_has_shell_but_no_write_and_maker_the_inverse():
    assert FENCES["executor"].has_shell and not FENCES["executor"].can_write
    assert FENCES["maker"].can_write and not FENCES["maker"].has_shell
    assert not FENCES["maker"].has_network


def test_fence_table_covers_exactly_the_role_literal():
    assert set(FENCES) == set(get_args(GraphRole))
    assert set(ROSTER_NAME) == set(get_args(GraphRole))


def test_distinct_grant_count_is_measured_not_asserted():
    # 6 roles over 4 distinct grants: read-only (planner/judge),
    # read+web (researcher/critic), write (maker), shell (executor).
    assert roles.distinct_grants() == 4


def test_unknown_role_raises():
    with pytest.raises(FenceViolation, match="unknown role"):
        fence_for("maker-shell")


def test_roster_mapping_members_fit_inside_their_fence():
    """The mapped roster member's grant must sit INSIDE the role fence — a
    roster member may hold fewer tools than the fence allows, never more."""
    for role, name in ROSTER_NAME.items():
        if name is None:
            continue
        assert name in ROSTER, f"{role} maps to unknown roster member {name!r}"
        assert set(ROSTER[name].allowed_tools) <= set(FENCES[role].tools), (
            f"roster member {name!r} holds tools outside the {role} fence"
        )


def test_roster_name_is_identity_since_the_consolidation():
    """The roster IS the six roles, so the mapping is identity —
    including executor, whose shell-holding roster member now exists with
    exactly the fence grant."""
    assert ROSTER_NAME == {role: role for role in FENCES}


# ---------------------------------------------------------------------------
# Levers — defaults restrictive, typo is not a decision, one read point
# ---------------------------------------------------------------------------


def test_lever_defaults_are_the_restrictive_posture(monkeypatch):
    for name in ("CHIMERA_GRAPH_WIDTH", "CHIMERA_GRAPH_PHASES",
                 "CHIMERA_GRAPH_CALL_BUDGET", "CHIMERA_GRAPH_REPAIR_LAPS"):
        monkeypatch.delenv(name, raising=False)
    lv = graph_levers()
    assert (lv.width_max, lv.phases_max, lv.call_budget, lv.repair_laps) == (3, 5, 40, 1)


@pytest.mark.parametrize("bad", ["five", "3.0", "-1", "", " 4", "4 ", "+4", "0x4"])
def test_a_typo_is_not_a_decision(monkeypatch, bad):
    monkeypatch.setenv("CHIMERA_GRAPH_WIDTH", bad)
    assert graph_levers().width_max == 3


def test_out_of_range_reads_as_unset(monkeypatch):
    monkeypatch.setenv("CHIMERA_GRAPH_WIDTH", "99")  # past the hard cap of 8
    assert graph_levers().width_max == 3
    monkeypatch.setenv("CHIMERA_GRAPH_WIDTH", "0")  # below the floor
    assert graph_levers().width_max == 3


def test_a_deliberate_widening_is_honored(monkeypatch):
    monkeypatch.setenv("CHIMERA_GRAPH_WIDTH", "6")
    monkeypatch.setenv("CHIMERA_GRAPH_REPAIR_LAPS", "2")
    lv = graph_levers()
    assert lv.width_max == 6
    assert lv.repair_laps == 2


def test_hard_caps_hold_regardless_of_lever():
    assert levers.GRAPH_WIDTH_HARD_MAX == 8
    assert levers.GRAPH_CALL_BUDGET_HARD_MAX == 250  # the runner ceiling


def test_graph_module_reads_no_environment():
    """graph.py takes levers as data; the environment is read in levers.py
    only. A direct env read in graph.py would be a second, unreviewed lever
    surface."""
    import inspect

    source = inspect.getsource(graph)
    for pattern in ("environ.get", "environ[", "getenv("):
        assert pattern not in source, f"graph.py reads the environment: {pattern}"


def test_no_generic_bypass_field_on_levers():
    """A generically named override (bypass/skip/disable) is banned by
    design — each lever must name its blast radius."""
    banned = {"bypass", "skip", "disable", "override", "unsafe"}
    for field in GraphLevers.model_fields:
        assert not (banned & set(field.lower().split("_"))), field


# ---------------------------------------------------------------------------
# Admission — every refusal is loud and names its lever
# ---------------------------------------------------------------------------


def test_the_modest_diamond_admits_unchanged():
    plan = _diamond()
    assert admit(plan, DEFAULTS) is plan  # never silently narrowed


def test_width_refusal_names_the_lever():
    plan = _plan([{"name": "wide", "nodes": [_node(f"n-{i}") for i in range(4)]},
                  {"name": "merge", "nodes": [_node("m", role="judge", reads=[f"n-{i}" for i in range(4)])]}])
    with pytest.raises(GraphAdmissionError, match="CHIMERA_GRAPH_WIDTH"):
        admit(plan, DEFAULTS)
    # the same plan admits once the operator widens deliberately
    admit(plan, GraphLevers(width_max=4, phases_max=5, call_budget=40, repair_laps=1))


def test_phase_refusal_names_the_lever():
    phases = [{"name": f"p-{i}", "nodes": [_node(f"n-{i}", role="maker", tier="frontier")]} for i in range(6)]
    with pytest.raises(GraphAdmissionError, match="CHIMERA_GRAPH_PHASES"):
        admit(_plan(phases), DEFAULTS)


def test_budget_refusal_names_the_lever():
    plan = _diamond()
    tight = GraphLevers(width_max=3, phases_max=5, call_budget=5, repair_laps=1)
    with pytest.raises(GraphAdmissionError, match="CHIMERA_GRAPH_CALL_BUDGET"):
        admit(plan, tight)


def test_estimated_calls_is_the_documented_formula():
    plan = _diamond()  # 3 nodes
    # plan(1) + replan allowance(1) + nodes(3) + wrap(1) + panel(3) + 1 lap*(1+3)
    assert estimated_calls(plan, repair_laps=1) == 13
    assert estimated_calls(plan, repair_laps=0) == 9


def test_unknown_read_is_refused():
    plan = _plan([
        {"name": "gather", "nodes": [_node("gather-a")]},
        {"name": "merge", "nodes": [_node("m", role="judge", reads=["ghost-node"])]},
    ])
    with pytest.raises(GraphAdmissionError, match="unknown node 'ghost-node'"):
        admit(plan, DEFAULTS)


def test_same_phase_read_is_refused_cycles_are_unrepresentable():
    plan = _plan([
        {"name": "one", "nodes": [_node("a", role="maker", tier="frontier"),
                                    _node("b", role="critic", reads=["a"])]},
    ])
    with pytest.raises(GraphAdmissionError, match="strictly earlier"):
        admit(plan, DEFAULTS)


def test_later_phase_read_is_refused():
    plan = _plan([
        {"name": "one", "nodes": [_node("early", role="critic", reads=["late"])]},
        {"name": "two", "nodes": [_node("late", role="maker", tier="frontier")]},
    ])
    with pytest.raises(GraphAdmissionError, match="strictly earlier"):
        admit(plan, DEFAULTS)


def test_checker_reading_nothing_is_dead_weight():
    plan = _plan([
        {"name": "one", "nodes": [_node("a")]},
        {"name": "two", "nodes": [_node("idle-critic", role="critic")]},
    ])
    with pytest.raises(GraphAdmissionError, match="reads nothing"):
        admit(plan, DEFAULTS)


def test_fan_out_without_fan_in_is_refused():
    """Silent duplication gets a structural home: two parallel producers with
    no later node reading BOTH means disagreements land unexamined."""
    plan = _plan([
        {"name": "gather", "nodes": [_node("gather-a"), _node("gather-b")]},
        {"name": "half", "nodes": [_node("m", role="judge", reads=["gather-a"])]},
    ])
    with pytest.raises(GraphAdmissionError, match="no later node reads all"):
        admit(plan, DEFAULTS)


def test_single_producer_needs_no_fan_in():
    plan = _plan([
        {"name": "gather", "nodes": [_node("solo")]},
        {"name": "check", "nodes": [_node("c", role="critic", reads=["solo"])]},
    ])
    admit(plan, DEFAULTS)


def test_mixed_tier_producer_reads_refused_maker_neq_checker_must_derive():
    plan = _plan([
        {"name": "make", "nodes": [_node("fast-one", role="maker", tier="fast"),
                                     _node("deep-one", role="maker", tier="frontier")]},
        {"name": "check", "nodes": [_node("j", role="judge", reads=["fast-one", "deep-one"])]},
    ])
    with pytest.raises(GraphAdmissionError, match="both tiers"):
        admit(plan, DEFAULTS)


# ---------------------------------------------------------------------------
# Model derivation — maker ≠ checker by construction, judging never downgraded
# ---------------------------------------------------------------------------


def test_worker_models_follow_the_tier_dial():
    plan = _diamond()
    gather = plan.phases[0].nodes[0]
    assert node_model(plan, gather) == resolve_models().research
    deep = GraphNode(id="deep", role="maker", tier="frontier", brief="x")
    assert node_model(_plan([{"name": "p", "nodes": [deep]}]), deep) == resolve_models().maker


def test_checker_model_is_distinct_from_the_producer_it_reads():
    plan = _diamond()  # fast gathers
    judge = plan.phases[1].nodes[0]
    checker = node_model(plan, judge)
    assert checker != resolve_models().research  # distinct from what it judges

    frontier_plan = _plan([
        {"name": "make", "nodes": [_node("deep", role="maker", tier="frontier")]},
        {"name": "check", "nodes": [_node("c", role="critic", reads=["deep"])]},
    ])
    critic = frontier_plan.phases[1].nodes[0]
    assert node_model(frontier_plan, critic) != resolve_models().maker


def test_checker_with_no_producer_reads_runs_the_judge_tier():
    """A judge merging critic verdicts judges judgments — it runs the JUDGE
    tier (default = the maker alias; CHIMERA_JUDGE_MODEL raises it alone),
    because the fan-in is the one place a cheap tier costs the most."""
    plan = _plan([
        {"name": "make", "nodes": [_node("m", role="maker", tier="frontier")]},
        {"name": "check", "nodes": [_node("c1", role="critic", reads=["m"]),
                                      _node("c2", role="critic", reads=["m"])]},
        {"name": "merge", "nodes": [_node("j", role="judge", reads=["c1", "c2"])]},
    ])
    judge = plan.phases[2].nodes[0]
    # NOT `== resolve_models().judge` — that holds for ANY lever value and so
    # can never fail (audit R-4). Assert the property that actually matters:
    # the judge runs the judge tier AND, under the default posture, that tier
    # is distinct from the critics it merges.
    models = resolve_models()
    assert node_model(plan, judge) == models.judge
    assert models.judge != models.critic, (
        "default posture must not put the fan-in judge on the critic tier"
    )
    assert node_model(plan, judge) != node_model(plan, plan.phases[1].nodes[0])


def test_checker_roles_constant_matches_the_fence_read_only_posture():
    for role in GRAPH_CHECKER_ROLES:
        assert not FENCES[role].can_write


# ---------------------------------------------------------------------------
# The operator's G1 shape pick — you decide, the framework only recommends,
# and admission enforces the decision (rev-2 design, 2026-08-28)
# ---------------------------------------------------------------------------


def test_pinned_straight_refuses_a_fan_out():
    with pytest.raises(GraphAdmissionError, match="pinned shape 'straight'"):
        admit(_diamond(), DEFAULTS, shape="straight")


def test_pinned_diamond_refuses_a_single_lane():
    lane = _plan([
        {"name": "make", "nodes": [_node("m", role="maker", tier="frontier")]},
        {"name": "check", "nodes": [_node("c", role="critic", reads=["m"])]},
    ])
    with pytest.raises(GraphAdmissionError, match="pinned shape 'diamond'"):
        admit(lane, DEFAULTS, shape="diamond")


def test_pinned_shape_admits_a_conforming_plan():
    admit(_diamond(), DEFAULTS, shape="diamond")
    lane = _plan([
        {"name": "make", "nodes": [_node("m", role="maker", tier="frontier")]},
        {"name": "check", "nodes": [_node("c", role="critic", reads=["m"])]},
    ])
    admit(lane, DEFAULTS, shape="straight")


def test_no_pick_admits_either_shape():
    admit(_diamond(), DEFAULTS)


def test_unknown_shape_pick_refuses_instead_of_unpinning():
    """A garbled pick must not silently FREE the planner — that's the
    loosening direction, the opposite of the lever rule (audit OP-7)."""
    with pytest.raises(GraphAdmissionError, match="unknown shape pick 'wide'"):
        admit(_diamond(), DEFAULTS, shape="wide")
    with pytest.raises(GraphAdmissionError, match="unknown shape pick"):
        admit(_diamond(), DEFAULTS, shape="STRAIGHT")
    with pytest.raises(GraphAdmissionError, match="unknown shape pick"):
        admit(_diamond(), DEFAULTS, shape="")


def test_lever_hard_caps_live_on_the_type_not_only_the_parser():
    """A GraphLevers built in code cannot exceed what the env can grant
    (audit OP-6)."""
    with pytest.raises(ValidationError):
        GraphLevers(width_max=999, phases_max=5, call_budget=40, repair_laps=1)
    with pytest.raises(ValidationError):
        GraphLevers(width_max=3, phases_max=999, call_budget=40, repair_laps=1)
    with pytest.raises(ValidationError):
        GraphLevers(width_max=3, phases_max=5, call_budget=99999, repair_laps=1)
    with pytest.raises(ValidationError):
        GraphLevers(width_max=3, phases_max=5, call_budget=40, repair_laps=99)


def test_node_model_refuses_an_unadmitted_mixed_tier_plan():
    """Reaching node_model with mixed-tier producer reads means admission was
    bypassed — a domain error, not a bare tuple-unpack ValueError (audit OP-9)."""
    from tests.arc_drivers import _gr_plan_payload
    from chimera.models import GraphPlan as _GP

    payload = _gr_plan_payload()
    payload["phases"][0]["nodes"][0]["tier"] = "frontier"  # gather-a; gather-b stays fast
    plan = _GP.model_validate(payload)
    judge = plan.phases[1].nodes[0]
    with pytest.raises(GraphAdmissionError, match="mixed tiers"):
        node_model(plan, judge)

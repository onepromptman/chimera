"""maker≠checker and repair-lap hardening (adversarial round, 2026-08-28).

Six defects the v7.1 clearance round left open, each with the drill that
proves it stays closed:

  A  a `planner` node inside a plan runs the maker tier while sitting outside
     the producer set, so any checker reading it derives the same model
  B  the shape pin was re-checked against the same hand-editable file that
     carries the plan, so nulling it defeated the check
  C  models resolve at CALL time and nothing recorded what actually ran, so
     lever drift between two ticks collapsed the pair in the transcript
  E  a repair lap deleted maker content that sibling nodes had already been
     judged against, leaving their verdicts standing
  F  load-time re-admission (OP-8) was only ever tested on the reads axis
  G  AgentDef — the grant that actually installs — had no compound-grant rule
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera import graph
from chimera.arcs.graph import GraphArc, GraphArcError
from chimera.levers import graph_levers
from chimera.models import (
    GraphNode,
    GraphPhase,
    GraphPlan,
    TaskRecord,
    TaskSpec,
    Transition,
)

PAUSE = "PAUSE — SURFACE TO OPERATOR"


def _out(node_id: str, recommendation: str = "PROCEED", output: str = "ok") -> dict:
    return {"node_id": node_id, "output": output, "sources": ["s.md:1"],
            "confidence": 82, "recommendation": recommendation}


def _pair(producer_role: str, checker_role: str = "critic") -> GraphPlan:
    return GraphPlan(goal="g", rationale="r", phases=[
        GraphPhase(name="p1", nodes=[
            GraphNode(id="prod", role=producer_role, brief="b")]),
        GraphPhase(name="p2", nodes=[
            GraphNode(id="chk", role=checker_role, brief="b", reads=["prod"])]),
    ])


def _arc_with_record(tmp_path: Path, shape: str | None):
    task_dir = tmp_path / "task"
    task_dir.mkdir(parents=True, exist_ok=True)
    spec = TaskSpec(id="20260828-harden", slug="harden", ask="a", arc="graph",
                    shape=shape)
    record = TaskRecord(spec=spec, state="running",
                        history=[Transition(from_state=None, to_state="ready", by="t")])
    (task_dir / "task.json").write_text(record.model_dump_json(indent=2),
                                        encoding="utf-8")
    arc = GraphArc(task_dir)
    return arc, arc.initialize(spec)


# --- A: planner nodes -------------------------------------------------------

def test_a_planner_node_is_refused_at_admission():
    with pytest.raises(graph.GraphAdmissionError, match="role 'planner'"):
        graph.admit(_pair("planner"), graph_levers())


def test_a_planner_refusal_also_holds_at_load_time():
    """_check_structure is environment-free precisely so it re-runs at load;
    the planner rule must therefore be inside it, not only in admit()."""
    with pytest.raises(graph.GraphAdmissionError, match="role 'planner'"):
        graph.check_admitted(_pair("planner"))


@pytest.mark.parametrize("producer", ["planner", "researcher", "maker", "executor"])
@pytest.mark.parametrize("checker", ["critic", "judge"])
def test_a_no_producer_role_collapses_onto_its_checker(monkeypatch, producer, checker):
    """The deny-list, stated as the invariant it exists for: for EVERY
    non-checker role — including ones added to GraphRole later — a checker
    reading it derives a different model. An allow-list satisfied this only
    for the roles someone remembered to list."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "sonnet")
    plan = _pair(producer, checker)
    assert graph.node_model(plan, plan.phases[0].nodes[0]) != graph.node_model(
        plan, plan.phases[1].nodes[0]
    ), f"{producer} -> {checker} resolves to one model"


# --- B / F: shape authority and the OP-8 shape axis -------------------------

_WIDE = {
    "goal": "g", "rationale": "r",
    "phases": [
        {"name": "make", "nodes": [
            {"id": "m1", "role": "maker", "brief": "b"},
            {"id": "m2", "role": "maker", "brief": "b"}]},
        {"name": "judge", "nodes": [
            {"id": "jd", "role": "judge", "brief": "b", "reads": ["m1", "m2"]}]},
    ],
}


def test_b_nulling_the_shape_in_arc_state_cannot_unpin_the_operators_pick(tmp_path):
    """The bypass: arc-state.json carried BOTH the plan and the shape it was
    checked against, so an edit that widened the plan and nulled the shape
    reloaded clean. The G1 task record is the authority."""
    arc, state = _arc_with_record(tmp_path, "straight")
    state.shape = None                       # the tamper
    state.plan = GraphPlan.model_validate(_WIDE)
    arc.save(state)
    with pytest.raises(GraphArcError, match="G1 task record"):
        arc.load()


def test_f_op8_reload_enforces_the_shape_axis_not_only_reads(tmp_path):
    """OP-8's only drill covered forward reads. A plan that violates the
    SHAPE must fail the same load-time gate."""
    arc, state = _arc_with_record(tmp_path, "straight")
    state.plan = GraphPlan.model_validate(_WIDE)   # a diamond under a straight pin
    arc.save(state)
    with pytest.raises(GraphArcError, match="admission structure"):
        arc.load()


def test_f_a_conforming_plan_still_reloads_under_its_pick(tmp_path):
    """The gate refuses violations without refusing honest work."""
    arc, state = _arc_with_record(tmp_path, "diamond")
    state.plan = GraphPlan.model_validate(_WIDE)
    arc.save(state)
    assert arc.load().plan is not None


# --- C: the model that actually ran -----------------------------------------

def test_c_dispatch_records_the_model_each_node_actually_ran_on(tmp_path):
    arc, state = _arc_with_record(tmp_path, None)
    state = arc.submit(state, "plan", {
        "goal": "g", "rationale": "r",
        "phases": [{"name": "make", "nodes": [
            {"id": "impl", "role": "maker", "brief": "b"}]},
            {"name": "check", "nodes": [
                {"id": "crit", "role": "critic", "brief": "b", "reads": ["impl"]}]}],
    })
    arc.pending_calls(state)
    assert state.node_models["impl"], "the maker's dispatched model was not recorded"


def test_c_lever_drift_between_ticks_cannot_collapse_the_pair(tmp_path, monkeypatch):
    """The maker runs on model-a. The operator then SWAPS the tiers. Deriving
    the critic against the CURRENT levers sees producer=model-b, picks the
    critic tier model-a — and dispatches the critic on the very model the
    maker actually ran on. Distinct in every snapshot, identical in the
    transcript. Deriving against the dispatch record instead means the pair
    either stays distinct or refuses LOUDLY; what it may never do is quietly
    hand the checker the producer's model."""
    from chimera.agents import MakerCheckerViolation

    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "model-a")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "model-b")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "model-b")
    arc, state = _arc_with_record(tmp_path, None)
    state = arc.submit(state, "plan", {
        "goal": "g", "rationale": "r",
        "phases": [{"name": "make", "nodes": [
            {"id": "impl", "role": "maker", "brief": "b"}]},
            {"name": "check", "nodes": [
                {"id": "crit", "role": "critic", "brief": "b", "reads": ["impl"]}]}],
    })
    arc.pending_calls(state)                       # tick 1: maker dispatched
    ran_on = state.node_models["impl"]
    assert ran_on == "model-a"
    state = arc.submit(state, "node:impl", _out("impl"))

    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "model-b")   # the swap
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "model-a")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "model-a")
    try:
        call = [c for c in arc.pending_calls(state) if c.label == "node:crit"][0]
    except MakerCheckerViolation:
        return                                     # refused loudly — honest
    assert call.model != ran_on, (
        f"critic dispatched on {call.model!r}, the model the maker actually ran on"
    )


# --- E: a repair lap invalidates what it invalidated ------------------------

def test_e_repair_invalidates_a_sibling_that_already_judged_the_old_content(tmp_path):
    """Two executors read one maker. The first lands PROCEED. The second
    PAUSEs, so the maker is re-run — and the first executor's approval now
    refers to content that no longer exists. It must be re-run, not kept."""
    arc, state = _arc_with_record(tmp_path, None)
    state = arc.submit(state, "plan", {
        "goal": "g", "rationale": "r",
        "phases": [
            {"name": "make", "nodes": [{"id": "impl", "role": "maker", "brief": "b"}]},
            {"name": "test", "nodes": [
                {"id": "e1", "role": "executor", "brief": "b", "reads": ["impl"]},
                {"id": "e2", "role": "executor", "brief": "b", "reads": ["impl"]}]},
        ],
    })
    state = arc.submit(state, "node:impl", _out("impl"))
    state = arc.submit(state, "node:e1", _out("e1"))            # PROCEED, lands
    assert state.outputs["e1"] is not None
    state = arc.submit(state, "node:e2", _out("e2", recommendation=PAUSE))

    assert "impl" not in state.outputs, "the maker under repair is still landed"
    assert "e1" not in state.outputs, (
        "e1's PROCEED still stands against maker content that was deleted"
    )
    assert state.repair_queue == ["impl", "e1", "e2"]
    assert any("invalidated=e1" in line for line in state.log)


def test_e_an_unrelated_sibling_is_not_invalidated(tmp_path):
    """The blast radius is the read closure, not the phase: a node that never
    read the repaired maker keeps its result."""
    arc, state = _arc_with_record(tmp_path, None)
    state = arc.submit(state, "plan", {
        "goal": "g", "rationale": "r",
        "phases": [
            {"name": "make", "nodes": [
                {"id": "m1", "role": "maker", "brief": "b"},
                {"id": "m2", "role": "maker", "brief": "b"},
                {"id": "syn", "role": "maker", "brief": "b"}]},
            {"name": "test", "nodes": [
                {"id": "e1", "role": "executor", "brief": "b", "reads": ["m1"]},
                {"id": "e2", "role": "executor", "brief": "b", "reads": ["m2"]}]},
            {"name": "fan", "nodes": [
                {"id": "jd", "role": "judge", "brief": "b",
                 "reads": ["m1", "m2", "syn"]}]},
        ],
    })
    for nid in ("m1", "m2", "syn"):
        state = arc.submit(state, f"node:{nid}", _out(nid))
    state = arc.submit(state, "node:e2", _out("e2"))            # reads m2 only
    state = arc.submit(state, "node:e1", _out("e1", recommendation=PAUSE))

    assert "m1" not in state.outputs                            # repaired
    assert state.outputs.get("m2") is not None, "m2 was never in the read closure"
    assert state.outputs.get("e2") is not None, "e2 never read the repaired maker"
    assert state.outputs.get("syn") is not None


# --- G: the grant that actually installs ------------------------------------

def test_g_agentdef_refuses_a_compound_write_network_grant():
    from pydantic import ValidationError

    from chimera.agents import AgentDef

    # pydantic surfaces the validator's FenceViolation as a ValidationError;
    # the point is the compound cannot be built at all (same idiom as the
    # RoleFence drills in test_graph_admission.py)
    with pytest.raises(ValidationError, match="write AND network"):
        AgentDef(name="researcher", system_prompt="p", model="m",
                 allowed_tools=["Read", "Write", "WebFetch"])


def test_g_agentdef_refuses_a_compound_write_shell_grant():
    from pydantic import ValidationError

    from chimera.agents import AgentDef

    with pytest.raises(ValidationError, match="write AND shell"):
        AgentDef(name="maker", system_prompt="p", model="m",
                 allowed_tools=["Read", "Write", "Bash"])


def test_g_an_operator_mcp_grant_cannot_smuggle_write_into_a_network_role(monkeypatch):
    """internal_roles() widens researcher/critic with operator-granted MCP
    tools and it is THAT widened list which is rendered into the installed
    .md files. The widened grant must go through the compound rule."""
    from pydantic import ValidationError

    from chimera import agents

    monkeypatch.setattr(agents, "research_mcp_tools", lambda: ["mcp__notion__search"])
    roles = agents.internal_roles()
    for name in ("researcher", "critic"):
        tools = roles[name].allowed_tools
        assert "mcp__notion__search" in tools, "operator MCP grant did not land"
        # that grant carries network; a write tool on top is unconstructible —
        # and it is AgentDef, not RoleFence, that has to say so
        with pytest.raises(ValidationError, match="write AND network"):
            agents.AgentDef(name=name, system_prompt="p", model="m",
                            allowed_tools=[*tools, "Write"])


# --- R-2: an impossible posture refuses at PLAN time, not mid-run -----------

def test_r2_impossible_posture_refuses_at_admission_not_at_the_checker(monkeypatch):
    """MAKER == CRITIC means no model is distinct from the producer. Counting
    distinct producer models does not notice: admission passed and the run
    died at the CHECKER's phase, after the maker's calls were already spent.
    Admission must run the real derivation and refuse into the re-plan lap."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "same-model")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "same-model")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "same-model")
    with pytest.raises(graph.GraphAdmissionError, match="no admissible model"):
        graph.admit(_pair("maker", "critic"), graph_levers())


def test_r2_a_workable_posture_still_admits(monkeypatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "model-a")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "model-b")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "model-b")
    assert graph.admit(_pair("maker", "critic"), graph_levers()) is not None


# --- R-4: read-less judge on the critic tier warns, never blocks ------------

def _diamond() -> GraphPlan:
    return GraphPlan(goal="g", rationale="r", phases=[
        GraphPhase(name="make", nodes=[GraphNode(id="mk", role="maker", brief="b")]),
        GraphPhase(name="check", nodes=[
            GraphNode(id="c1", role="critic", brief="b", reads=["mk"]),
            GraphNode(id="c2", role="critic", brief="b", reads=["mk"])]),
        GraphPhase(name="merge", nodes=[
            GraphNode(id="jd", role="judge", brief="b", reads=["c1", "c2"])]),
    ])


def test_r4_readless_judge_on_the_critic_tier_is_flagged(monkeypatch):
    """Operator ruling (2026-08-28): WARN, do not block. A read-less judge
    merges critic verdicts, so pointing CHIMERA_JUDGE_MODEL at the critic
    tier has it adjudicate the critics on the critics' own model."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_JUDGE_MODEL", "sonnet")
    warning = graph.judge_tier_warning(_diamond())
    assert warning is not None and "jd" in warning
    assert "JUDGE_TIER_SHARES_CRITIC_MODEL" in warning


def test_r4_a_distinct_judge_tier_is_not_flagged(monkeypatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_JUDGE_MODEL", "opus")
    assert graph.judge_tier_warning(_diamond()) is None


def test_r4_a_judge_that_reads_a_producer_is_not_flagged(monkeypatch):
    """The warning is scoped to READ-LESS checkers: a judge reading a maker
    derives distinct-by-construction and is not the R-4 shape."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_JUDGE_MODEL", "sonnet")
    assert graph.judge_tier_warning(_pair("maker", "judge")) is None


def test_r4_the_warning_rides_the_run_and_does_not_block_it(tmp_path, monkeypatch):
    """The whole point of the ruling: the plan is ADMITTED and the flag lands
    in the log where the digest picks it up."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_JUDGE_MODEL", "sonnet")
    arc, state = _arc_with_record(tmp_path, None)
    state = arc.submit(state, "plan", _diamond().model_dump())
    assert state.stage == "run", "the warning must not block admission"
    assert any("JUDGE_TIER_SHARES_CRITIC_MODEL" in line for line in state.log)

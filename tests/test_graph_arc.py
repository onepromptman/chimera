"""Graph arc state machine — everything the cross-arc parity suites don't
already hold: the bounded re-plan lap, phase barriers, degraded-node flow,
the input-set invariant on checker prompts, and the lever wiring.

(Null tolerance at verify, primary-null halting, timeout expiry, priors
consumption, schema-gate state-safety, and the verify repair lap are covered
by the parametrized parity suites via tests/arc_drivers.py — this file does
not repeat them.)"""

from __future__ import annotations

import json

import pytest

from chimera import graph
from chimera.arcs.graph import GraphArc, GraphArcError
from chimera.verify.schema_gate import SchemaGateError
from tests.arc_drivers import (
    _gr_node_payload,
    _gr_plan_payload,
    _gr_wrap_payload,
    graph_fresh,
    graph_to_verify,
    valid_opinion,
)

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_end_to_end(tmp_path):
    arc, state, task_dir = graph_to_verify(tmp_path)
    assert state.phase == "verify"
    for i in (1, 2, 3):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=False), kind="null")
    assert state.phase == "complete"
    assert arc.verify_verdict(state).passed is True
    rendered = (task_dir / "artifacts" / "graph-output.md").read_text(encoding="utf-8")
    assert "arc: graph" in rendered and "phases: 2" in rendered


def test_plan_is_persisted_before_any_node_runs(tmp_path):
    """Auditability: the admitted plan is durable state the moment it lands —
    a resumed session re-reads the same shape it committed to."""
    arc, state, _ = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    assert state.stage == "run"
    reloaded = arc.load()
    assert reloaded.plan is not None
    assert [p.name for p in reloaded.plan.phases] == ["gather", "merge"]
    assert any("PLAN_ADMITTED" in line for line in reloaded.log)


# ---------------------------------------------------------------------------
# The bounded re-plan lap (admission refusal is a loop, not a death)
# ---------------------------------------------------------------------------


def _wide_plan_payload() -> dict:
    plan = _gr_plan_payload()
    plan["phases"][0]["nodes"] = [
        {"id": f"gather-{s}", "role": "researcher", "tier": "fast",
         "brief": f"angle {s}", "reads": []}
        for s in ("a", "b", "c", "d")  # width 4 > default 3
    ]
    plan["phases"][1]["nodes"][0]["reads"] = [f"gather-{s}" for s in ("a", "b", "c", "d")]
    return plan


def test_admission_refusal_feeds_one_replan_lap(tmp_path):
    arc, state, _ = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _wide_plan_payload(), kind="null")
    assert state.stage == "plan"  # not halted — one lap owed
    assert state.plan_repairs == 1
    assert state.plan_brief and "CHIMERA_GRAPH_WIDTH" in state.plan_brief
    # the re-issued plan call carries the refusal
    calls = arc.pending_calls(state)
    assert len(calls) == 1 and calls[0].label == "plan"
    assert "REFUSED AT ADMISSION" in calls[0].prompt
    assert "CHIMERA_GRAPH_WIDTH" in calls[0].prompt


def test_second_refusal_halts(tmp_path):
    arc, state, _ = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _wide_plan_payload(), kind="null")
    state = arc.submit(state, "plan", _wide_plan_payload(), kind="null")
    assert state.phase == "failed"
    assert "refused at admission" in (state.failure or "")


def test_admitted_replan_clears_the_brief(tmp_path):
    arc, state, _ = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _wide_plan_payload(), kind="null")
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    assert state.stage == "run"
    assert state.plan_brief is None


def test_widened_lever_admits_the_same_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_GRAPH_WIDTH", "4")
    arc, state, _ = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _wide_plan_payload(), kind="null")
    assert state.stage == "run"


def test_malformed_plan_raises_and_state_survives(tmp_path):
    arc, state, _ = graph_fresh(tmp_path)
    with pytest.raises(Exception):  # pydantic ValidationError surfaces pre-gate
        arc.submit(state, "plan", {"goal": "x", "bogus": True}, kind="null")
    assert state.stage == "plan"
    assert state.plan_repairs == 0  # a transport error is not a refusal


# ---------------------------------------------------------------------------
# Phase barriers + node routing
# ---------------------------------------------------------------------------


def _to_run(tmp_path):
    arc, state, task_dir = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    return arc, state, task_dir


def test_barrier_only_current_phase_nodes_pend(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    labels = {c.label for c in arc.pending_calls(state)}
    assert labels == {"node:gather-a", "node:gather-b"}
    state = arc.submit(state, "node:gather-a", _gr_node_payload("gather-a"), kind="null")
    labels = {c.label for c in arc.pending_calls(state)}
    assert labels == {"node:gather-b"}  # phase 2 stays behind the barrier
    state = arc.submit(state, "node:gather-b", _gr_node_payload("gather-b"), kind="null")
    labels = {c.label for c in arc.pending_calls(state)}
    assert labels == {"node:judge-gathers"}


def test_out_of_phase_submission_is_refused(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    with pytest.raises(GraphArcError, match="out of phase order"):
        arc.submit(state, "node:judge-gathers", _gr_node_payload("judge-gathers"), kind="null")


def test_unknown_node_is_refused(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    with pytest.raises(GraphArcError, match="unknown node id"):
        arc.submit(state, "node:ghost", _gr_node_payload("ghost"), kind="null")


def test_duplicate_submission_is_refused(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    state = arc.submit(state, "node:gather-a", _gr_node_payload("gather-a"), kind="null")
    with pytest.raises(GraphArcError, match="already submitted"):
        arc.submit(state, "node:gather-a", _gr_node_payload("gather-a"), kind="null")


def test_node_id_label_mismatch_is_refused(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    with pytest.raises(GraphArcError, match="mismatch"):
        arc.submit(state, "node:gather-a", _gr_node_payload("gather-b"), kind="null")


def test_malformed_node_payload_routes_through_schema_gate(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    bad = _gr_node_payload("gather-a")
    bad["bogus_extra_field"] = True
    with pytest.raises(Exception):
        arc.submit(state, "node:gather-a", bad, kind="null")
    # state-safety: the same label accepts a valid resubmission
    state = arc.submit(state, "node:gather-a", _gr_node_payload("gather-a"), kind="null")
    assert "gather-a" in state.outputs


# ---------------------------------------------------------------------------
# Degraded nodes — a lost gather is fewer candidates, never a wedge
# ---------------------------------------------------------------------------


def test_degraded_node_slots_none_and_the_run_continues(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    state = arc.submit(state, "node:gather-a", None, kind="null")
    assert state.outputs["gather-a"] is None
    assert state.stage == "run"
    state = arc.submit(state, "node:gather-b", _gr_node_payload("gather-b"), kind="null")
    # barrier crossed with one degraded member
    assert {c.label for c in arc.pending_calls(state)} == {"node:judge-gathers"}


def test_degraded_upstream_is_marked_never_invented(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    state = arc.submit(state, "node:gather-a", None, kind="null")
    state = arc.submit(state, "node:gather-b", _gr_node_payload("gather-b"), kind="null")
    judge_call = arc.pending_calls(state)[0]
    assert "node degraded" in judge_call.prompt
    assert "finding from gather-b" in judge_call.prompt


def test_fully_degraded_run_still_reaches_wrap_and_verify_gates_it(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    for label in ("node:gather-a", "node:gather-b", "node:judge-gathers"):
        state = arc.submit(state, label, None, kind="null")
    assert state.stage == "wrap"
    wrap_call = arc.pending_calls(state)[0]
    assert wrap_call.prompt.count("node degraded") == 3


# ---------------------------------------------------------------------------
# The input-set invariant — a checker sees {ask, rubric, artifacts}, nothing else
# ---------------------------------------------------------------------------


def test_checker_prompt_is_exactly_ask_rubric_artifacts(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    state = arc.submit(state, "node:gather-a", _gr_node_payload("gather-a"), kind="null")
    state = arc.submit(state, "node:gather-b", _gr_node_payload("gather-b"), kind="null")
    judge_call = arc.pending_calls(state)[0]
    # built by checker_brief and only checker_brief
    expected = graph.checker_brief(
        state.ask,
        "score both gathers; declare a winner and grafts",
        {"gather-a": "finding from gather-a", "gather-b": "finding from gather-b"},
        "judge-gathers",
    )
    assert judge_call.prompt == expected
    # the output contract is IN the prompt — a checker never told it answers
    # in prose, fails the schema gate, and degrades to --null (audit OP-1)
    assert "GraphNodeOutput" in judge_call.prompt
    assert "'judge-gathers'" in judge_call.prompt
    # the ask and the artifacts are in; the maker context is not reachable:
    # the plan's rationale is the closest thing to maker reasoning in state,
    # and it must never leak into a checker prompt.
    assert state.ask in judge_call.prompt
    assert state.plan is not None
    assert state.plan.rationale not in judge_call.prompt


def test_checker_model_differs_from_the_gathers_it_judges(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    state = arc.submit(state, "node:gather-a", _gr_node_payload("gather-a"), kind="null")
    state = arc.submit(state, "node:gather-b", _gr_node_payload("gather-b"), kind="null")
    judge_call = arc.pending_calls(state)[0]
    gather_call_model = graph.node_model(state.plan, state.plan.phases[0].nodes[0])
    assert judge_call.model != gather_call_model


def test_worker_prompt_carries_goal_and_upstreams_flow_by_reads_only(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    calls = {c.label: c for c in arc.pending_calls(state)}
    a = calls["node:gather-a"]
    assert "answer the demo ask" in a.prompt  # the goal
    assert "investigate angle a" in a.prompt  # its own brief
    assert "investigate angle b" not in a.prompt  # siblings don't leak


# ---------------------------------------------------------------------------
# Repair-lap lever wiring (default lap is held by the parity suite)
# ---------------------------------------------------------------------------


def test_repair_laps_lever_grants_a_second_lap(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_GRAPH_REPAIR_LAPS", "2")
    arc, state, task_dir = graph_to_verify(tmp_path)
    for lap in (1, 2):
        for i in (1, 2, 3):
            state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=True), kind="null")
        if lap < 2:
            assert state.phase == "wrap"
            assert state.verify_repairs == lap
            state = arc.submit(state, "wrap", _gr_wrap_payload(), kind="null")
    assert state.phase == "wrap"  # second refutation still repairs
    assert state.verify_repairs == 2
    state = arc.submit(state, "wrap", _gr_wrap_payload(), kind="null")
    for i in (1, 2, 3):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=True), kind="null")
    assert state.phase == "failed"  # budget exhausted
    assert (task_dir / "verification.json").exists()


def test_repair_laps_zero_halts_on_first_genuine_refutation(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_GRAPH_REPAIR_LAPS", "0")
    arc, state, _ = graph_to_verify(tmp_path)
    for i in (1, 2, 3):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=True), kind="null")
    assert state.phase == "failed"
    assert state.verify_repairs == 0


# ---------------------------------------------------------------------------
# Budget disclosure — the planner is told the posture it must plan within
# ---------------------------------------------------------------------------


def test_plan_prompt_discloses_the_posture(tmp_path):
    arc, state, _ = graph_fresh(tmp_path)
    prompt = arc.pending_calls(state)[0].prompt
    assert "at most 3 nodes per phase, 5 phases" in prompt
    assert "call budget 40" in prompt
    assert "GraphPlan" in prompt


def test_state_round_trips_through_disk(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    state = arc.submit(state, "node:gather-a", None, kind="null")
    reloaded = arc.load()
    assert reloaded.outputs["gather-a"] is None
    assert reloaded.stage == "run"
    assert reloaded.phase_index == 0


# ---------------------------------------------------------------------------
# The operator's G1 shape pick, end to end through the arc
# ---------------------------------------------------------------------------


def _fresh_with_shape(tmp_path, shape):
    """Build the arc with a task record carrying the operator's pick — the
    arc reads the pick from task.json, the same path the CLI produces."""
    from chimera.models import TaskRecord, TaskSpec, Transition

    task_dir = tmp_path / "task"
    task_dir.mkdir(parents=True, exist_ok=True)
    spec = TaskSpec(id="20260828-shape-pick", slug="shape-pick",
                     ask="answer the demo ask", arc="graph", shape=shape)
    record = TaskRecord(
        spec=spec, state="running",
        history=[Transition(from_state=None, to_state="ready", by="t")],
    )
    (task_dir / "task.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    arc = GraphArc(task_dir)
    return arc, arc.initialize(spec)


def test_pinned_shape_reaches_the_planner_prompt(tmp_path):
    arc, state = _fresh_with_shape(tmp_path, "straight")
    assert state.shape == "straight"
    prompt = arc.pending_calls(state)[0].prompt
    assert "OPERATOR PINNED THE SHAPE AT G1: STRAIGHT" in prompt


def test_plan_that_ignores_the_pick_is_refused_into_a_replan_lap(tmp_path):
    arc, state = _fresh_with_shape(tmp_path, "straight")
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")  # a diamond
    assert state.stage == "plan"
    assert state.plan_repairs == 1
    assert "pinned shape 'straight'" in (state.plan_brief or "")


def test_conforming_plan_admits_under_the_pick(tmp_path):
    arc, state = _fresh_with_shape(tmp_path, "diamond")
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    assert state.stage == "run"


def test_no_record_means_no_pick_fail_open(tmp_path):
    arc, state, _ = graph_fresh(tmp_path)
    assert state.shape is None


# ---------------------------------------------------------------------------
# The 250-call runtime ceiling — re-enforced at the submit door (audit OP-4)
# ---------------------------------------------------------------------------


def test_call_ceiling_halts_the_run(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    state.audit.agent_calls_attempted = 250
    state = arc.submit(state, "node:gather-a", _gr_node_payload("gather-a"))
    assert state.stage == "halted"
    assert state.phase == "failed"
    assert "AGENT_CALL_CEILING" in state.failure


def test_call_ceiling_binds_on_the_null_path_too(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    state.audit.agent_calls_attempted = 250
    state = arc.submit(state, "node:gather-a", None, kind="null")
    assert state.stage == "halted"
    assert "AGENT_CALL_CEILING" in state.failure


def test_calls_below_the_ceiling_populate_by_label(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    state = arc.submit(state, "node:gather-a", _gr_node_payload("gather-a"))
    assert state.audit.by_label.get("node:gather-a") == 1


# ---------------------------------------------------------------------------
# Verify panel payload — truncation is marked, never silent (audit OP-12)
# ---------------------------------------------------------------------------


def test_verify_panel_truncation_is_marked_never_silent(tmp_path):
    from tests.arc_drivers import graph_to_verify

    arc, state, _ = graph_to_verify(tmp_path)
    state.artifact = state.artifact.model_copy(update={"body": "x" * 60_000})
    calls = arc.pending_calls(state)
    assert "artifact truncated for the verify panel" in calls[0].prompt


def test_verify_panel_sees_a_short_artifact_whole(tmp_path):
    from tests.arc_drivers import graph_to_verify

    arc, state, _ = graph_to_verify(tmp_path)
    calls = arc.pending_calls(state)
    assert "truncated" not in calls[0].prompt


# ---------------------------------------------------------------------------
# Structural re-admission at load (audit OP-8) + priors threading
# ---------------------------------------------------------------------------


def test_hand_widened_persisted_plan_is_refused_at_load(tmp_path):
    """A plan edited on disk to violate structure (a forward read) must fail
    at load — never resume and issue calls admission never saw."""
    import json as _json

    arc, state, _ = _to_run(tmp_path)
    arc.save(state)
    raw = _json.loads(arc.state_path.read_text(encoding="utf-8"))
    # make gather-a read the LATER judge node — structurally inadmissible
    raw["plan"]["phases"][0]["nodes"][0]["reads"] = ["judge-gathers"]
    arc.state_path.write_text(_json.dumps(raw), encoding="utf-8")
    with pytest.raises(GraphArcError, match="no longer passes admission structure"):
        arc.load()


def test_priors_seed_reaches_worker_nodes(tmp_path):
    """The L2 seed threads into worker briefs (it reached only the planner
    before — audit roadmap #10); checker prompts never carry it."""
    from chimera.arcs._common import PriorsSeed
    from tests.arc_drivers import _gr_node_payload as _np

    arc, state, _ = _to_run(tmp_path)
    state.priors = PriorsSeed(block="PRIORS: last run of this shape halted on X")
    calls = {c.label: c for c in arc.pending_calls(state)}
    assert "PRIORS: last run" in calls["node:gather-a"].prompt
    state = arc.submit(state, "node:gather-a", _np("gather-a"))
    state = arc.submit(state, "node:gather-b", _np("gather-b"))
    judge_call = arc.pending_calls(state)[0]
    assert "PRIORS: last run" not in judge_call.prompt  # input-set invariant

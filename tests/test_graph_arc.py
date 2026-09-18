"""Graph arc state machine — everything the cross-arc parity suites don't
already hold: the bounded re-plan lap, phase barriers, degraded-node flow,
the input-set invariant on checker prompts, and the lever wiring.

(Null tolerance at verify, primary-null halting, timeout expiry, priors
consumption, schema-gate state-safety, and the verify repair lap are covered
by the parametrized parity suites via tests/arc_drivers.py — this file does
not repeat them.)"""

from __future__ import annotations

import pytest

from chimera import graph
from chimera.arcs.graph import GraphArc, GraphArcError
from tests.arc_drivers import (
    _gr_node_payload,
    _gr_plan_payload,
    _gr_wrap_payload,
    fatal_opinion,
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
    assert {c.label for c in arc.pending_calls(state)} == {"verify:critic1", "verify:critic2"}
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=False), kind="null")
    assert state.phase == "complete"
    assert arc.verify_verdict(state).passed is True
    rendered = (task_dir / "artifacts" / "graph-output.md").read_text(encoding="utf-8")
    assert "arc: graph" in rendered and "phases: 2" in rendered


def test_a_material_refutation_survives_and_lands_as_an_open_finding(tmp_path):
    """2026-09-16: only `fatal` vetoes. A `material` refutation is real
    signal, but the run PASSES and the refutation surfaces on the digest as
    an open finding instead of forcing a repair lap or a halt."""
    arc, state, _ = graph_to_verify(tmp_path)
    state = arc.submit(state, "verify:critic1", valid_opinion(refuted=True), kind="null")
    state = arc.submit(state, "verify:critic2", valid_opinion(refuted=False), kind="null")
    assert state.phase == "complete"
    verdict = arc.verify_verdict(state)
    assert verdict.passed is True
    assert verdict.open_findings and "[material]" in verdict.open_findings[0]


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
        {
            "id": f"gather-{s}",
            "role": "researcher",
            "tier": "fast",
            "brief": f"angle {s}",
            "reads": [],
        }
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


def test_the_refused_plans_skeleton_rides_the_replan_prompt(tmp_path):
    """A re-plan used to be blind: only chimera's refusal string came back, so
    the planner re-derived a shape it had already emitted. It now sees what it
    sent — phases, node ids, roles — and can diff against it."""
    arc, state, _ = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _wide_plan_payload(), kind="null")
    prompt = arc.pending_calls(state)[0].prompt
    assert "THE PLAN YOU EMITTED:" in prompt
    assert "phase 1 'gather'" in prompt
    for node_id in ("gather-a", "gather-b", "gather-c", "gather-d"):
        assert f"{node_id}(researcher)" in prompt
    assert "judge-gathers(judge)" in prompt


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
    with pytest.raises(ValueError):  # SchemaGateError / pydantic ValidationError (both ValueError)
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
    with pytest.raises(ValueError):  # SchemaGateError is a ValueError
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


def test_per_call_briefs_name_their_schema_and_re_author_no_role_prelude(tmp_path):
    """The role prelude ships once, in the installed role file the call
    dispatches to (agents.ROSTER); a brief states only what is specific to the
    call and NAMES the schema it owes."""
    arc, state, _ = graph_fresh(tmp_path)
    plan_call = arc.pending_calls(state)[0]
    assert plan_call.subagent_type == "planner"
    assert "GraphPlan" in plan_call.prompt
    assert "You are chimera's graph planner" not in plan_call.prompt

    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    worker = next(c for c in arc.pending_calls(state) if c.label == "node:gather-a")
    assert "GraphNodeOutput" in worker.prompt and "'gather-a'" in worker.prompt
    assert "You are a chimera graph" not in worker.prompt

    for label in ("node:gather-a", "node:gather-b", "node:judge-gathers"):
        state = arc.submit(state, label, _gr_node_payload(label.split(":", 1)[1]), kind="null")
    wrap_call = arc.pending_calls(state)[0]
    assert wrap_call.subagent_type == "maker"
    assert "GraphArtifact" in wrap_call.prompt
    assert "You are chimera's graph wrap maker" not in wrap_call.prompt


# ---------------------------------------------------------------------------
# Repair-lap lever wiring (default lap is held by the parity suite)
# ---------------------------------------------------------------------------


def test_repair_laps_lever_grants_a_second_lap(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_GRAPH_REPAIR_LAPS", "2")
    arc, state, task_dir = graph_to_verify(tmp_path)
    for lap in (1, 2):
        for i in (1, 2):
            state = arc.submit(state, f"verify:critic{i}", fatal_opinion(), kind="null")
        if lap < 2:
            assert state.phase == "wrap"
            assert state.verify_repairs == lap
            state = arc.submit(state, "wrap", _gr_wrap_payload(), kind="null")
    assert state.phase == "wrap"  # second refutation still repairs
    assert state.verify_repairs == 2
    state = arc.submit(state, "wrap", _gr_wrap_payload(), kind="null")
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", fatal_opinion(), kind="null")
    assert state.phase == "failed"  # budget exhausted
    assert (task_dir / "verification.json").exists()


def test_repair_laps_zero_halts_on_first_genuine_refutation(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_GRAPH_REPAIR_LAPS", "0")
    arc, state, _ = graph_to_verify(tmp_path)
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", fatal_opinion(), kind="null")
    assert state.phase == "failed"
    assert state.verify_repairs == 0


# ---------------------------------------------------------------------------
# Posture disclosure — the planner is told the fixed machinery it plans
# alongside, honestly (no call-budget lever since 2026-09-16)
# ---------------------------------------------------------------------------


def test_plan_prompt_discloses_the_posture(tmp_path):
    arc, state, _ = graph_fresh(tmp_path)
    prompt = arc.pending_calls(state)[0].prompt
    assert "at most 3 nodes per phase, 5 phases" in prompt
    assert f"fixed machinery of {graph.overhead_calls(1)} calls" in prompt
    assert "2-critic" in prompt and "verify panel" in prompt
    assert "call budget" not in prompt
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
    spec = TaskSpec(
        id="20260828-shape-pick",
        slug="shape-pick",
        ask="answer the demo ask",
        arc="graph",
        shape=shape,
    )
    record = TaskRecord(
        spec=spec,
        state="running",
        history=[Transition(from_state=None, to_state="ready", by="t")],
    )
    (task_dir / "task.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    arc = GraphArc(task_dir)
    return arc, arc.start(spec.id, spec.slug, spec.ask, spec.context)


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
    from chimera.arcs.graph import VERIFY_PAYLOAD_MAX
    from tests.arc_drivers import graph_to_verify

    arc, state, _ = graph_to_verify(tmp_path)
    # model_copy skips validation on purpose: this exercises the fallback
    # behind the gate, which no gated artifact can reach
    state.artifact = state.artifact.model_copy(update={"body": "x" * (VERIFY_PAYLOAD_MAX + 1_000)})
    calls = arc.pending_calls(state)
    assert "artifact truncated for the verify panel" in calls[0].prompt


def test_wrap_maker_is_told_the_panel_body_ceiling(tmp_path):
    """The panel judges a prefix of a long artifact (marked, never silent) —
    but the wrap maker must learn the ceiling BEFORE writing, or a 52k body
    loses its Open findings to the cap and the lap is refuted for what the
    critics could not see (first real run, 2026-09-04)."""
    from chimera.arcs.graph import VERIFY_PAYLOAD_MAX, WRAP_BODY_HEADROOM
    from tests.arc_drivers import _gr_node_payload, _gr_plan_payload, graph_fresh

    arc, state, _ = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    for node in ("gather-a", "gather-b", "judge-gathers"):
        state = arc.submit(state, f"node:{node}", _gr_node_payload(node), kind="null")
    assert state.stage == "wrap"
    (wrap_call,) = arc.pending_calls(state)
    from chimera.models import WRAP_BODY_MAX

    ceiling = VERIFY_PAYLOAD_MAX - WRAP_BODY_HEADROOM
    assert ceiling == WRAP_BODY_MAX  # the window is derived from the gated ceiling
    assert "BODY CEILING" in wrap_call.prompt
    assert f"{ceiling:,} characters" in wrap_call.prompt
    assert "REFUSED at submit" in wrap_call.prompt
    assert ceiling < VERIFY_PAYLOAD_MAX  # net of frontmatter/render overhead


def test_verify_panel_sees_a_short_artifact_whole(tmp_path):
    from tests.arc_drivers import graph_to_verify

    arc, state, _ = graph_to_verify(tmp_path)
    calls = arc.pending_calls(state)
    assert "truncated" not in calls[0].prompt


def _graph_to_wrap(tmp_path):
    from tests.arc_drivers import _gr_node_payload, _gr_plan_payload, graph_fresh

    arc, state, task_dir = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    for node in ("gather-a", "gather-b", "judge-gathers"):
        state = arc.submit(state, f"node:{node}", _gr_node_payload(node), kind="null")
    assert state.stage == "wrap"
    return arc, state, task_dir


def test_a_wrap_over_the_body_ceiling_is_refused_at_the_gate_and_state_survives(tmp_path):
    """Four wraps in a row (2026-09-16) ignored the disclosed ceiling and each
    panel graded a truncated artifact. The ceiling is now a schema gate: the
    over-long body is refused BEFORE anything is written, the error names the
    overage for the maker's one retry and stays short enough to ride that
    retry prompt, and the same label accepts a compliant resubmission."""
    from chimera.arcs.graph import ARTIFACT_FILE
    from chimera.models import WRAP_BODY_MAX
    from chimera.verify.schema_gate import SchemaGateError
    from tests.arc_drivers import _gr_wrap_payload

    arc, state, _ = _graph_to_wrap(tmp_path)
    over = _gr_wrap_payload()
    over["body"] = "x" * (WRAP_BODY_MAX + 5)
    with pytest.raises(SchemaGateError) as excinfo:
        arc.submit(state, "wrap", over, kind="null")
    message = str(excinfo.value)
    assert f"{WRAP_BODY_MAX + 5:,} chars" in message
    assert "cut 5 chars" in message
    assert len(message) < 2_000  # pydantic elides the value; the retry prompt stays small
    assert state.stage == "wrap"
    assert state.artifact is None
    assert not (arc.task_dir / "artifacts" / ARTIFACT_FILE).exists()
    state = arc.submit(state, "wrap", _gr_wrap_payload(), kind="null")
    assert state.stage == "verify"


def test_a_body_at_the_ceiling_reaches_the_panel_whole(tmp_path):
    """WRAP_BODY_HEADROOM must absorb the frontmatter and render overhead, or a
    compliant body would still be truncated for the panel and the gate would
    be a promise the window breaks."""
    from chimera.arcs.graph import ARTIFACT_FILE, VERIFY_PAYLOAD_MAX
    from chimera.models import WRAP_BODY_MAX
    from tests.arc_drivers import _gr_wrap_payload

    arc, state, _ = _graph_to_wrap(tmp_path)
    at_ceiling = _gr_wrap_payload()
    at_ceiling["body"] = "y" * WRAP_BODY_MAX
    state = arc.submit(state, "wrap", at_ceiling, kind="null")
    assert state.stage == "verify"
    rendered = (arc.task_dir / "artifacts" / ARTIFACT_FILE).read_text(encoding="utf-8")
    assert WRAP_BODY_MAX < len(rendered) <= VERIFY_PAYLOAD_MAX
    calls = arc.pending_calls(state)
    assert calls
    for call in calls:
        assert "truncated" not in call.prompt
        assert "y" * WRAP_BODY_MAX in call.prompt


def test_every_committed_artifact_body_is_under_the_wrap_ceiling():
    """The gate lives on the model, so it also runs at LOAD: a ceiling below
    any artifact already committed would make that run's arc-state unreadable.
    Walk every committed arc-state; skip when the checkout carries none."""
    import json
    from pathlib import Path

    from chimera.models import WRAP_BODY_MAX

    records = sorted((Path(__file__).resolve().parents[1] / "tasks").glob("*/arc-state*.json"))
    if not records:
        pytest.skip("no committed arc-state records in this checkout")
    for record in records:
        artifact = json.loads(record.read_text(encoding="utf-8")).get("artifact")
        if artifact is not None:
            assert len(artifact["body"]) <= WRAP_BODY_MAX, record


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


# ---------------------------------------------------------------------------
# The seeded-mutant panel credential (C15) — a control, never a gate
# ---------------------------------------------------------------------------


def _mutant_labels(arc, state) -> set[str]:
    return {c.label for c in arc.pending_calls(state)}


def test_no_mutant_is_scheduled_when_the_cadence_is_not_due(tmp_path):
    """The default cadence is 5 and this queue has committed 0 verdicts, so
    run 1 is uncredentialed: no extra call, and the verdict says so."""
    arc, state, _ = graph_to_verify(tmp_path)
    assert state.mutant is None
    assert _mutant_labels(arc, state) == {"verify:critic1", "verify:critic2"}
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=False), kind="null")
    assert arc.verify_verdict(state).panel_credential == "panel-uncredentialed"


def test_a_stray_mutant_submit_cannot_mint_a_credential(tmp_path):
    """No control was scheduled (cadence not due), yet a `verify:mutant1`
    payload arrives — by hand, or from a driver replaying an old call. It
    changes nothing: no credential, no record under mutants/, and the verdict
    still reads `panel-uncredentialed`."""
    arc, state, _ = graph_to_verify(tmp_path)
    assert state.mutant is None
    state = arc.submit(state, "verify:mutant1", valid_opinion(refuted=False))
    assert state.mutant is None
    assert not (arc.task_dir / "mutants").exists()
    assert any("MUTANT_IGNORED" in line for line in state.log)
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=False), kind="null")
    assert arc.verify_verdict(state).panel_credential == "panel-uncredentialed"


def test_a_due_run_emits_verify_mutant1_alongside_the_panel(tmp_path, monkeypatch):
    from tests.arc_drivers import graph_to_verify_with_mutant

    arc, state, _ = graph_to_verify_with_mutant(tmp_path, monkeypatch)
    assert state.mutant is not None and state.mutant.status == "pending"
    assert state.mutant.defect is not None and state.mutant.defect.operator == "quantity"

    calls = {c.label: c for c in arc.pending_calls(state)}
    assert set(calls) == {"verify:critic1", "verify:critic2", "verify:mutant1"}
    mutant_call = calls["verify:mutant1"]
    # the control carries the SEEDED artifact, and nothing tells the agent so
    assert "29 lanes" in mutant_call.prompt  # 4 * 7 + 1
    assert "4 lanes" not in mutant_call.prompt
    assert "mutant" not in mutant_call.prompt.lower()
    # ... and it is otherwise byte-identical to the angle it rides
    real = calls[f"verify:critic{state.mutant.defect.angle}"]
    assert mutant_call.prompt == real.prompt.replace("4 lanes", "29 lanes")
    assert (mutant_call.model, mutant_call.subagent_type) == (real.model, real.subagent_type)


def test_finalize_waits_for_a_pending_mutant(tmp_path, monkeypatch):
    """The verdict WAITS for the control; it never changes because of it. A
    finalize that ran without the control would write a verdict whose
    credential field is a lie about what was measured."""
    from tests.arc_drivers import graph_to_verify_with_mutant

    arc, state, task_dir = graph_to_verify_with_mutant(tmp_path, monkeypatch)
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=False), kind="null")
    assert state.phase == "verify"  # panel complete, control outstanding
    assert not (task_dir / "verification.json").exists()

    state = arc.submit(state, "verify:mutant1", valid_opinion(refuted=True), kind="null")
    assert state.phase == "complete"
    assert (task_dir / "verification.json").exists()


@pytest.mark.parametrize(
    "refuted,status,credential",
    [(True, "caught", "mutant-caught"), (False, "missed", "mutant-missed")],
)
def test_a_caught_or_missed_mutant_does_not_change_the_verdict(
    tmp_path, monkeypatch, refuted, status, credential
):
    """The credential NEVER gates: a panel that missed its control still
    passes, and one that caught it gets no easier ride."""
    from tests.arc_drivers import graph_to_verify_with_mutant

    arc, state, _ = graph_to_verify_with_mutant(tmp_path, monkeypatch)
    state = arc.submit(state, "verify:mutant1", valid_opinion(refuted=refuted), kind="null")
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=False), kind="null")

    assert state.phase == "complete"
    verdict = arc.verify_verdict(state)
    assert verdict.passed is True
    assert verdict.panel_credential == credential
    assert verdict.mutant is not None and verdict.mutant.status == status


def test_a_null_mutant_degrades_to_not_measured_and_finalizes(tmp_path, monkeypatch):
    from tests.arc_drivers import graph_to_verify_with_mutant

    arc, state, _ = graph_to_verify_with_mutant(tmp_path, monkeypatch)
    state = arc.submit(state, "verify:mutant1", None, kind="null")
    assert state.mutant is not None and state.mutant.status == "not-measured"
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=False), kind="null")
    assert state.phase == "complete"
    assert arc.verify_verdict(state).panel_credential == "mutant-not-measured"


def test_a_garbage_mutant_payload_never_raises(tmp_path, monkeypatch):
    """A broken control must not break the run it measures — every failure
    path is `not-measured`, not an exception."""
    from tests.arc_drivers import graph_to_verify_with_mutant

    arc, state, _ = graph_to_verify_with_mutant(tmp_path, monkeypatch)
    state = arc.submit(state, "verify:mutant1", {"bogus_extra_field": True}, kind="null")
    assert state.mutant is not None and state.mutant.status == "not-measured"
    assert state.mutant.note
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=False), kind="null")
    assert state.phase == "complete"
    assert arc.verify_verdict(state).passed is True


def test_the_mutant_opinion_never_enters_verify_opinions_or_open_findings(tmp_path, monkeypatch):
    """The control refutes a defect chimera seeded. Recording it as a panel
    finding would put a manufactured refutation on the artifact's record."""
    from tests.arc_drivers import graph_to_verify_with_mutant

    arc, state, _ = graph_to_verify_with_mutant(tmp_path, monkeypatch)
    state = arc.submit(
        state, "verify:mutant1", valid_opinion(refuted=True, severity="fatal"), kind="null"
    )
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", valid_opinion(refuted=False), kind="null")

    assert set(state.verify_opinions) == {"verify:critic1", "verify:critic2"}
    verdict = arc.verify_verdict(state)
    assert verdict.passed is True  # a FATAL refutation of the control vetoes nothing
    assert verdict.open_findings == []
    assert len(verdict.opinions) == 2
    assert verdict.valid_critic_count == 2
    assert state.verify_notes == []


def test_a_repair_lap_never_re_dispatches_the_mutant(tmp_path, monkeypatch):
    """One control per RUN, not per lap: re-seeding the rewritten artifact
    would charge a second call and measure the same panel twice."""
    from tests.arc_drivers import _gr_mutant_wrap_payload, graph_to_verify_with_mutant

    arc, state, _ = graph_to_verify_with_mutant(tmp_path, monkeypatch)
    state = arc.submit(state, "verify:mutant1", valid_opinion(refuted=True), kind="null")
    for i in (1, 2):
        state = arc.submit(state, f"verify:critic{i}", fatal_opinion(), kind="null")
    assert state.phase == "wrap" and state.verify_repairs == 1

    state = arc.submit(state, "wrap", _gr_mutant_wrap_payload(), kind="null")
    assert state.mutant is not None and state.mutant.status == "caught"  # not re-seeded
    assert _mutant_labels(arc, state) == {"verify:critic1", "verify:critic2"}
    assert state.audit.by_label.get("verify:mutant1") == 1


def test_the_mutant_record_is_written_under_mutants(tmp_path, monkeypatch):
    """`tasks/<id>/mutants/run-<n>.json` — the CLI's per-task checkpoint
    commits it with the rest of the task directory, and the push-boundary
    tripwire admits exactly this path shape."""
    import json as _json
    import re as _re

    from tests.arc_drivers import graph_to_verify_with_mutant

    arc, state, task_dir = graph_to_verify_with_mutant(tmp_path, monkeypatch)
    state = arc.submit(state, "verify:mutant1", valid_opinion(refuted=True), kind="null")

    records = sorted((task_dir / "mutants").glob("*"))
    assert [p.name for p in records] == ["run-1.json"]
    assert _re.fullmatch(r"run-\d+\.json", records[0].name)
    written = _json.loads(records[0].read_text(encoding="utf-8"))
    assert written["status"] == "caught"
    assert written["defect"]["operator"] == "quantity"


def test_run_abstract_carries_the_credential_line(tmp_path, monkeypatch):
    from chimera.arcs.graph import run_abstract
    from tests.arc_drivers import graph_to_verify_with_mutant

    _, plain_state, _ = graph_to_verify(tmp_path / "plain")
    assert "CREDENTIAL: panel-uncredentialed" in run_abstract(plain_state)

    arc, state, _ = graph_to_verify_with_mutant(tmp_path / "credentialed", monkeypatch)
    state = arc.submit(
        state, "verify:mutant1", valid_opinion(refuted=True, severity="fatal"), kind="null"
    )
    abstract = run_abstract(state)
    assert "CREDENTIAL: mutant-caught (quantity, fatal)" in abstract
    assert "verified" not in abstract


def test_mutant_timeout_expires_at_the_300s_default(tmp_path, monkeypatch):
    """`verify:mutant1` carries no special ceiling: it rides the 300 s default
    like the critics it measures, and an aged-out control degrades."""
    from tests.arc_drivers import ago, graph_to_verify_with_mutant

    arc, state, _ = graph_to_verify_with_mutant(tmp_path, monkeypatch)
    arc.pending_calls(state)  # stamps first_issued
    state.first_issued["verify:mutant1"] = ago(301)

    expired = arc.expire_timeouts(state)

    assert expired == ["verify:mutant1"]
    assert state.mutant is not None and state.mutant.status == "not-measured"


def test_pre_mutant_arc_state_still_loads():
    """Every committed arc-state.json predates `GraphArcState.mutant`; the
    field must default rather than fail validation (state models are
    extra="forbid")."""
    from pathlib import Path

    from chimera.arcs.graph import GraphArcState

    repo_root = Path(__file__).resolve().parent.parent
    records = sorted((repo_root / "tasks").glob("*/arc-state.json"))
    if not records:
        pytest.skip("no committed arc-state.json in this checkout")
    for record in records:
        state = GraphArcState.model_validate_json(record.read_text(encoding="utf-8"))
        assert state.mutant is None


def test_priors_seed_reaches_the_planner_and_nothing_else(tmp_path):
    """The L2 seed rides the PLANNER call alone. The planner authors the node
    briefs, so what matters travels folded into them; re-sending the 1,500-char
    block to every worker was up to 16 identical copies per run. Checkers
    never carried it (input-set invariant) and wrap never did either."""
    from chimera.arcs._common import PriorsSeed
    from tests.arc_drivers import _gr_node_payload as _np

    seed = "PRIORS: last run of this shape halted on X"

    # the planner sees it
    planner_arc, fresh, _ = graph_fresh(tmp_path / "planner")
    fresh.priors = PriorsSeed(block=seed)
    assert seed in planner_arc.pending_calls(fresh)[0].prompt

    # no worker node sees it
    arc, state, _ = _to_run(tmp_path / "run")
    state.priors = PriorsSeed(block=seed)
    calls = {c.label: c for c in arc.pending_calls(state)}
    assert seed not in calls["node:gather-a"].prompt
    assert seed not in calls["node:gather-b"].prompt

    # nor the checker, nor wrap
    state = arc.submit(state, "node:gather-a", _np("gather-a"))
    state = arc.submit(state, "node:gather-b", _np("gather-b"))
    judge_call = arc.pending_calls(state)[0]
    assert seed not in judge_call.prompt
    state = arc.submit(state, "node:judge-gathers", _np("judge-gathers"))
    assert state.stage == "wrap"
    assert seed not in arc.pending_calls(state)[0].prompt

"""Executor→maker repair lap — bounded make→check→revise in code (approved
2026-08-28, panel roadmap #8).

An executor node landing PAUSE re-runs the maker node(s) it read (carrying
the executor's findings), then itself — sequentially via repair_queue,
bounded per executor by CHIMERA_GRAPH_REPAIR_LAPS. On exhaustion the PAUSE
stands and rides the digest. The loop lives in CODE; the DAG stays acyclic
data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.arcs._common import ARC_STATE_FILE
from chimera.arcs.graph import GraphArc, GraphArcError
from chimera.graph import estimated_calls, overhead_calls
from chimera.models import GraphPlan, TaskSpec

PAUSE = "PAUSE — SURFACE TO OPERATOR"


def _build_plan() -> dict:
    return {
        "goal": "build the widget",
        "rationale": "straight: make then test",
        "phases": [
            {"name": "make", "nodes": [
                {"id": "impl", "role": "maker", "tier": "frontier",
                 "brief": "write the widget"},
            ]},
            {"name": "test", "nodes": [
                {"id": "check", "role": "executor",
                 "brief": "run the widget's tests", "reads": ["impl"]},
            ]},
        ],
    }


def _out(node_id: str, recommendation: str = "PROCEED", output: str = "ok") -> dict:
    return {"node_id": node_id, "output": output, "sources": ["s.md:1"],
            "confidence": 82, "recommendation": recommendation}


def _to_test_phase(tmp_path: Path):
    arc = GraphArc(tmp_path / "task")
    arc.task_dir.mkdir(parents=True, exist_ok=True)
    spec = TaskSpec(id="20260828-repair", slug="repair",
                    ask="build the widget", arc="graph")
    state = arc.initialize(spec)
    state = arc.submit(state, "plan", _build_plan())
    state = arc.submit(state, "node:impl", _out("impl"))
    assert state.stage == "run"
    return arc, state


def test_pause_triggers_a_bounded_repair_lap(tmp_path):
    arc, state = _to_test_phase(tmp_path)
    state = arc.submit(
        state, "node:check",
        _out("check", recommendation=PAUSE, output="2 tests failed: widget.spin"),
    )
    assert state.repair_queue == ["impl", "check"]
    assert state.exec_repairs == {"check": 1}
    assert "impl" not in state.outputs and "check" not in state.outputs
    # sequential lap: the maker repair is the ONLY pending call, carrying the
    # executor's findings — repair context never free-floats
    calls = arc.pending_calls(state)
    assert [c.label for c in calls] == ["node:impl"]
    assert "REPAIR CONTEXT" in calls[0].prompt
    assert "2 tests failed: widget.spin" in calls[0].prompt
    # maker lands -> the executor re-check is next
    state = arc.submit(state, "node:impl", _out("impl", output="fixed"))
    calls = arc.pending_calls(state)
    assert [c.label for c in calls] == ["node:check"]
    assert "Re-run your checks" in calls[0].prompt
    # executor passes -> lap closed, barrier advances
    state = arc.submit(state, "node:check", _out("check"))
    assert state.repair_queue == []
    assert state.stage == "wrap"
    assert any("EXEC_REPAIR node=check lap=1" in line for line in state.log)


def test_repair_laps_are_bounded_then_the_pause_stands(tmp_path):
    """Default lever: ONE lap. A second PAUSE stands — the run continues and
    the flag rides the digest, never a new halt class."""
    arc, state = _to_test_phase(tmp_path)
    state = arc.submit(state, "node:check", _out("check", recommendation=PAUSE))
    state = arc.submit(state, "node:impl", _out("impl"))
    state = arc.submit(state, "node:check", _out("check", recommendation=PAUSE))
    assert state.repair_queue == []
    assert state.stage == "wrap"
    assert state.outputs["check"].recommendation.startswith("PAUSE")
    assert any("EXEC_REPAIR_EXHAUSTED" in line for line in state.log)


def test_repair_laps_follow_the_lever(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_GRAPH_REPAIR_LAPS", "2")
    arc, state = _to_test_phase(tmp_path)
    state = arc.submit(state, "node:check", _out("check", recommendation=PAUSE))
    state = arc.submit(state, "node:impl", _out("impl"))
    state = arc.submit(state, "node:check", _out("check", recommendation=PAUSE))
    assert state.exec_repairs == {"check": 2}  # second lap granted
    assert state.repair_queue == ["impl", "check"]


def test_pause_without_a_maker_read_does_not_trigger(tmp_path):
    """An executor over researcher output has no repair edge — the PAUSE
    stands immediately and rides the digest."""
    arc = GraphArc(tmp_path / "task")
    arc.task_dir.mkdir(parents=True, exist_ok=True)
    spec = TaskSpec(id="20260828-norepair", slug="norepair", ask="x", arc="graph")
    state = arc.initialize(spec)
    plan = _build_plan()
    plan["phases"][0]["nodes"][0]["role"] = "researcher"
    plan["phases"][0]["nodes"][0]["tier"] = "fast"
    state = arc.submit(state, "plan", plan)
    state = arc.submit(state, "node:impl", _out("impl"))
    state = arc.submit(state, "node:check", _out("check", recommendation=PAUSE))
    assert state.repair_queue == []
    assert state.stage == "wrap"


def test_null_during_repair_degrades_and_the_lap_continues(tmp_path):
    arc, state = _to_test_phase(tmp_path)
    state = arc.submit(state, "node:check", _out("check", recommendation=PAUSE))
    state = arc.submit(state, "node:impl", None, kind="null")  # repair degrades
    assert state.outputs["impl"] is None
    assert state.repair_queue == ["check"]
    state = arc.submit(state, "node:check", _out("check"))
    assert state.stage == "wrap"


def test_out_of_order_repair_submission_is_refused(tmp_path):
    arc, state = _to_test_phase(tmp_path)
    state = arc.submit(state, "node:check", _out("check", recommendation=PAUSE))
    with pytest.raises(GraphArcError, match="queued for repair but not at the head"):
        arc.submit(state, "node:check", _out("check"))


def test_second_executor_pause_is_deferred_then_gets_its_own_lap(tmp_path):
    """A sibling executor's PAUSE arriving mid-lap is DEFERRED, not dropped:
    it is logged, queued, and gets its own repair lap when the queue drains.

    Supersedes the v7.1 R-3 drill, which asserted EXEC_REPAIR_SKIPPED — a log
    line, not a repair. Under that behavior only the FIRST executor in a phase
    could ever earn a lap; every sibling's PAUSE rode the digest unrepaired."""
    arc = GraphArc(tmp_path / "task")
    arc.task_dir.mkdir(parents=True, exist_ok=True)
    spec = TaskSpec(id="20260828-2exec", slug="two-exec", ask="x", arc="graph")
    state = arc.initialize(spec)
    plan = {
        "goal": "build the widget",
        "rationale": "one maker, two independent checks",
        "phases": [
            {"name": "make", "nodes": [
                {"id": "impl", "role": "maker", "tier": "frontier", "brief": "b"},
            ]},
            {"name": "test", "nodes": [
                {"id": "e1", "role": "executor", "brief": "unit tests", "reads": ["impl"]},
                {"id": "e2", "role": "executor", "brief": "lint", "reads": ["impl"]},
            ]},
        ],
    }
    state = arc.submit(state, "plan", plan)
    state = arc.submit(state, "node:impl", _out("impl"))
    state = arc.submit(state, "node:e1", _out("e1", recommendation=PAUSE))
    assert state.repair_queue == ["impl", "e1"]
    state = arc.submit(state, "node:e2", _out("e2", recommendation=PAUSE))
    # deferred, not skipped: logged AND queued for its own lap
    assert state.exec_repairs == {"e1": 1}
    assert any("EXEC_REPAIR_DEFERRED e2" in line for line in state.log)
    assert state.deferred_repairs == ["e2"]
    assert state.outputs["e2"].recommendation.startswith("PAUSE")

    # drain e1's lap; the moment it empties, e2's lap starts
    state = arc.submit(state, "node:impl", _out("impl"))
    state = arc.submit(state, "node:e1", _out("e1"))
    assert state.exec_repairs == {"e1": 1, "e2": 1}, "e2 must earn its own lap"
    assert state.deferred_repairs == []
    # e2's lap repairs impl, so e1's just-landed PROCEED — which judged the
    # PRE-repair impl — is invalidated with it and re-runs (the stale-sibling
    # invariant, exercised here as a side effect of the deferred lap)
    assert state.repair_queue == ["impl", "e1", "e2"]
    assert "e1" not in state.outputs

    # and it converges: the lap drains and the phase advances
    state = arc.submit(state, "node:impl", _out("impl"))
    state = arc.submit(state, "node:e1", _out("e1"))
    state = arc.submit(state, "node:e2", _out("e2"))
    assert state.repair_queue == []
    assert state.stage == "wrap"


def test_estimated_calls_carry_the_repair_allowance():
    plan = GraphPlan.model_validate(_build_plan())
    # 2 planned nodes + one executor→maker pair: laps × (1 maker + itself)
    assert estimated_calls(plan, 1) == overhead_calls(1) + 2 + 2
    assert estimated_calls(plan, 3) == overhead_calls(3) + 2 + 6


def test_digest_flags_a_standing_pause(repo):
    from chimera import digest as digest_mod
    from chimera.queue import Queue

    queue = Queue(root=repo)
    tid = "20260828-pause-demo"
    task_dir = queue.task_dir(tid)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.json").write_text(
        json.dumps({
            "spec": {"id": tid, "slug": "pause-demo", "ask": "x", "arc": "graph",
                     "created": "2026-08-28T00:00:00Z"},
            "state": "ready",
            "history": [{"from_state": None, "to_state": "ready",
                         "at": "2026-08-28T00:00:00Z", "by": "t"}],
        }) + "\n",
        encoding="utf-8",
    )
    (task_dir / ARC_STATE_FILE).write_text(
        json.dumps({"outputs": {"check": _out("check", recommendation=PAUSE)}}),
        encoding="utf-8",
    )
    record = queue.load(tid)
    flags = digest_mod._arc_flags(queue, record)
    assert any("PAUSE recommendation on `check`" in f for f in flags)

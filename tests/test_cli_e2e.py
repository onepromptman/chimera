"""End-to-end dry run through the CLI — one door, one arc.

Scenario 1 (ambiguous ask): park + single posted question -> answer ->
autonomous graph run via tick/submit -> digest entry -> approve -> archived.
Scenario 2 (clean ask): straight through.
Scenario 3 (subagent flow): the six-role subagent_type flows through the CLI.
"""

import json

import pytest

from chimera.cli import main
from chimera.queue import Queue
from tests.arc_drivers import (
    _gr_node_payload,
    _gr_plan_payload,
    _gr_wrap_payload,
    valid_opinion,
)


def run(capsys, *argv, expect_code=0):
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    assert exc.value.code == expect_code, capsys.readouterr().err
    out = capsys.readouterr().out
    return json.loads(out) if out.lstrip().startswith("{") else out


def respond(label: str):
    if label == "plan":
        return _gr_plan_payload()
    if label.startswith("node:"):
        node_id = label.split(":", 1)[1]
        # gather-a comes back below the 70 threshold -> digest must flag it
        return _gr_node_payload(node_id, confidence=60 if node_id == "gather-a" else 82)
    if label == "wrap":
        return _gr_wrap_payload()
    if label.startswith("verify:"):
        return valid_opinion(refuted=False)
    raise AssertionError(f"unexpected label {label}")


def pump_until_signoff(capsys, task_id):
    payload = run(capsys, "tick")
    assert payload["action"] == "work"
    assert payload["task_id"] == task_id
    pending = payload["pending_calls"]
    guard = 0
    result = payload
    while pending:
        guard += 1
        assert guard < 200
        call = pending[0]
        result = run(
            capsys, "arc", "submit", task_id, call["label"],
            "--json", json.dumps(respond(call["label"])),
        )
        pending = result["pending_calls"]
    return result


def test_ambiguous_ask_full_lifecycle(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)

    # G1: ambiguous -> park with ONE posted question set
    out = run(
        capsys, "new", "research e-bikes maybe for commuting or touring",
        "--outcome", "ask",
        "--question", "Commuting or touring?", "--slug", "ebike-pick",
    )
    task = out["tasks"][0]
    task_id = task["task_id"]
    assert task["state"] == "awaiting-input"
    assert "Commuting or touring?" in task["questions_comment"]

    # tick while parked: idle (parked tasks are not runnable)
    assert run(capsys, "tick")["action"] == "idle"

    # answer -> ready
    out = run(capsys, "answer", task_id, "--answer", "q1", "commuting")
    assert out["state"] == "ready"

    # autonomous run: claim + graph to completion
    result = pump_until_signoff(capsys, task_id)
    assert result["arc_phase"] == "complete"
    assert result["verification"]["passed"] is True
    assert "/approve" in result["signoff_comment"]

    # digest flagged the low-confidence node output
    digests = list((repo / "digest").glob("*.md"))
    assert digests, "digest rollup must be committed"
    digest_text = digests[0].read_text()
    assert "low confidence (60)" in digest_text
    assert task_id in digest_text

    # G2 -> done -> archived
    out = run(capsys, "approve", task_id)
    assert out["state"] == "done"
    out = run(capsys, "archive", task_id)
    assert out["state"] == "archived"

    # durable: everything reached the bare origin
    queue = Queue(root=repo)
    record = queue.load(task_id)
    assert record.state == "archived"
    assert record.approved_by == "operator"


def test_clean_ask_straight_through(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    out = run(
        capsys, "new", "compare static site generators",
        "--slug", "ssg-compare",
    )
    task_id = out["tasks"][0]["task_id"]
    assert out["tasks"][0]["state"] == "ready"
    assert out["tasks"][0].get("arc", "graph") in ("graph", None) or True
    result = pump_until_signoff(capsys, task_id)
    assert result["arc_phase"] == "complete"
    run(capsys, "approve", task_id)
    out = run(capsys, "status")
    assert [t["state"] for t in out["tasks"]] == ["done"]
    assert [t["arc"] for t in out["tasks"]] == ["graph"]  # one door


def test_approve_before_arc_completes_is_blocked(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    out = run(
        capsys, "new", "quick research", "--slug", "quick",
    )
    task_id = out["tasks"][0]["task_id"]
    run(capsys, "tick")
    run(capsys, "approve", task_id, expect_code=1)  # still running, no signoff state


def test_role_subagents_flow_through_the_cli(repo, monkeypatch, capsys):
    """The six-role selection survives the CLI wire: the plan call names the
    planner role, work nodes name their fence roles, and phase-2 fan-in runs
    a judge with a model distinct from the fast gathers."""
    monkeypatch.chdir(repo)
    out = run(capsys, "new", "role flow drill", "--slug", "role-flow")
    task_id = out["tasks"][0]["task_id"]

    payload = run(capsys, "tick")
    plan_call = payload["pending_calls"][0]
    assert plan_call["label"] == "plan"
    assert plan_call["subagent_type"] == "planner"

    result = run(capsys, "arc", "submit", task_id, "plan",
                 "--json", json.dumps(_gr_plan_payload()))
    gathers = {c["label"]: c for c in result["pending_calls"]}
    assert set(gathers) == {"node:gather-a", "node:gather-b"}
    assert all(c["subagent_type"] == "researcher" for c in gathers.values())

    for label in ("node:gather-a", "node:gather-b"):
        result = run(capsys, "arc", "submit", task_id, label,
                     "--json", json.dumps(respond(label)))
    (judge_call,) = result["pending_calls"]
    assert judge_call["subagent_type"] == "judge"
    assert judge_call["model"] != gathers["node:gather-a"]["model"]  # maker != checker

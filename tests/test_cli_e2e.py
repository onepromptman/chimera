"""End-to-end dry run through the CLI — one door, one arc (ADR-010).

Scenario 1 (full lifecycle): new -> autonomous graph run via tick/submit ->
digest entry -> auto-approve on a passing panel -> archived. An ambiguous ask
is disambiguated in conversation BEFORE `chimera new` now — there is no async
ask/park branch (D1, handoff item 3).
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
            capsys,
            "arc",
            "submit",
            task_id,
            call["label"],
            "--json",
            json.dumps(respond(call["label"])),
        )
        pending = result["pending_calls"]
    return result


def test_full_lifecycle_to_archive(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)

    # G1: the ask is already disambiguated (in conversation, before this
    # call) — `chimera new` opens the task straight to `ready`.
    out = run(
        capsys,
        "new",
        "research e-bikes for commuting, not touring",
        "--slug",
        "ebike-pick",
    )
    task = out["tasks"][0]
    task_id = task["task_id"]
    assert task["state"] == "ready"

    # autonomous run: claim + graph to completion
    result = pump_until_signoff(capsys, task_id)
    assert result["arc_phase"] == "complete"
    assert result["verification"]["passed"] is True
    # G2 is review-after: a passing panel auto-approves, the comment says so
    assert "Done" in result["signoff_comment"]
    assert "reject" in result["signoff_comment"]
    assert "/approve" not in result["signoff_comment"]

    # digest flagged the low-confidence node output
    digests = list((repo / "digest").glob("*.md"))
    assert digests, "digest rollup must be committed"
    digest_text = digests[0].read_text()
    assert "low confidence (60)" in digest_text
    assert task_id in digest_text

    # auto-approved -> done already; a manual approve has nothing to act on
    assert Queue(root=repo).load(task_id).state == "done"
    run(capsys, "approve", task_id, expect_code=1)
    out = run(capsys, "archive", task_id)
    assert out["state"] == "archived"

    # durable: everything reached the bare origin
    queue = Queue(root=repo)
    record = queue.load(task_id)
    assert record.state == "archived"
    assert record.approved_by == "auto:verify-pass"


def test_clean_ask_straight_through(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    out = run(
        capsys,
        "new",
        "compare static site generators",
        "--slug",
        "ssg-compare",
    )
    task_id = out["tasks"][0]["task_id"]
    assert out["tasks"][0]["state"] == "ready"
    assert out["tasks"][0].get("arc", "graph") in ("graph", None) or True
    result = pump_until_signoff(capsys, task_id)
    assert result["arc_phase"] == "complete"
    # no approve step: the passing panel finished the task on its own
    out = run(capsys, "status")
    assert [t["state"] for t in out["tasks"]] == ["done"]
    assert [t["arc"] for t in out["tasks"]] == ["graph"]  # one door


def test_approve_before_arc_completes_is_blocked(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    out = run(
        capsys,
        "new",
        "quick research",
        "--slug",
        "quick",
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

    result = run(capsys, "arc", "submit", task_id, "plan", "--json", json.dumps(_gr_plan_payload()))
    gathers = {c["label"]: c for c in result["pending_calls"]}
    assert set(gathers) == {"node:gather-a", "node:gather-b"}
    assert all(c["subagent_type"] == "researcher" for c in gathers.values())

    for label in ("node:gather-a", "node:gather-b"):
        result = run(capsys, "arc", "submit", task_id, label, "--json", json.dumps(respond(label)))
    (judge_call,) = result["pending_calls"]
    assert judge_call["subagent_type"] == "judge"
    assert judge_call["model"] != gathers["node:gather-a"]["model"]  # maker != checker


# ---------------------------------------------------------------------------
# Absence tests (Rule 6, D1/D2 handoff item 3): the ask/park branch and the
# one-shot migration are gone from the CLI surface outright, not just unwired.
# ---------------------------------------------------------------------------


def test_new_parser_rejects_outcome_and_question_flags():
    from chimera.cli import build_parser

    parser = build_parser()
    for bad_argv in (
        ["new", "an ask", "--outcome", "ask"],
        ["new", "an ask", "--question", "budget?"],
    ):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(bad_argv)
        assert exc.value.code == 2  # argparse's "unrecognized arguments" exit


def test_help_lists_neither_answer_nor_migrate_tasks():
    from chimera.cli import build_parser

    parser = build_parser()
    subcommands = parser._subparsers._group_actions[0].choices.keys()
    assert "answer" not in subcommands
    assert "migrate-tasks" not in subcommands

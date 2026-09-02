"""N8 remediation — structured CLI errors.

The audit graded error CONTENT A-/B but presentation poor: raw 15-frame
tracebacks, no framing, no state-safety confirmation. Every CLI failure now
emits machine-parsable JSON on stdout ({"ok": false, ...}) and framed
Error/Hint/State lines on stderr — never a traceback. CHIMERA_DEBUG=1
restores the raw traceback for debugging.
"""

import json

import pytest

from chimera.cli import main
from tests.test_failed_state import _new_task, run
from tests.arc_drivers import _gr_plan_payload as plan_payload


def fail(capsys, *argv):
    """Run a CLI invocation expected to exit 1; return (stdout_json, stderr)."""
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert captured.err.startswith("Error: ")
    assert "State: " in captured.err
    return payload, captured.err


def test_malformed_json_flag_is_framed(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "badjson")
    tick_out = run(capsys, "tick")
    label = tick_out["pending_calls"][0]["label"]

    payload, err = fail(capsys, "arc", "submit", tid, label, "--json", "{not json")
    assert "invalid JSON" in payload["error"]
    assert "Hint: " in err
    assert "pending call is intact" in payload["state"]

    # state safety is real: the same submit succeeds afterwards
    result = run(capsys, "arc", "submit", tid, label, "--json", json.dumps(plan_payload()))
    assert result["ok"] is True


def test_missing_file_is_framed(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "nofile")
    tick_out = run(capsys, "tick")
    label = tick_out["pending_calls"][0]["label"]

    payload, err = fail(capsys, "arc", "submit", tid, label, "--file", "does-not-exist.json")
    assert "file not found" in payload["error"]
    assert "Hint: " in err


def test_malformed_file_is_framed(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "badfile")
    tick_out = run(capsys, "tick")
    label = tick_out["pending_calls"][0]["label"]
    bad = repo / "bad.json"
    bad.write_text("{truncated", encoding="utf-8")

    payload, _ = fail(capsys, "arc", "submit", tid, label, "--file", str(bad))
    assert "invalid JSON" in payload["error"]


def test_schema_gate_rejection_is_framed_and_state_safe(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "badshape")
    tick_out = run(capsys, "tick")
    label = tick_out["pending_calls"][0]["label"]

    payload, err = fail(capsys, "arc", "submit", tid, label, "--json", '{"bogus": true}')
    assert "schema gate" in payload["error"]  # TICK_PROTOCOL greps this
    assert "Hint: " in err

    # the pending call survived the rejection — a valid resubmit proceeds
    result = run(capsys, "arc", "submit", tid, label, "--json", json.dumps(plan_payload()))
    assert result["arc_phase"] == "run"


def test_unknown_task_is_framed(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    payload, _ = fail(capsys, "abandon", "20990101-ghost")
    assert "no such task" in payload["error"]


def test_unexpected_error_is_framed_without_debug(repo, monkeypatch, capsys):
    """The last-resort handler's own guarantee: an exception outside the typed
    set is framed (JSON + Error/Hint/State), never a raw traceback."""
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CHIMERA_DEBUG", raising=False)
    tid = _new_task(capsys, "unexpected")
    run(capsys, "tick")

    from chimera.queue import Queue

    state_path = Queue(root=repo).task_dir(tid) / "arc-state.json"
    state_path.write_text("not json at all", encoding="utf-8")
    payload, err = fail(capsys, "arc", "next", tid)
    assert payload["error"].startswith("unexpected ")
    assert "CHIMERA_DEBUG=1" in payload["hint"]


def test_nonresearch_arc_error_family_gets_the_arc_hint(repo, monkeypatch, capsys):
    """Every arc defines its own <Arc>ArcError; the handler must give the
    routine 'arc next' hint for the whole family, not 'unexpected'."""
    monkeypatch.chdir(repo)

    def raise_arc_error(args):
        from chimera.arcs.graph import GraphArcError

        raise GraphArcError("label 'plan:1' is not pending")

    monkeypatch.setattr("chimera.cli.cmd_status", raise_arc_error)
    payload, err = fail(capsys, "status")
    assert "not pending" in payload["error"]
    assert "unexpected" not in payload["error"]
    assert "arc next" in payload["hint"]


def test_debug_env_restores_traceback(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("CHIMERA_DEBUG", "1")
    tid = _new_task(capsys, "debug")
    run(capsys, "tick")

    # force an unexpected (non-QueueError) failure: corrupt arc state
    from chimera.queue import Queue

    state_path = Queue(root=repo).task_dir(tid) / "arc-state.json"
    state_path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        main(["arc", "next", tid])
    assert not isinstance(exc.value, SystemExit)

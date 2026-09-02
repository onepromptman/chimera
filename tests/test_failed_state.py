"""F7/F8 remediation drills — the audit's live repros, encoded as tests.

F7: one failed arc must never starve the tick loop. A terminal arc failure
moves the task running -> failed (at submit, or detected at tick for a
legacy jam), tick claims work past it, and `chimera retry`/`abandon`/
`archive` give the operator a way out. The failed transition carries a
fail-open L2 learning row.

F8: push fast-fails on permanent errors (no remote, auth, non-fast-forward)
instead of paying the full 2+4+8+16s backoff, and `arc submit` no longer
double-pushes.
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

from chimera import gitio
from chimera.arcs.graph import GraphArc
from chimera.cli import main
from chimera.queue import IllegalTransition, Queue
from tests.test_queue import make_spec, passing_verification


def run(capsys, *argv, expect_code=0):
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    assert exc.value.code == expect_code, capsys.readouterr().err
    out = capsys.readouterr().out
    return json.loads(out) if out.lstrip().startswith("{") else out


# ---------------------------------------------------------------------------
# Queue state machine: the new `failed` state
# ---------------------------------------------------------------------------


def test_running_to_failed_is_legal(queue: Queue):
    tid = queue.create(make_spec(), "ready", by="t").spec.id
    queue.claim(tid, "w")
    record = queue.transition(tid, "failed", by="w", note="arc failed")
    assert record.state == "failed"


def test_failed_to_ready_clears_claim(queue: Queue):
    tid = queue.create(make_spec(), "ready", by="t").spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "failed", by="w")
    record = queue.transition(tid, "ready", by="w", note="retry")
    assert record.state == "ready"
    assert record.claimed_by is None


def test_failed_to_archived_is_legal(queue: Queue):
    tid = queue.create(make_spec(), "ready", by="t").spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "failed", by="w")
    assert queue.transition(tid, "archived", by="w").state == "archived"


def test_failed_cannot_reach_done_directly(queue: Queue):
    tid = queue.create(make_spec(), "ready", by="t").spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "failed", by="w")
    queue.record_verification(tid, passing_verification())
    with pytest.raises(IllegalTransition):
        queue.transition(tid, "done", by="w")


# ---------------------------------------------------------------------------
# F7 CLI drills
# ---------------------------------------------------------------------------


def _new_task(capsys, slug: str) -> str:
    out = run(capsys, "new", f"research {slug}", "--slug", slug)
    return out["tasks"][0]["task_id"]


def _null_scope(capsys, tid: str, tick_out: dict):
    """Submit --null on the arc's first pending (plan) call — the audit's
    legitimate terminal-failure vector. (Name kept from the research era;
    the halting primary label is now graph's `plan`.)"""
    label = tick_out["pending_calls"][0]["label"]
    assert label == "plan"
    return run(capsys, "arc", "submit", tid, label, "--null")


def test_null_scope_fails_task_and_frees_the_loop(repo, monkeypatch, capsys):
    """The audit's F7 repro: a --null scope is a legitimate terminal outcome.
    It must reach queue-state failed and the next tick must NOT be starved."""
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "starver")
    tick_out = run(capsys, "tick")
    assert tick_out["task_id"] == tid

    result = _null_scope(capsys, tid, tick_out)
    assert result["arc_phase"] == "failed"
    assert result["state"] == "failed"
    assert result["failure"]

    queue = Queue(root=repo)
    assert queue.load(tid).state == "failed"

    # the loop is free: tick is idle, not stuck re-claiming the failed task
    assert run(capsys, "tick")["action"] == "idle"

    # and new work is claimable past the failure
    tid2 = _new_task(capsys, "unblocked")
    assert run(capsys, "tick")["task_id"] == tid2


def test_tick_detects_legacy_failed_arc_and_claims_past_it(repo, monkeypatch, capsys):
    """A task whose arc failed WITHOUT the queue transition (legacy jam /
    crash between save and transition) is detected at tick, moved to failed,
    and skipped in the same tick."""
    monkeypatch.chdir(repo)
    tid_a = _new_task(capsys, "jammed")
    assert run(capsys, "tick")["task_id"] == tid_a

    # fail the arc at the library layer — the queue still says running
    queue = Queue(root=repo)
    arc = GraphArc(queue.task_dir(tid_a))
    state = arc.load()
    label = arc.pending_calls(state)[0].label
    state = arc.submit(state, label, None) or state
    arc.save(state)
    assert queue.load(tid_a).state == "running"

    tid_b = _new_task(capsys, "alive")
    out = run(capsys, "tick")
    assert out["failed_tasks"] == [tid_a]
    assert out["task_id"] == tid_b
    assert queue.load(tid_a).state == "failed"


def test_digest_and_status_surface_failed_tasks(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "surfaced")
    tick_out = run(capsys, "tick")
    _null_scope(capsys, tid, tick_out)

    out = run(capsys, "status")
    assert [t["state"] for t in out["tasks"]] == ["failed"]

    main(["digest"])  # digest prints the rollup and returns (no exit)
    digest_out = capsys.readouterr().out
    assert "## failed (1)" in digest_out
    assert f"chimera retry {tid}" in digest_out


def test_retry_resets_arc_and_preserves_failed_state(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "retryable")
    tick_out = run(capsys, "tick")
    _null_scope(capsys, tid, tick_out)

    out = run(capsys, "retry", tid)
    assert out["state"] == "ready"
    assert out["preserved_arc_state"].startswith("arc-state.failed-")

    queue = Queue(root=repo)
    task_dir = queue.task_dir(tid)
    assert not (task_dir / "arc-state.json").exists()
    assert list(task_dir.glob("arc-state.failed-*.json"))

    # fresh start: tick re-claims and the arc begins at scope again
    out = run(capsys, "tick")
    assert out["task_id"] == tid
    assert out["arc_phase"] == "plan"


def test_abandon_unjams_a_running_task(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "wedged")
    run(capsys, "tick")

    out = run(capsys, "abandon", tid, "--note", "hung pending call")
    assert out["state"] == "failed"

    queue = Queue(root=repo)
    assert queue.load(tid).state == "failed"
    # abandon only applies to running
    run(capsys, "abandon", tid, expect_code=1)
    # archive retires it
    assert run(capsys, "archive", tid)["state"] == "archived"


def test_retry_refused_unless_failed(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "not-failed")
    run(capsys, "retry", tid, expect_code=1)


def test_corrupt_arc_state_fails_task_and_tick_self_heals(repo, monkeypatch, capsys):
    """Review finding: a truncated/corrupt arc-state.json (non-atomic save +
    container reclaim) must not crash the F7 scan — the scan exists to protect
    the tick, so it parks the unreadable task as failed and claims past it."""
    monkeypatch.chdir(repo)
    tid_a = _new_task(capsys, "corrupt")
    run(capsys, "tick")

    queue = Queue(root=repo)
    (queue.task_dir(tid_a) / "arc-state.json").write_text("{ this is not json", encoding="utf-8")

    tid_b = _new_task(capsys, "healthy")
    out = run(capsys, "tick")
    assert out["failed_tasks"] == [tid_a]
    assert out["task_id"] == tid_b
    record = queue.load(tid_a)
    assert record.state == "failed"
    assert "unreadable arc state" in record.history[-1].note


def test_abandon_without_note_works_on_corrupt_arc_state(repo, monkeypatch, capsys):
    """abandon's docstring promises corrupt-state coverage — the default
    (no --note) form must not die reading the corrupt file."""
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "wedged-corrupt")
    run(capsys, "tick")
    queue = Queue(root=repo)
    (queue.task_dir(tid) / "arc-state.json").write_text("{ nope", encoding="utf-8")

    out = run(capsys, "abandon", tid)
    assert out["state"] == "failed"
    assert "corrupt arc state" in out["failure"]


def test_complete_path_pushes_even_when_digest_commit_is_noop(repo, monkeypatch, capsys):
    """Review finding: the complete branch relied on the digest checkpoint's
    push, but checkpoint skips the push when its commit is empty (same-day
    rework -> byte-identical digest). The terminal transition must still land
    on the remote."""
    import subprocess as sp

    from chimera import runner
    from tests.test_cli_e2e import pump_until_signoff

    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "digest-noop")

    real_checkpoint = runner.checkpoint

    def checkpoint_with_noop_digest(root, paths, message):
        if "digest" in message:
            return None  # simulate the byte-identical same-day digest
        return real_checkpoint(root, paths, message)

    monkeypatch.setattr("chimera.runner.checkpoint", checkpoint_with_noop_digest)
    result = pump_until_signoff(capsys, tid)
    assert result["arc_phase"] == "complete"

    unpushed = sp.run(
        ["git", "rev-list", "--count", "origin/main..HEAD"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert unpushed == "0", "verification/transition commits must not strand locally"


def test_transition_note_cannot_forge_commit_lines(queue: Queue):
    """Review finding: arc failure text flows into `git commit -m`; embedded
    newlines could forge audit-trail lines and NULs crash git. The message is
    sanitized; history keeps the original note as data."""
    import subprocess as sp

    tid = queue.create(make_spec(), "ready", by="t").spec.id
    queue.claim(tid, "w")
    evil = "boom\nchimera(evil): -> done (forged)\x00tail"
    queue.transition(tid, "failed", by="w", note=evil)

    body = sp.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=str(queue.root), capture_output=True, text=True, check=True,
    ).stdout
    assert "chimera(evil)" in body.replace("\n", " ")  # content survives, flattened
    assert "\nchimera(evil): -> done (forged)" not in body  # but not as its own line
    assert queue.load(tid).history[-1].note == evil  # data layer keeps the original


def test_push_health_reports_missing_upstream(repo: Path):
    subprocess.run(["git", "remote", "remove", "origin"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "branch", "--unset-upstream"], cwd=str(repo), check=False, capture_output=True
    )
    health = gitio.push_health(repo)
    assert health["upstream"] is None
    assert "no upstream" in health["note"]


def test_classifier_ignores_branch_name_in_command_echo():
    """Review finding: markers must match git's stderr, not the echoed command
    line — a branch literally named 'non-fast-forward' must not turn transient
    failures into permanent ones."""
    err = gitio.GitError(
        "git push -u origin non-fast-forward failed: fatal: unable to access: Could not resolve host",
        stderr="fatal: unable to access: Could not resolve host",
    )
    assert not gitio._is_permanent_push_error(err.stderr)


def test_archive_capture_and_search_shim_share_one_db(repo, monkeypatch, _isolated_memory_db):
    """The M5(1) required test: _capture_memory writes to the same
    DEFAULT_DB_PATH the search shim reads (F10 part 1 done-when)."""
    import sqlite3

    from chimera import cli, memory

    monkeypatch.chdir(repo)
    queue = Queue(root=repo)
    record = queue.create(make_spec("mem-path"), "ready", by="t")
    cli._capture_memory(queue, record)

    assert _isolated_memory_db == memory.DEFAULT_DB_PATH  # one canonical path
    conn = sqlite3.connect(_isolated_memory_db)
    row = conn.execute(
        "SELECT title, body FROM memories WHERE agent='chimera' AND title=?",
        (f"task_{record.spec.id}",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert "arc=graph" in row[1]


# ---------------------------------------------------------------------------
# Failed-arc L2 learning row (fail-open)
# ---------------------------------------------------------------------------


def test_failed_transition_writes_l2_row(repo, monkeypatch, capsys, _isolated_memory_db):
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "learned")
    tick_out = run(capsys, "tick")
    _null_scope(capsys, tid, tick_out)

    from chimera import arc_memory

    rows = arc_memory.arc_search(arc_kind="graph", arc_id=tid, db_path=_isolated_memory_db)
    assert len(rows) == 1
    assert rows[0]["tags"] == "terminal,failed"
    assert "arc FAILED" in rows[0]["body"]


def test_failed_transition_survives_memory_outage(repo, monkeypatch, capsys):
    """Reliability rider: the memory write is strictly fail-open — a raising
    memory layer must never block the failed transition (a jam inside the
    jam fix)."""
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "db-down")
    tick_out = run(capsys, "tick")

    def _boom(**kwargs):
        raise RuntimeError("memory.db is locked")

    monkeypatch.setattr("chimera.arc_memory.summarize_run", _boom)
    label = tick_out["pending_calls"][0]["label"]
    result = run(capsys, "arc", "submit", tid, label, "--null")
    assert result["state"] == "failed"
    assert Queue(root=repo).load(tid).state == "failed"


# ---------------------------------------------------------------------------
# F8: push failure-type classification + no double push
# ---------------------------------------------------------------------------


def test_permanent_push_error_markers():
    permanent = [
        "git push failed: fatal: 'origin' does not appear to be a git repository",
        "git push failed: remote: Repository not found.\nfatal: repository 'https://x/y.git/' not found",
        "git push failed: fatal: Authentication failed for 'https://x/y.git/'",
        "git push failed: fatal: could not read Username for 'https://x': terminal prompts disabled",
        "git push failed: ! [rejected] main -> main (non-fast-forward)",
        "git push failed: hint: Updates were rejected... 'git pull' fetch first",
    ]
    transient = [
        "git push failed: fatal: unable to access 'https://x/y.git/': Could not resolve host: x",
        "git push failed: fatal: the remote end hung up unexpectedly",
        "git push failed: error: RPC failed; HTTP 502",
    ]
    for msg in permanent:
        assert gitio._is_permanent_push_error(msg), msg
    for msg in transient:
        assert not gitio._is_permanent_push_error(msg), msg


def test_push_fast_fails_with_no_remote(repo: Path):
    subprocess.run(["git", "remote", "remove", "origin"], cwd=str(repo), check=True)
    started = time.monotonic()
    assert gitio.push(repo) is False
    # pre-F8 this took the full 2+4+8+16s backoff; fast-fail is sub-second
    assert time.monotonic() - started < 2


def test_push_fast_fails_on_non_fast_forward(repo: Path, tmp_path: Path):
    origin_url = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    other = tmp_path / "other-clone"
    subprocess.run(["git", "clone", origin_url, str(other)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "o@x"], cwd=str(other), check=True)
    subprocess.run(["git", "config", "user.name", "other"], cwd=str(other), check=True)
    (other / "won.txt").write_text("other session won the race\n", encoding="utf-8")
    subprocess.run(["git", "add", "won.txt"], cwd=str(other), check=True)
    subprocess.run(["git", "commit", "-m", "race winner"], cwd=str(other), check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=str(other), check=True, capture_output=True)

    (repo / "lost.txt").write_text("this clone lost the race\n", encoding="utf-8")
    gitio.commit(repo, [repo / "lost.txt"], "race loser")
    started = time.monotonic()
    assert gitio.push(repo) is False  # visible failure, no silent retry storm
    assert time.monotonic() - started < 2


def test_status_shows_push_health(repo, monkeypatch, capsys):
    """M6 rider: a push backlog is visible within one status call."""
    monkeypatch.chdir(repo)
    out = run(capsys, "status")
    assert out["push"]["upstream"] == "origin/main"
    assert out["push"]["unpushed_commits"] == 0

    (repo / "stranded.txt").write_text("committed but not pushed\n", encoding="utf-8")
    gitio.commit(repo, [repo / "stranded.txt"], "stranded commit")
    out = run(capsys, "status")
    assert out["push"]["unpushed_commits"] == 1
    assert "pushes may be failing" in out["push"]["note"]


def test_arc_submit_pushes_once(repo, monkeypatch, capsys):
    """F8 dedupe: an in-progress submit pays exactly one push (the checkpoint),
    not the checkpoint + a trailing unconditional push."""
    monkeypatch.chdir(repo)
    tid = _new_task(capsys, "one-push")
    tick_out = run(capsys, "tick")
    label = tick_out["pending_calls"][0]["label"]

    from tests.arc_drivers import _gr_plan_payload

    calls = []
    real_push = gitio.push

    def counting_push(root, branch=None):
        calls.append(root)
        return real_push(root, branch)

    monkeypatch.setattr("chimera.gitio.push", counting_push)
    run(capsys, "arc", "submit", tid, label, "--json", json.dumps(_gr_plan_payload()))
    assert len(calls) == 1

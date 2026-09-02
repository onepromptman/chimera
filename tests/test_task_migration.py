"""M2 remediation — tournament deletion with a one-shot record migration.

TaskSpec is extra="forbid", so a legacy task.json carrying the removed
`tournament` field fails load outright (no silent tolerate-and-drop —
the N3 amnesty rule forbids permanent shims). The failure names the fix;
`chimera migrate-tasks` strips removed fields once, validates BEFORE
writing, and commits.
"""

import json

import pytest

from chimera.cli import main
from chimera.queue import Queue, QueueError
from tests.test_failed_state import run


def _write_legacy_record(queue: Queue, tid: str) -> None:
    task_dir = queue.task_dir(tid)
    task_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "spec": {
            "id": tid,
            "slug": "legacy",
            "ask": "a pre-deletion task",
            "arc": "research",
            "tournament": False,  # the removed field
            "created": "2026-06-30T00:00:00Z",
        },
        "state": "ready",
        "history": [
            {"from_state": None, "to_state": "ready", "at": "2026-06-30T00:00:00Z", "by": "t"}
        ],
    }
    (task_dir / "task.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def test_legacy_record_fails_load_with_migration_pointer(repo):
    queue = Queue(root=repo)
    _write_legacy_record(queue, "20260630-legacy")
    with pytest.raises(QueueError, match="migrate-tasks"):
        queue.load("20260630-legacy")
    with pytest.raises(QueueError, match="migrate-tasks"):
        queue.list_tasks()


def test_migrate_tasks_strips_removed_fields_and_unblocks(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    queue = Queue(root=repo)
    _write_legacy_record(queue, "20260630-legacy")

    out = run(capsys, "migrate-tasks")
    assert out["migrated"] == ["20260630-legacy"]

    # status runs green over the migrated record; tick surfaces it as
    # retired-ready instead of crashing or claiming it
    status = run(capsys, "status")
    assert status["tasks"][0]["id"] == "20260630-legacy"
    tick = run(capsys, "tick")
    assert tick["action"] == "idle"
    assert tick["retired_ready"] == ["20260630-legacy"]

    # idempotent: a second run migrates nothing
    assert run(capsys, "migrate-tasks")["migrated"] == []


def test_tournament_flag_is_gone(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as exc:
        main(["new", "x", "--slug", "flagless", "--tournament"])
    assert exc.value.code == 2  # argparse: unrecognized argument


def test_running_retired_record_is_parked_failed_at_tick(repo, monkeypatch, capsys):
    """A retired-arc record found `running` is parked failed at
    tick, loudly — this pins the claim to a test (audit SN-6)."""
    monkeypatch.chdir(repo)
    queue = Queue(root=repo)
    tid = "20260630-stuck"
    task_dir = queue.task_dir(tid)
    task_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "spec": {
            "id": tid,
            "slug": "stuck",
            "ask": "an in-flight pre-v7 task",
            "arc": "research",
            "created": "2026-06-30T00:00:00Z",
        },
        "state": "running",
        "history": [
            {"from_state": None, "to_state": "ready", "at": "2026-06-30T00:00:00Z", "by": "t"},
            {"from_state": "ready", "to_state": "running", "at": "2026-06-30T00:01:00Z", "by": "t"},
        ],
    }
    (task_dir / "task.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    tick = run(capsys, "tick")
    assert tick["action"] == "idle"
    assert tick["failed_tasks"] == [tid]
    assert queue.load(tid).state == "failed"

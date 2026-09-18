"""Queue state machine: legal transitions, done-only-via-gate, claim semantics."""

import json
import subprocess
from typing import get_args

import pytest

from chimera.models import RETIRED_ARCS, CriticOpinion, TaskSpec, TaskState, VerifyResult
from chimera.queue import (
    LEGAL_TRANSITIONS,
    REMOVED_SPEC_FIELDS,
    IllegalTransition,
    Queue,
    QueueError,
    VerifyGateError,
    next_runnable,
    retired_ready,
    tick_lock,
)


def make_spec(slug: str = "demo-task") -> TaskSpec:
    return TaskSpec(
        id=f"20260610-{slug}",
        slug=slug,
        ask="demo ask",
        arc="graph",
    )


def passing_verification() -> VerifyResult:
    ops = [CriticOpinion(refuted=False, reason="holds")] * 3
    return VerifyResult(
        mode="lite",
        passed=True,
        maker_model="opus",
        critic_model="sonnet",
        opinions=ops,
        valid_critic_count=3,
        unrefuted_count=3,
    )


def failing_verification() -> VerifyResult:
    ops = [CriticOpinion(refuted=True, reason="refuted", severity="fatal")] * 3
    return VerifyResult(
        mode="lite",
        passed=False,
        maker_model="opus",
        critic_model="sonnet",
        opinions=ops,
        valid_critic_count=3,
        unrefuted_count=0,
    )


def test_repo_fixture_is_a_private_copy_of_the_session_template(repo, tmp_path):
    """Hermeticity of the copied fixture (conftest._repo_template).

    Every test gets its own copy of the work repo AND of the bare origin, with
    the remote repointed at its own copy. If the URL ever pointed back at the
    shared template, one test's push would be visible to the next — the whole
    reason the suite builds a throwaway repo in the first place."""
    url = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()
    assert url == str(tmp_path / "origin.git")
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=str(repo), capture_output=True, text=True
    ).stdout
    assert len(log.splitlines()) == 1  # a pristine copy: only the template's init commit
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True
    ).stdout
    assert status == ""


def test_create_commits(queue: Queue):
    queue.create(make_spec(), "ready", by="test")
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=str(queue.root), capture_output=True, text=True
    ).stdout
    assert "create [ready]" in log


def test_create_duplicate_refused(queue: Queue):
    queue.create(make_spec(), "ready", by="test")
    with pytest.raises(QueueError, match="already exists"):
        queue.create(make_spec(), "ready", by="test")


def test_claim_moves_to_running_single_flight(queue: Queue):
    record = queue.create(make_spec(), "ready", by="test")
    claimed = queue.claim(record.spec.id, "worker-1")
    assert claimed.state == "running"
    assert claimed.claimed_by == "worker-1"
    with pytest.raises(QueueError, match="not ready"):
        queue.claim(record.spec.id, "worker-2")


def test_illegal_transitions_rejected(queue: Queue):
    record = queue.create(make_spec(), "ready", by="test")
    tid = record.spec.id
    for bad in ("awaiting-signoff", "done", "archived"):
        with pytest.raises(IllegalTransition):
            queue.transition(tid, bad, by="test")


# ---------------------------------------------------------------------------
# Absence tests (Rule 6, D3 handoff item 3): the one conditional edge
# (ready -> archived for a retired arc, P1-11) is gone outright — the queue
# is left with 6 states and zero conditional edges. A stranded retired-arc
# `ready` record now has no queue-level exit; re-opening the ask as a fresh
# graph task is the only path.
# ---------------------------------------------------------------------------


def test_no_conditional_transitions_remain():
    from chimera import queue as queue_mod

    assert not hasattr(queue_mod, "CONDITIONAL_TRANSITIONS")


def test_awaiting_input_is_gone_from_state_and_transitions():
    assert "awaiting-input" not in get_args(TaskState)
    assert "awaiting-input" not in LEGAL_TRANSITIONS
    for targets in LEGAL_TRANSITIONS.values():
        assert "awaiting-input" not in targets


def test_retired_arc_ready_record_has_no_archive_exit(queue: Queue):
    """A pre-ADR-010 record left `ready` has no CLI exit at all (again, as
    before P1-11 introduced the now-removed conditional edge): next_runnable
    skips it, abandon needs `running`, archive needs `done|failed`."""
    spec = TaskSpec(id="20260610-stranded", slug="stranded", ask="a", arc="research")
    tid = queue.create(spec, "ready", by="test").spec.id
    with pytest.raises(IllegalTransition, match="ready -> archived"):
        queue.transition(tid, "archived", by="test")


# ---------------------------------------------------------------------------
# "Retired arcs load, never dispatch" (CLAUDE.md invariant; G4, handoff item
# 3 opus review — this queue-level coverage was lost entirely when the
# conditional archive edge above was removed, and nothing else pinned it).
# ---------------------------------------------------------------------------


def test_next_runnable_skips_a_ready_record_whose_arc_is_retired(queue: Queue):
    """A retired-arc record sorts FIRST by id (oldest), so if next_runnable
    did not filter on RETIRED_ARCS it would wrongly hand this one out."""
    retired = queue.create(
        TaskSpec(id="20260101-retired", slug="retired", ask="a", arc="research"),
        "ready",
        by="test",
    )
    live = queue.create(
        TaskSpec(id="20260610-live", slug="live", ask="a", arc="graph"),
        "ready",
        by="test",
    )
    assert retired.spec.arc in RETIRED_ARCS
    assert live.spec.arc not in RETIRED_ARCS

    runnable = next_runnable(queue)
    assert runnable is not None
    assert runnable.spec.id == live.spec.id


def test_retired_ready_surfaces_exactly_the_stranded_record(queue: Queue):
    retired = queue.create(
        TaskSpec(id="20260101-retired2", slug="retired2", ask="a", arc="research"),
        "ready",
        by="test",
    )
    queue.create(
        TaskSpec(id="20260610-live2", slug="live2", ask="a", arc="graph"),
        "ready",
        by="test",
    )

    assert retired_ready(queue) == [retired.spec.id]


def test_arc_for_refuses_to_dispatch_a_retired_arc(queue: Queue):
    """cli._arc_for is the actual dispatch refusal every tick claim runs
    through; pinned directly so the invariant is covered without driving a
    full tick."""
    from chimera.cli import _arc_for

    tid = queue.create(
        TaskSpec(id="20260101-retired3", slug="retired3", ask="a", arc="research"),
        "ready",
        by="test",
    ).spec.id

    with pytest.raises(QueueError, match="retired"):
        _arc_for(queue, tid)


# ---------------------------------------------------------------------------
# Legacy-record migration machinery (G1, handoff item 3 opus review):
# `queue.migrate_task_records()` was kept on a false "live consumers" claim
# after `chimera migrate-tasks` (the CLI verb) and its test file were both
# deleted. It is genuinely still needed — an unmigrated legacy record wedges
# EVERY queue verb (load, list_tasks, ...) — so the unwedge path has to work.
# ---------------------------------------------------------------------------


def _write_legacy_task_json(queue: Queue, tid: str, field: str, value: object) -> None:
    """A raw task.json carrying a spec field TaskSpec no longer has — the
    real shape a pre-deletion record takes on disk, not something built
    through TaskSpec (which would refuse it outright, extra="forbid")."""
    task_dir = queue.task_dir(tid)
    task_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "spec": {
            "id": tid,
            "slug": "legacy",
            "ask": "a pre-deletion task",
            "arc": "research",
            "created": "2026-06-30T00:00:00Z",
            field: value,
        },
        "state": "ready",
        "history": [
            {"from_state": None, "to_state": "ready", "at": "2026-06-30T00:00:00Z", "by": "t"}
        ],
    }
    (task_dir / "task.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def test_migrate_task_records_unblocks_a_legacy_record(queue: Queue):
    assert "tournament" in REMOVED_SPEC_FIELDS  # the field this test exercises
    tid = "20260630-legacy"
    _write_legacy_task_json(queue, tid, "tournament", False)

    with pytest.raises(QueueError, match="Queue.migrate_task_records"):
        queue.load(tid)

    assert queue.migrate_task_records() == [tid]

    record = queue.load(tid)
    assert record.spec.id == tid
    assert record.state == "ready"


def test_done_requires_verification_file(queue: Queue):
    tid = queue.create(make_spec(), "ready", by="test").spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "awaiting-signoff", by="w")
    queue.record_approval(tid, by="operator")
    with pytest.raises(VerifyGateError, match="no verification.json"):
        queue.transition(tid, "done", by="w")


def test_done_requires_passing_verification(queue: Queue):
    tid = queue.create(make_spec(), "ready", by="test").spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "awaiting-signoff", by="w")
    queue.record_approval(tid, by="operator")
    queue.record_verification(tid, failing_verification())
    with pytest.raises(VerifyGateError, match="verification failed"):
        queue.transition(tid, "done", by="w")


def test_done_requires_g2_approval(queue: Queue):
    tid = queue.create(make_spec(), "ready", by="test").spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "awaiting-signoff", by="w")
    queue.record_verification(tid, passing_verification())
    with pytest.raises(VerifyGateError, match="no G2 approval"):
        queue.transition(tid, "done", by="w")


def test_full_lifecycle_to_archived(queue: Queue):
    tid = queue.create(make_spec(), "ready", by="test").spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "awaiting-signoff", by="w")
    queue.record_verification(tid, passing_verification())
    queue.record_approval(tid, by="operator")
    record = queue.transition(tid, "done", by="operator")
    assert record.state == "done"
    record = queue.transition(tid, "archived", by="w")
    assert record.state == "archived"
    with pytest.raises(IllegalTransition):
        queue.transition(tid, "done", by="w")  # archived is terminal


def test_g2_rework_path(queue: Queue):
    tid = queue.create(make_spec(), "ready", by="test").spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "awaiting-signoff", by="w")
    record = queue.transition(tid, "running", by="operator", note="rework")
    assert record.state == "running"


def test_history_is_audit_trail(queue: Queue):
    tid = queue.create(make_spec(), "ready", by="test").spec.id
    queue.claim(tid, "w")
    record = queue.transition(tid, "awaiting-signoff", by="w")
    states = [(t.from_state, t.to_state) for t in record.history]
    assert states == [
        (None, "ready"),
        ("ready", "running"),
        ("running", "awaiting-signoff"),
    ]


def _commit_count(queue: Queue) -> int:
    out = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=str(queue.root),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return int(out.strip())


def test_every_transition_commits(queue: Queue):
    """The CLAUDE.md queue invariant, pinned: EVERY transition is a git commit
    (durable state first). Counted around each of the three shapes a task
    moves through — a claim, a plain transition, and the archive — so a write
    that stops committing fails here instead of silently losing the audit
    trail on a container that dies mid-run."""
    tid = queue.create(make_spec(), "ready", by="test").spec.id

    before = _commit_count(queue)
    queue.claim(tid, "w")
    assert _commit_count(queue) == before + 1, "claim did not commit"

    before = _commit_count(queue)
    queue.transition(tid, "awaiting-signoff", by="w")
    assert _commit_count(queue) == before + 1, "ready -> awaiting-signoff did not commit"

    queue.record_verification(tid, passing_verification())
    queue.record_approval(tid, by="operator")
    queue.transition(tid, "done", by="operator")

    before = _commit_count(queue)
    queue.transition(tid, "archived", by="w")
    assert _commit_count(queue) == before + 1, "archive did not commit"


def test_tick_lock_excludes_second_tick(queue: Queue):
    with tick_lock(queue.root):
        with pytest.raises(QueueError, match="another tick"):
            with tick_lock(queue.root):
                pass


def test_wait_mode_times_out_on_a_stuck_holder(queue: Queue, monkeypatch):
    """The wait mode is a BOUNDED poll on every platform (defect B
    prerequisite): a live-but-stuck holder must not block a submit — or an
    operator's approve — forever. The bound is one constant, shortened here."""
    import time

    from chimera import queue as queue_mod

    monkeypatch.setattr(queue_mod, "LOCK_WAIT_S", 0.3)
    with tick_lock(queue.root):
        started = time.monotonic()
        with pytest.raises(QueueError, match="timed out"):
            with tick_lock(queue.root, wait=True):
                pass
        assert time.monotonic() - started < 5

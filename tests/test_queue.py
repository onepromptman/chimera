"""Queue state machine: legal transitions, done-only-via-gate, claim semantics."""

import subprocess

import pytest

from chimera.models import CriticOpinion, TaskSpec, VerifyResult
from chimera.queue import IllegalTransition, Queue, QueueError, VerifyGateError, tick_lock


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
    ops = [CriticOpinion(refuted=True, reason="refuted")] * 3
    return VerifyResult(
        mode="lite",
        passed=False,
        maker_model="opus",
        critic_model="sonnet",
        opinions=ops,
        valid_critic_count=3,
        unrefuted_count=0,
    )


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


def test_tick_lock_excludes_second_tick(queue: Queue):
    with tick_lock(queue.root):
        with pytest.raises(QueueError, match="another tick"):
            with tick_lock(queue.root):
                pass

"""G1 intake (single-path) and G2 (operator approve)."""

import inspect

import pytest

from chimera import gates, models
from chimera.gates import g1_intake, g2_approve
from chimera.queue import Queue, QueueError


def test_proceed_top_creates_ready_task(queue: Queue):
    records = g1_intake(
        queue,
        ask="compare e-bike options",
        arc="research",
        by="test",
    )
    assert len(records) == 1
    assert records[0].state == "ready"


def test_g1_intake_has_no_outcome_parameter():
    """Absence test (Rule 6, G2 handoff item 3 review): the parallel-ab
    branch was dead surface — unreachable from the CLI once `--outcome` was
    removed wholesale. `g1_intake` is single-path now: `outcome` is not a
    parameter, `SocraticOutcome` no longer exists as a type to route
    through it, and passing the old kwarg fails loudly instead of silently
    minting a second task."""
    assert "outcome" not in inspect.signature(gates.g1_intake).parameters
    assert not hasattr(models, "SocraticOutcome")
    with pytest.raises(TypeError, match="outcome"):
        g1_intake(None, ask="plan the trip", arc="research", by="test", outcome="parallel-ab")


def test_g2_approve_blocked_without_verification(queue: Queue):
    records = g1_intake(
        queue,
        ask="clean ask",
        arc="research",
        by="test",
    )
    tid = records[0].spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "awaiting-signoff", by="w")
    with pytest.raises(QueueError, match="no verification"):
        g2_approve(queue, tid, by="operator")


def test_gates_module_has_no_ask_once_surface():
    """Absence test (Rule 6): the G1 ask/park branch is gone — AskOnceViolation,
    write_questions/read_questions/g1_answer, and the questions.yaml
    constant no longer exist on the module (D1, handoff item 3)."""
    for name in (
        "AskOnceViolation",
        "write_questions",
        "read_questions",
        "g1_answer",
        "QUESTIONS_FILE",
        "IntakeQuestion",
        "IntakeQuestions",
    ):
        assert not hasattr(gates, name), f"gates.{name} should have been deleted"

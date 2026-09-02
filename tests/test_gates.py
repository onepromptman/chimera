"""G1/G2: ask-once invariant, answer round-trip, parallel-ab."""

import pytest

from chimera.gates import (
    AskOnceViolation,
    g1_answer,
    g1_intake,
    g2_approve,
    read_questions,
    write_questions,
)
from chimera.models import IntakeQuestion, IntakeQuestions
from chimera.queue import Queue, QueueError


def test_proceed_top_creates_ready_task(queue: Queue):
    records = g1_intake(
        queue, ask="compare e-bike options", arc="research",
        outcome="proceed-top", by="test",
    )
    assert len(records) == 1
    assert records[0].state == "ready"


def test_parallel_ab_mints_two_slugs(queue: Queue):
    records = g1_intake(
        queue, ask="plan the trip", arc="research",
        outcome="parallel-ab", by="test",
    )
    assert [r.spec.slug for r in records] == ["plan-the-trip-a", "plan-the-trip-b"]
    assert all(r.state == "ready" for r in records)


def test_ask_outcome_parks_with_questions(queue: Queue):
    records = g1_intake(
        queue, ask="research something vague", arc="research",
        outcome="ask", by="test",
        questions=["What budget?", "What timeline?"],
    )
    record = records[0]
    assert record.state == "awaiting-input"
    qs = read_questions(queue, record.spec.id)
    assert [q.question for q in qs.questions] == ["What budget?", "What timeline?"]


def test_ask_once_reemission_refused(queue: Queue):
    records = g1_intake(
        queue, ask="vague ask", arc="research",
        outcome="ask", by="test", questions=["Q1?"],
    )
    tid = records[0].spec.id
    with pytest.raises(AskOnceViolation, match="asked ONCE"):
        write_questions(
            queue, tid,
            IntakeQuestions(task_id=tid, questions=[IntakeQuestion(id="q1", question="again?")]),
        )


def test_answer_unparks_only_when_complete(queue: Queue):
    records = g1_intake(
        queue, ask="vague ask", arc="research",
        outcome="ask", by="test", questions=["What budget?", "What timeline?"],
    )
    tid = records[0].spec.id
    with pytest.raises(QueueError, match="unanswered"):
        g1_answer(queue, tid, {"q1": "under $2k"}, by="test")
    record = g1_answer(queue, tid, {"q1": "under $2k", "q2": "by July"}, by="test")
    assert record.state == "ready"
    qs = read_questions(queue, tid)
    assert qs.questions[0].answer == "under $2k"


def test_questions_roundtrip_with_awkward_strings(queue: Queue):
    records = g1_intake(
        queue, ask="quoting test", arc="research",
        outcome="ask", by="test",
        questions=['Bud "get"? — em-dash: colon, [brackets]'],
    )
    qs = read_questions(queue, records[0].spec.id)
    assert qs.questions[0].question == 'Bud "get"? — em-dash: colon, [brackets]'


def test_g2_approve_blocked_without_verification(queue: Queue):
    records = g1_intake(
        queue, ask="clean ask", arc="research",
        outcome="proceed-top", by="test",
    )
    tid = records[0].spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "awaiting-signoff", by="w")
    with pytest.raises(QueueError, match="no verification"):
        g2_approve(queue, tid, by="operator")

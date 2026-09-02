"""The exactly-two blocking human gates.

G1 — intake-once: Socratic three-outcome branch. ASK writes questions.yaml
     ONCE and parks the task; re-emission is refused while questions.yaml
     exists (ask-once invariant: ask once, persist immediately, park — never
     loop). Answers arrive as an Issue comment or `chimera answer`.

G2 — final sign-off: `/approve` on the task's Issue (or `chimera approve`).
     Approval is recorded, then queue.transition() runs the verify gate on
     the way to done. Everything between G1 and G2 is async digest.

Publish boundary: the framework carries no per-task sensitivity policy and
makes no judgment about what your content is. Protection is structural and
path-shaped — the gitignored private zones — not a runtime gate. A local
pre-push hook is a sensible second layer and is deliberately not part of the
framework (see CLAUDE.md Security Rule 1).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    IntakeQuestion,
    IntakeQuestions,
    SocraticOutcome,
    TaskRecord,
    TaskSpec,
)
from .queue import Queue, QueueError, slugify

QUESTIONS_FILE = "questions.yaml"


class AskOnceViolation(QueueError):
    pass


def _task_id(slug: str) -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%d')}-{slug}"


def g1_intake(
    queue: Queue,
    ask: str,
    outcome: SocraticOutcome,
    by: str,
    questions: list[str] | None = None,
    context: str | None = None,
    slug: str | None = None,
    arc: str = "graph",
    shape: str | None = None,
) -> list[TaskRecord]:
    """Open task(s) from one intake. Returns 1 task (proceed-top/ask) or 2
    (parallel-ab).

    One door: every task is a graph task — the planner node decides
    the shape, so intake carries no per-arc flags. `arc` stays a parameter
    only so tests can exercise the retired-arc refusal in the dispatcher.
    """
    base_slug = slug or slugify(ask)

    if outcome == "parallel-ab":
        records = []
        for suffix in ("a", "b"):
            spec = TaskSpec(
                id=_task_id(f"{base_slug}-{suffix}"),
                slug=f"{base_slug}-{suffix}",
                ask=ask,
                arc=arc,  # type: ignore[arg-type]
                context=context,
                shape=shape,  # type: ignore[arg-type]
            )
            records.append(queue.create(spec, "ready", by=by))
        return records

    spec = TaskSpec(
        id=_task_id(base_slug),
        slug=base_slug,
        ask=ask,
        arc=arc,  # type: ignore[arg-type]
        context=context,
        shape=shape,  # type: ignore[arg-type]
    )

    if outcome == "ask":
        if not questions:
            raise QueueError("outcome=ask requires at least one question")
        record = queue.create(spec, "awaiting-input", by=by)
        write_questions(
            queue,
            spec.id,
            IntakeQuestions(
                task_id=spec.id,
                questions=[
                    IntakeQuestion(id=f"q{i+1}", question=q)
                    for i, q in enumerate(questions)
                ],
            ),
        )
        return [record]

    return [queue.create(spec, "ready", by=by)]


# ---------------------------------------------------------------------------
# questions.yaml — ask-once invariant
# ---------------------------------------------------------------------------
# Self-authored constrained YAML (minimal-dependency posture: no PyYAML). String values are
# JSON-quoted, which is valid YAML and round-trips safely.


def _questions_path(queue: Queue, task_id: str) -> Path:
    return queue.task_dir(task_id) / QUESTIONS_FILE


def write_questions(queue: Queue, task_id: str, qs: IntakeQuestions) -> None:
    path = _questions_path(queue, task_id)
    if path.exists():
        raise AskOnceViolation(
            f"{task_id}: questions.yaml already exists — questions are asked ONCE; "
            "answer the existing ones (chimera answer) instead of re-emitting"
        )
    lines = [
        f"task_id: {json.dumps(qs.task_id)}",
        f"posted_at: {json.dumps(qs.posted_at)}",
        "questions:",
    ]
    for q in qs.questions:
        lines.append(f"  - id: {json.dumps(q.id)}")
        lines.append(f"    question: {json.dumps(q.question)}")
        lines.append(f"    answer: {json.dumps(q.answer)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    from . import gitio

    gitio.commit(queue.root, [path], f"chimera({task_id}): G1 questions posted (ask-once)")


def read_questions(queue: Queue, task_id: str) -> IntakeQuestions:
    path = _questions_path(queue, task_id)
    if not path.exists():
        raise QueueError(f"{task_id}: no questions.yaml")
    data: dict = {"questions": []}
    current: dict | None = None

    def _parse(raw_value: str) -> object:
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise QueueError(
                f"malformed questions.yaml at {path}: {exc}"
            ) from exc

    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("task_id:"):
            data["task_id"] = _parse(raw.split(":", 1)[1].strip())
        elif raw.startswith("posted_at:"):
            data["posted_at"] = _parse(raw.split(":", 1)[1].strip())
        elif raw.strip().startswith("- id:"):
            current = {"id": _parse(raw.split(":", 1)[1].strip())}
            data["questions"].append(current)
        elif current is not None and raw.strip().startswith("question:"):
            current["question"] = _parse(raw.split(":", 1)[1].strip())
        elif current is not None and raw.strip().startswith("answer:"):
            current["answer"] = _parse(raw.split(":", 1)[1].strip())
    return IntakeQuestions.model_validate(data)


def g1_answer(
    queue: Queue, task_id: str, answers: dict[str, str], by: str
) -> TaskRecord:
    """Record answers and unpark the task. Unanswered questions stay blocking."""
    qs = read_questions(queue, task_id)
    for q in qs.questions:
        if q.id in answers:
            q.answer = answers[q.id]
    unanswered = [q.id for q in qs.questions if not q.answer.strip()]
    if unanswered:
        raise QueueError(f"{task_id}: unanswered questions {unanswered} — still parked")
    # rewrite in place (answers update is the one sanctioned mutation).
    # Write directly instead of via write_questions() so the commit message
    # reflects an answer update, not a question post (audit-trail correctness).
    path = _questions_path(queue, task_id)
    lines = [
        f"task_id: {json.dumps(qs.task_id)}",
        f"posted_at: {json.dumps(qs.posted_at)}",
        "questions:",
    ]
    for q in qs.questions:
        lines.append(f"  - id: {json.dumps(q.id)}")
        lines.append(f"    question: {json.dumps(q.question)}")
        lines.append(f"    answer: {json.dumps(q.answer)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    from . import gitio

    gitio.commit(queue.root, [path], f"chimera({task_id}): G1 answers recorded")
    return queue.transition(task_id, "ready", by=by, note="G1 answers received")


# ---------------------------------------------------------------------------
# G2
# ---------------------------------------------------------------------------


def g2_approve(queue: Queue, task_id: str, by: str) -> TaskRecord:
    """Final sign-off: record approval, then transition through the verify gate."""
    queue.record_approval(task_id, by=by)
    return queue.transition(task_id, "done", by=by, note="G2 approved")


def g2_reject(queue: Queue, task_id: str, by: str, note: str) -> TaskRecord:
    return queue.transition(task_id, "running", by=by, note=f"G2 rework: {note}")

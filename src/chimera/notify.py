"""Issue-thread notification payloads.

The async surface is ONE GitHub Issue per task: G1 questions, digest notes
(confidence flags, critic splits), and the G2 sign-off request all ride that
single thread. This module only BUILDS bodies; the driving session does the
actual posting with its GitHub MCP tools and dedups by scanning the Issue
thread before posting — the package holds no tokens (security rule: OAuth
stays with the session). Post-once ledger deleted 2026-07-14 — audit F3
found it unwired; the driving session dedups by scanning the Issue thread.
"""

from __future__ import annotations

from .models import IntakeQuestions, TaskRecord, VerifyResult


def issue_title(record: TaskRecord) -> str:
    return f"[chimera] {record.spec.id}: {record.spec.ask[:80]}"


def issue_body(record: TaskRecord) -> str:
    s = record.spec
    return (
        f"Chimera task `{s.id}` — arc `{s.arc}`, lite "
        f"verification.\n\n**Ask:** {s.ask}\n\n"
        "This single Issue is the task's async surface: G1 questions, digest "
        "notes, and the G2 sign-off all happen here. Comment `/approve` to "
        "sign off once the task reaches awaiting-signoff."
    )


def questions_comment(qs: IntakeQuestions) -> str:
    lines = [
        "**G1 — input needed (asked once; the task is parked until answered):**",
        "",
    ]
    for q in qs.questions:
        lines.append(f"- **{q.id}**: {q.question}")
    lines += [
        "",
        "Reply with answers inline (`q1: ...`) or run "
        f"`chimera answer {qs.task_id} --q1 \"...\"`.",
    ]
    return "\n".join(lines)


def signoff_comment(record: TaskRecord, verdict: VerifyResult, artifact_rel: str) -> str:
    status = "PASSED" if verdict.passed else "FAILED"
    lines = [
        f"**G2 — sign-off requested** for `{record.spec.id}`.",
        "",
        f"- artifact: `{artifact_rel}`",
        f"- verification ({verdict.mode}): **{status}** "
        f"({verdict.unrefuted_count}/{verdict.valid_critic_count} critics unrefuted; "
        f"maker={verdict.maker_model}, critics={verdict.critic_model})",
        "",
        "Comment `/approve` to complete, or describe rework to send it back.",
    ]
    if not verdict.passed:
        lines.insert(3, "- ⚠ verification failed — `done` is blocked until a re-run passes")
    return "\n".join(lines)


def digest_comment(flags: list[str]) -> str:
    if not flags:
        return "Digest: no flags — arc proceeding clean."
    return "Digest flags:\n" + "\n".join(f"- ⚠ {f}" for f in flags)

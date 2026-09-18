"""Issue-thread notification payloads.

The async surface is ONE GitHub Issue per task: digest notes (confidence
flags, critic splits) and the G2 sign-off request ride that single thread.
This module only BUILDS bodies; the driving session does the
actual posting with its GitHub MCP tools and dedups by scanning the Issue
thread before posting — the package holds no tokens (security rule: OAuth
stays with the session). Post-once ledger deleted 2026-07-14 — audit F3
found it unwired; the driving session dedups by scanning the Issue thread.
"""

from __future__ import annotations

from .models import TaskRecord, VerifyResult

# One plain-English clause per credential value. The vocabulary is fixed on
# purpose: a summary line that says more than the layer beneath it (a
# "verified" where the state was "one control, uncaught") is exactly the
# laundering C15 forbids.
_CREDENTIAL_GLOSS = {
    "panel-uncredentialed": "no seeded-mutant control ran on this run",
    "mutant-caught": "seeded defect caught by the panel — a floor, not a certificate",
    "mutant-missed": "the panel did not catch a defect chimera seeded — a flag, not a block",
    "mutant-not-measured": "the control did not return a usable answer",
}


def issue_title(record: TaskRecord) -> str:
    return f"[chimera] {record.spec.id}: {record.spec.ask[:80]}"


def issue_body(record: TaskRecord) -> str:
    s = record.spec
    return (
        f"Chimera task `{s.id}` — arc `{s.arc}`, lite "
        f"verification.\n\n**Ask:** {s.ask}\n\n"
        "This single Issue is the task's async surface: digest notes and the "
        "G2 sign-off all happen here. Comment `/approve` to sign off once the "
        "task reaches awaiting-signoff."
    )


def signoff_comment(record: TaskRecord, verdict: VerifyResult, artifact_rel: str) -> str:
    status = "PASSED" if verdict.passed else "FAILED"
    done = record.state == "done"
    header = "**Done — verification passed**" if done else "**G2 — sign-off requested**"
    footer = (
        "Review at your convenience; `chimera reject <id>` reopens it for rework."
        if done
        else "Comment `/approve` to complete, or describe rework to send it back."
    )
    lines = [
        f"{header} for `{record.spec.id}`.",
        "",
        f"- artifact: `{artifact_rel}`",
        f"- verification ({verdict.mode}): **{status}** "
        f"({verdict.unrefuted_count}/{verdict.valid_critic_count} critics unrefuted; "
        f"maker={verdict.maker_model}, critics={verdict.critic_model})",
        # The panel's own credential rides the same comment as its verdict: an
        # auto-approved PASS must disclose whether anything measured the panel
        # at all. "caught", never "verified" — a caught mutant is a
        # floor-raiser, not a certificate, and it gates nothing (C15).
        f"- panel credential: {verdict.panel_credential} ({_CREDENTIAL_GLOSS[verdict.panel_credential]})",
        "",
        footer,
    ]
    if not verdict.passed:
        lines.insert(3, "- ⚠ verification failed — `done` is blocked until a re-run passes")
    if verdict.open_findings:
        # a passing panel can still carry material/cosmetic refutations
        # (fatal-only veto) — surface them here so an auto-approved PASS
        # never hides what the panel actually found.
        lines.append("")
        lines.append("**Open findings:**")
        lines.extend(f"- {finding}" for finding in verdict.open_findings)
    return "\n".join(lines)

"""Async digest — the surface between G1 and G2.

One durable rollup per day at digest/YYYY-MM-DD.md, committed; the same
notes are mirrored to each task's single Issue thread by the driving
session (notify.py builds the bodies). Flags: any step/synthesis below
CONFIDENCE_FLAG_THRESHOLD, critic splits, verification failures, tasks
awaiting sign-off.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .arcs._common import ARC_STATE_FILE
from .models import CONFIDENCE_FLAG_THRESHOLD, TaskRecord, VerifyResult
from .queue import Queue
from .verify import mutants

DIGEST_DIR = "digest"


def _credential_flag(queue: Queue, record: TaskRecord, verdict: VerifyResult) -> str:
    """What this verdict's OWN control measured — stated every time.

    A verdict that does not carry its own limitations is read as a warranty,
    so an uncredentialed run says so out loud rather than staying silent. The
    wording is "caught", never "verified": a caught mutant says the panel
    caught a SEEDED defect of a SEARCHED class, and nothing about the defects
    nobody seeded. A MISSED control is a ⚠ flag on the digest and nothing
    more — it blocks no transition (C15: the credential never gates).
    """
    if verdict.panel_credential == "panel-uncredentialed":
        return "panel credential: none — no seeded-mutant control ran on this verdict"
    defect = verdict.mutant.defect if verdict.mutant is not None else None
    detail = f" ({defect.operator})" if defect is not None else ""
    try:  # fail-open: an unreadable queue costs the aggregate, not the digest
        caught, measured = mutants.catch_rate(queue.task_dir(record.spec.id).parent)
        rate = f"catch rate {caught}/{measured}"
    except Exception:
        rate = "catch rate unavailable"
    if verdict.panel_credential == "mutant-caught":
        return f"panel credential: seeded mutant CAUGHT{detail} — {rate}"
    if verdict.panel_credential == "mutant-missed":
        return (
            f"⚠ panel credential: seeded mutant MISSED{detail} — the panel did not "
            f"catch a defect chimera seeded; {rate}"
        )
    note = (verdict.mutant.note if verdict.mutant is not None else None) or "no reason recorded"
    return f"panel credential: seeded mutant not measured ({note})"


def _arc_flags(queue: Queue, record: TaskRecord) -> list[str]:
    flags: list[str] = []
    # M5(3) rider: surface a degraded priors seed-read, fail-open. Plain-JSON
    # read so a missing/odd arc-state file never breaks the digest.
    try:
        state_path = queue.task_dir(record.spec.id) / ARC_STATE_FILE
        if state_path.exists():
            arc_state = json.loads(state_path.read_text(encoding="utf-8"))
            priors_flag = (arc_state.get("priors") or {}).get("flag")
            if priors_flag:
                flags.append(priors_flag)
            # graph arc: every node output carries confidence; below the
            # threshold it rides the digest (80/20 — flag, never block).
            for node_id, out in (arc_state.get("outputs") or {}).items():
                if out and out.get("confidence", 100) < CONFIDENCE_FLAG_THRESHOLD:
                    flags.append(
                        f"low confidence ({out['confidence']}) on `{node_id}` — review before relying on it"
                    )
                if out and str(out.get("recommendation", "")).startswith("PAUSE"):
                    flags.append(
                        f"PAUSE recommendation on `{node_id}` — the node asked to surface this for review"
                    )
    except Exception:
        pass
    # (the v6 research arc's result.json reader lived here; no live arc writes
    # that file — deleted in the 2026-08-28 optimization batch, opus #11)
    vpath = queue.verification_path(record.spec.id)
    if vpath.exists():
        verdict = VerifyResult.model_validate_json(vpath.read_text(encoding="utf-8"))
        if verdict.passed:
            # never the bare word "verified" — a passing panel REFUTE-attempted
            # and survived; it did not certify the artifact correct.
            flags.append(
                f"refutation-attempted and survived "
                f"({verdict.unrefuted_count}/{verdict.valid_critic_count} unrefuted)"
            )
        else:
            flags.append(
                f"VERIFICATION FAILED ({verdict.unrefuted_count}/{verdict.valid_critic_count} unrefuted) — done is blocked"
            )
            # the veto's reason is the one line the operator must see without
            # opening verification.json — open_findings excludes fatal by design
            for o in verdict.opinions:
                if o.refuted and o.severity == "fatal":
                    flags.append(f"Fatal refutation: {o.reason}")
        if verdict.open_findings:
            findings = "\n".join(f"  - {finding}" for finding in verdict.open_findings)
            flags.append(f"Open findings:\n{findings}")
        flags.append(_credential_flag(queue, record, verdict))
    return flags


def render(queue: Queue) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [f"# chimera digest — {today}", ""]
    tasks = queue.list_tasks()
    if not tasks:
        lines.append("No tasks in the queue.")
        return "\n".join(lines) + "\n"
    by_state: dict[str, list[TaskRecord]] = {}
    for t in tasks:
        by_state.setdefault(t.state, []).append(t)
    for state in (
        "failed",
        "awaiting-signoff",
        "running",
        "ready",
        "done",
        "archived",
    ):
        records = by_state.get(state)
        if not records:
            continue
        lines.append(f"## {state} ({len(records)})")
        lines.append("")
        for r in records:
            lines.append(f"### {r.spec.id} — {r.spec.ask}")
            if state == "failed":
                note = next(
                    (t.note for t in reversed(r.history) if t.to_state == "failed" and t.note),
                    None,
                )
                if note:
                    lines.append(f"- ⚠ {note}")
                lines.append(
                    f"- ACTION: `chimera retry {r.spec.id}` for a fresh arc start, "
                    f"or `chimera archive {r.spec.id}` to retire it"
                )
            if state == "awaiting-signoff":
                lines.append("- ACTION: review artifacts + `/approve` on the Issue (G2)")
            for flag in _arc_flags(queue, r):
                lines.append(f"- ⚠ {flag}")
            lines.append("")
    return "\n".join(lines) + "\n"


def write(queue: Queue) -> Path:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    path = queue.root / DIGEST_DIR / f"{today}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(queue), encoding="utf-8")
    return path

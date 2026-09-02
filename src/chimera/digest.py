"""Async digest — the surface between G1 and G2.

One durable rollup per day at digest/YYYY-MM-DD.md, committed; the same
notes are mirrored to each task's single Issue thread by the driving
session (notify.py builds the bodies). Flags: any step/synthesis below
CONFIDENCE_FLAG_THRESHOLD, critic splits, verification failures, parked
tasks awaiting answers, tasks awaiting sign-off.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .arcs._common import ARC_STATE_FILE
from .models import CONFIDENCE_FLAG_THRESHOLD, TaskRecord, VerifyResult
from .queue import Queue

DIGEST_DIR = "digest"


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
    # that file — deleted in the v7 consolidation)
    vpath = queue.verification_path(record.spec.id)
    if vpath.exists():
        verdict = VerifyResult.model_validate_json(vpath.read_text(encoding="utf-8"))
        if not verdict.passed:
            flags.append(
                f"VERIFICATION FAILED ({verdict.unrefuted_count}/{verdict.valid_critic_count} unrefuted) — done is blocked"
            )
    return flags


def render(queue: Queue) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
        "awaiting-input",
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
            if state == "awaiting-input":
                lines.append("- ACTION: answer the posted questions (parked, ask-once)")
            for flag in _arc_flags(queue, r):
                lines.append(f"- ⚠ {flag}")
            lines.append("")
    return "\n".join(lines) + "\n"


def write(queue: Queue) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = queue.root / DIGEST_DIR / f"{today}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(queue), encoding="utf-8")
    return path

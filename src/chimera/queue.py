"""Git-durable task queue — the v6 state machine.

Six states:
    ready -> running -> awaiting-signoff -> done -> archived
                         running -> failed -> ready | archived

Verification and the digest are activities *inside* `running`; they are not
states. Every transition is a git commit (durable-state-first). `done` is
reachable ONLY through `Queue.transition()`, which enforces the verify gate
(tasks/<id>/verification.json present AND passed) plus G2 approval — workers
cannot self-declare done. This is the committed stop-guard successor.

`failed` is the terminal-arc parking state (F7 remediation): a task whose
arc reached a terminal failure (or was manually abandoned) moves OUT of
`running` so it can never starve the tick loop. From `failed` the operator
either retries (`failed -> ready`, arc state reset) or retires the task
(`failed -> archived`).

Concurrency: single-flight per task via claim-in-a-commit; a flock guards
against two ticks inside one container. Commits are audit, not mutex — no
git-as-lock protocol beyond the claim commit.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from pydantic import ValidationError

from . import gitio
from .models import (
    TaskRecord,
    TaskSpec,
    TaskState,
    Transition,
    VerifyResult,
    utcnow_iso,
)

# Cross-platform exclusive non-blocking file lock for the tick guard.
# POSIX (cloud containers) use fcntl.flock; native Windows has no fcntl, so
# fall back to msvcrt.locking with the same semantics (raise BlockingIOError
# on contention so tick_lock maps it to QueueError identically on both).
# The wait mode is a bounded poll on BOTH platforms: a live-but-stuck holder
# must not block a submit — or an operator's approve — forever (a crashed
# holder is safe either way: the kernel releases on exit).
LOCK_WAIT_S = 60.0
LOCK_POLL_S = 0.05


def _poll_lock(try_acquire: Callable[[], None]) -> None:
    import time as _time

    deadline = _time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            try_acquire()
            return
        except OSError:  # BlockingIOError (POSIX) and msvcrt's OSError alike
            if _time.monotonic() >= deadline:
                raise QueueError("timed out waiting for the queue-state lock") from None
            _time.sleep(LOCK_POLL_S)


if sys.platform == "win32":
    import msvcrt

    def _tick_lock_acquire(fh: IO[str]) -> None:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc

    def _tick_lock_acquire_wait(fh: IO[str]) -> None:
        _poll_lock(lambda: msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1))

    def _tick_lock_release(fh: IO[str]) -> None:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _tick_lock_acquire(fh: IO[str]) -> None:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _tick_lock_acquire_wait(fh: IO[str]) -> None:
        _poll_lock(lambda: fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB))

    def _tick_lock_release(fh: IO[str]) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)


# Spec fields deleted from TaskSpec over time. `Queue.migrate_task_records()`
# strips them from tasks/*/task.json (no CLI verb — D2, handoff item 3: the
# one-shot `chimera migrate-tasks` command had zero observed uses after its
# 2026-07-02 run); until it runs, extra="forbid" makes the record REFUSE to
# load. That is the point — no silent tolerate-and-drop on load, so a stale
# record announces itself instead of quietly losing a field.
REMOVED_SPEC_FIELDS = (
    "tournament",  # removed 2026-07-02 (audit F1)
    # The retired pipelines' per-arc fields, removed 2026-09-02 (P2-3 / Q8).
    # `target_repo` is expected BACK as a real, read field in the build-mode
    # ADR; this entry retires the dead legacy one, and a re-added field must be
    # dropped from this tuple in the same change that introduces it.
    "brand_context",
    "upstream",
    "target_repo",
    "n8n_target",
    "audience",
    "voice",
    "output_kind",
    "external_ok",
    "manifest",
)

LEGAL_TRANSITIONS: dict[TaskState, tuple[TaskState, ...]] = {
    "ready": ("running",),
    # running -> failed is the terminal-arc exit (F7): a --null-degraded or
    # abandoned arc leaves `running` so it cannot block the tick loop.
    "running": ("awaiting-signoff", "failed"),
    "awaiting-signoff": ("done", "running"),  # running = rework after G2 rejection
    "failed": ("ready", "archived"),  # ready = retry (arc state reset); archived = retire
    "done": ("archived",),
    "archived": (),
}


class QueueError(RuntimeError):
    pass


class IllegalTransition(QueueError):
    pass


class VerifyGateError(QueueError):
    """Raised when a transition to done lacks a passing verification."""


def _validation_summary(exc: ValidationError, limit: int = 5) -> str:
    """Name the offending field(s) of a record-level ValidationError, so a
    migration failure points at what to fix instead of at a wall of pydantic
    output. Bounded: a record with dozens of errors reports the first few."""
    seen: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ())) or "<record>"
        entry = f"{loc} [{err.get('type', 'invalid')}]"
        if entry not in seen:
            seen.append(entry)
    head = ", ".join(seen[:limit])
    return head + (f", … (+{len(seen) - limit} more)" if len(seen) > limit else "")


def slugify(text: str, max_words: int = 4) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:max_words]
    if not words:
        raise QueueError(f"cannot derive slug from {text!r}")
    return "-".join(words)


class Queue:
    def __init__(self, root: Path | None = None):
        self.root = root or gitio.repo_root()
        self.tasks_dir = self.root / "tasks"

    # -- paths ---------------------------------------------------------------

    def task_dir(self, task_id: str) -> Path:
        return self.tasks_dir / task_id

    def task_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "task.json"

    def verification_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "verification.json"

    def artifacts_dir(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "artifacts"

    # -- IO ------------------------------------------------------------------

    def load(self, task_id: str) -> TaskRecord:
        path = self.task_path(task_id)
        if not path.exists():
            raise QueueError(f"no such task: {task_id}")
        try:
            return TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise QueueError(
                f"{task_id}: task.json does not match the current schema "
                f"({exc.error_count()} error(s): {exc.errors()[0].get('loc')}) — if this "
                "record predates a schema change, call Queue.migrate_task_records() once"
            ) from exc

    def _write(self, record: TaskRecord, message: str) -> None:
        path = self.task_path(record.spec.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        gitio.commit(self.root, [path.parent], message)

    # -- lifecycle -----------------------------------------------------------

    def create(self, spec: TaskSpec, initial_state: TaskState, by: str) -> TaskRecord:
        if initial_state != "ready":
            raise IllegalTransition(f"tasks are born ready, not {initial_state}")
        if self.task_path(spec.id).exists():
            raise QueueError(f"task already exists: {spec.id}")
        record = TaskRecord(
            spec=spec,
            state=initial_state,
            history=[Transition(from_state=None, to_state=initial_state, by=by)],
        )
        self._write(record, f"chimera({spec.id}): create [{initial_state}]")
        return record

    def list_tasks(self, state: TaskState | None = None) -> list[TaskRecord]:
        if not self.tasks_dir.exists():
            return []
        records = []
        for path in sorted(self.tasks_dir.glob("*/task.json")):
            record = self.load(path.parent.name)
            if state is None or record.state == state:
                records.append(record)
        return records

    def migrate_task_records(self) -> list[str]:
        """One-shot migration: strip REMOVED_SPEC_FIELDS from every
        tasks/*/task.json (raw JSON — the whole point is that these records
        no longer validate). Returns the migrated task ids; caller commits.

        TWO passes, because a migration is a durable-state write (Behavioral
        Rule 1). Pass 1 loads, strips and VALIDATES every candidate and
        collects the rewrites in memory; pass 2 writes them all. A record that
        still does not validate after stripping (an unknown field this
        migration does not remove) raises QueueError naming the task and the
        offending field(s) BEFORE any file is touched — a per-record write
        loop left earlier records rewritten on disk with nothing committed.

        Records with nothing to strip are validated too, and never rewritten:
        a record whose ONLY problem is an unknown field wedges every queue
        verb, so it has to surface as an error, not as an empty `migrated`
        list with rc 0.
        """
        if not self.tasks_dir.exists():
            return []
        pending: list[tuple[Path, TaskRecord]] = []
        for path in sorted(self.tasks_dir.glob("*/task.json")):
            task_dir = path.parent.name
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise QueueError(
                    f"tasks/{task_dir}/task.json is not valid JSON ({exc}); "
                    "nothing was written — fix the record by hand, then re-run "
                    "Queue.migrate_task_records()"
                ) from exc
            spec = data.get("spec", {})
            removed = [f for f in REMOVED_SPEC_FIELDS if f in spec]
            for field in removed:
                spec.pop(field)
            try:
                record = TaskRecord.model_validate(data)
            except ValidationError as exc:
                raise QueueError(
                    f"tasks/{task_dir}/task.json still does not match the current "
                    f"schema after stripping {removed or 'nothing'}: "
                    f"{_validation_summary(exc)} — nothing was written; fix or "
                    "remove the record by hand, then re-run Queue.migrate_task_records()"
                ) from exc
            if removed:
                pending.append((path, record))
        for path, record in pending:
            path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return [path.parent.name for path, _ in pending]

    def claim(self, task_id: str, worker: str) -> TaskRecord:
        """Claim a ready task and move it to running — single-flight per task."""
        record = self.load(task_id)
        if record.state != "ready":
            raise QueueError(f"{task_id} is {record.state}, not ready — cannot claim")
        if record.claimed_by is not None:
            raise QueueError(f"{task_id} already claimed by {record.claimed_by}")
        record.claimed_by = worker
        record.claimed_at = utcnow_iso()
        record.state = "running"
        record.history.append(
            Transition(from_state="ready", to_state="running", by=worker, note="claimed")
        )
        self._write(record, f"chimera({task_id}): claim by {worker} [running]")
        return record

    def transition(
        self,
        task_id: str,
        to_state: TaskState,
        by: str,
        note: str | None = None,
    ) -> TaskRecord:
        record = self.load(task_id)
        from_state = record.state
        if to_state not in LEGAL_TRANSITIONS[from_state]:
            raise IllegalTransition(
                f"{task_id}: {from_state} -> {to_state} is not a legal transition"
            )
        if to_state == "done":
            self._enforce_done_gate(record)
        if to_state == "ready":
            record.claimed_by = None
            record.claimed_at = None
        record.state = to_state
        record.history.append(
            Transition(from_state=from_state, to_state=to_state, by=by, note=note)
        )
        # The note travels into `git commit -m` and can carry agent-controlled
        # text (arc failure reasons): strip control chars/newlines and cap it so
        # it cannot forge audit-trail lines or crash git; history keeps the
        # original as data.
        msg_note = re.sub(r"[^\x20-\x7e]", " ", note)[:200] if note else None
        self._write(
            record,
            f"chimera({task_id}): -> {to_state}" + (f" ({msg_note})" if msg_note else ""),
        )
        return record

    def _enforce_done_gate(self, record: TaskRecord) -> None:
        """done requires (a) a committed, passing verification, (b) G2 approval."""
        vpath = self.verification_path(record.spec.id)
        if not vpath.exists():
            raise VerifyGateError(
                f"{record.spec.id}: no verification.json — run the verify gate before done"
            )
        verdict = VerifyResult.model_validate_json(vpath.read_text(encoding="utf-8"))
        if not verdict.passed:
            raise VerifyGateError(
                f"{record.spec.id}: verification failed "
                f"({verdict.unrefuted_count}/{verdict.valid_critic_count} unrefuted) — not done"
            )
        if record.approved_by is None:
            raise VerifyGateError(
                f"{record.spec.id}: no G2 approval recorded — the operator signs off before done"
            )

    def record_verification(self, task_id: str, result: VerifyResult) -> None:
        path = self.verification_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        gitio.commit(
            self.root,
            [path],
            f"chimera({task_id}): verification {'PASS' if result.passed else 'FAIL'} ({result.mode})",
        )

    def record_approval(self, task_id: str, by: str) -> TaskRecord:
        record = self.load(task_id)
        if record.state != "awaiting-signoff":
            raise QueueError(f"{task_id} is {record.state}; approval applies at awaiting-signoff")
        record.approved_by = by
        record.approved_at = utcnow_iso()
        self._write(record, f"chimera({task_id}): G2 approved by {by}")
        return record


@contextmanager
def tick_lock(root: Path, wait: bool = False) -> Iterator[None]:
    """flock guard: at most one queue-state writer per container at a time.
    Lock file is gitignored.

    wait=False (tick): a held lock refuses loudly — two ticks racing is an
    operator error worth surfacing. wait=True (arc submit / arc next): block
    until free — a phase's nodes legitimately land in parallel, and an
    unserialized load->mutate->save loses whichever write finishes first
    (2026-08-28 adversarial audit, OP-2: outputs vanished from committed
    state while the losing submit still exited 0)."""
    lock_path = root / ".chimera-tick.lock"
    fh = lock_path.open("w")
    try:
        if wait:
            _tick_lock_acquire_wait(fh)
        else:
            try:
                _tick_lock_acquire(fh)
            except BlockingIOError as exc:
                raise QueueError("another tick is already running in this container") from exc
        yield
    finally:
        _tick_lock_release(fh)
        fh.close()


def next_runnable(queue: Queue) -> TaskRecord | None:
    """Oldest ready task with a live arc, FIFO by id (ids start with YYYYMMDD).

    A pre-consolidation record whose arc was retired (ADR-010) is never
    claimable — claiming it would crash the tick at dispatch, and one legacy
    record must not starve the loop (the F7 rule, applied to retirement).
    `retired_ready` (below) surfaces them so they are skipped loudly, not
    silently."""
    from .models import RETIRED_ARCS

    ready = [r for r in queue.list_tasks("ready") if r.spec.arc not in RETIRED_ARCS]
    return ready[0] if ready else None


def retired_ready(queue: Queue) -> list[str]:
    """Ready tasks that can never run because their arc was retired — no
    queue-level exit remains (D3, handoff item 3). The real clearing path is
    an operator action, not a queue verb: `git rm -r tasks/<id>` + commit;
    re-open the ask as a graph task afterward if it still matters."""
    from .models import RETIRED_ARCS

    return [r.spec.id for r in queue.list_tasks("ready") if r.spec.arc in RETIRED_ARCS]

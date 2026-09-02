"""chimera CLI — the deterministic skeleton the cloud session drives.

Tick protocol (TICK_PROTOCOL.md): a worker session runs `chimera tick` to
claim a runnable task, executes each pending AgentCall with its native
Agent tool, submits results back with `chimera arc submit`, and the CLI
checkpoints (commit+push) after every mutation. Park or complete, re-arm.

Workers cannot self-declare done: `chimera approve` is the only path, and
queue.transition() enforces the verify gate + G2 approval underneath it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import digest as digest_mod
from . import gitio, notify, runner
from .arcs._common import ARC_STATE_FILE
from .arcs.graph import GraphArc, GraphArcError
from .gates import (
    g1_answer,
    g1_intake,
    g2_approve,
    g2_reject,
    read_questions,
)
from .models import VerifyResult
from .queue import Queue, QueueError, next_runnable, retired_ready, tick_lock
from .verify.schema_gate import SchemaGateError


def _git_config_user() -> str | None:
    """Return git config user.name (global or local) or None.

    Used as the public identity fallback for _worker(). Failure modes
    (no git, no config, repo with no user.name) all reduce to None — the
    caller falls back to hostname.
    """
    try:
        out = subprocess.run(
            ["git", "config", "--get", "user.name"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    name = out.stdout.strip()
    return name or None


def _worker() -> str:
    """Stable actor stamp for queue transitions, claims, approvals.

    Resolution order:
      1. $CHIMERA_AUTHOR  — explicit identity override (preferred name)
      2. $CHIMERA_WORKER  — legacy env var (kept for back-compat)
      3. git config user.name — discoverable per-machine identity
                                (whatever "git config user.name" returns)
      4. session-<hostname> — last-resort fallback so something is recorded
    """
    for env_key in ("CHIMERA_AUTHOR", "CHIMERA_WORKER"):
        v = os.environ.get(env_key)
        if v:
            by = v
            break
    else:
        git_user = _git_config_user()
        by = git_user if git_user else f"session-{socket.gethostname()}"
    # Strip non-printable/control chars (including newlines) and cap length so
    # `by` cannot inject git trailers when interpolated into commit messages.
    return re.sub(r"[^\x20-\x7e]", "", by)[:64]


def _emit(payload: object, code: int = 0) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    sys.exit(code)


# Always true under durable-state-first: steps either committed or didn't run.
DEFAULT_STATE_NOTE = "task state is as of the last committed step; any pending call is intact"


def _die(
    message: str,
    code: int = 1,
    hint: str | None = None,
    state_note: str = DEFAULT_STATE_NOTE,
) -> None:
    """Framed failure (N8 remediation): machine-parsable JSON on stdout for a
    dispatcher, Error/Hint/State lines on stderr for a human — never a raw
    traceback."""
    payload: dict = {"ok": False, "error": message}
    if hint:
        payload["hint"] = hint
    payload["state"] = state_note
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    sys.stderr.write(f"Error: {message}\n")
    if hint:
        sys.stderr.write(f"Hint: {hint}\n")
    sys.stderr.write(f"State: {state_note}\n")
    sys.exit(code)


def _arc_for(queue: Queue, task_id: str):
    """Return the arc instance for a task. One live arc since the v7 consolidation; a
    retired arc's record still loads (status/history/archive) but cannot be
    dispatched — retry it as a fresh graph task instead."""
    record = queue.load(task_id)
    if record.spec.arc == "graph":
        return GraphArc(queue.task_dir(task_id))
    from .models import RETIRED_ARCS

    if record.spec.arc in RETIRED_ARCS:
        raise QueueError(
            f"{task_id}: arc {record.spec.arc!r} was retired in the v7 "
            "consolidation. The record remains readable; to redo the "
            "work, open it as a graph task: chimera new \"<ask>\""
        )
    raise QueueError(f"unknown arc {record.spec.arc!r} (no dispatch mapping)")


def _checkpoint_task(queue: Queue, task_id: str, message: str) -> None:
    runner.checkpoint(queue.root, [queue.task_dir(task_id)], f"chimera({task_id}): {message}")


def _push_and_emit_state(queue: Queue, record) -> None:
    """Shared tail for the simple lifecycle commands (answer/approve/reject/
    archive): push, then emit the {ok, task_id, state} triple."""
    gitio.push(queue.root)
    _emit({"ok": True, "task_id": record.spec.id, "state": record.state})


def _write_failed_l2(record, failure: str | None) -> None:
    """Failure-learning memory row (tags='terminal,failed') so failure modes can
    compound. STRICTLY fail-open: the failed transition is the one code path
    that must never wedge, so a memory failure (DB absent, locked, corrupt)
    is swallowed — the row is a bonus, the transition is the contract."""
    try:
        from . import arc_memory

        arc_memory.summarize_run(
            arc_kind=record.spec.arc,
            arc_id=record.spec.id,
            summary=(
                f"arc FAILED: {failure or 'no failure reason recorded'}"
                f" | ask: {record.spec.ask}"
            ),
            tags="terminal,failed",
        )
    except Exception:
        pass


def _fail_task(queue: Queue, record, failure: str | None, by: str, note: str) -> None:
    """running -> failed (commit) + the fail-open L2 learning row."""
    detail = f"{note}: {failure}" if failure else note
    queue.transition(record.spec.id, "failed", by=by, note=detail[:400])
    _write_failed_l2(record, failure)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> None:
    queue = Queue()
    records = g1_intake(
        queue,
        ask=args.ask,
        outcome=args.outcome,
        by=_worker(),
        questions=args.question or None,
        context=args.context,
        slug=args.slug,
        shape=args.shape,
    )
    gitio.push(queue.root)
    out = []
    for r in records:
        entry = {"task_id": r.spec.id, "state": r.state}
        if r.state == "awaiting-input":
            qs = read_questions(queue, r.spec.id)
            entry["questions_comment"] = notify.questions_comment(qs)
            entry["issue_title"] = notify.issue_title(r)
            entry["issue_body"] = notify.issue_body(r)
        out.append(entry)
    _emit({"ok": True, "tasks": out})


def cmd_answer(args: argparse.Namespace) -> None:
    queue = Queue()
    answers = {k: v for k, v in (args.answer or [])}
    record = g1_answer(queue, args.task_id, answers, by=_worker())
    _push_and_emit_state(queue, record)


def cmd_tick(args: argparse.Namespace) -> None:
    queue = Queue()
    with tick_lock(queue.root):
        # resume work already in flight first (container-reclaim recovery),
        # then claim new work. A running task whose arc reached terminal
        # failure is moved to `failed` and skipped (F7: one failed arc must
        # never starve the loop) — tick keeps scanning for live work.
        newly_failed: list[str] = []
        record = None
        for candidate in queue.list_tasks("running"):
            if candidate.spec.arc != "graph":
                # a pre-consolidation running record can't dispatch; park it
                # failed so it never starves the loop (F7)
                _fail_task(
                    queue,
                    candidate,
                    f"arc {candidate.spec.arc!r} retired",
                    by=_worker(),
                    note="retired arc detected at tick",
                )
                newly_failed.append(candidate.spec.id)
                continue
            arc = _arc_for(queue, candidate.spec.id)
            if arc.state_path.exists():
                try:
                    arc_state = arc.load()
                except (ValueError, OSError, GraphArcError) as exc:
                    # corrupt/truncated arc state (non-atomic save + container
                    # reclaim, schema drift) OR a persisted plan failing
                    # structural re-admission (GraphArcError from load,
                    # audit R-1 — it is a RuntimeError, so the ValueError
                    # guard alone let it starve the queue): treat as terminal
                    # — the scan must self-heal, never crash the tick it
                    # exists to protect
                    _fail_task(
                        queue,
                        candidate,
                        f"corrupt arc state: {exc}",
                        by=_worker(),
                        note="unreadable arc state detected at tick",
                    )
                    newly_failed.append(candidate.spec.id)
                    continue
                if arc_state.phase == "failed":
                    _fail_task(
                        queue,
                        candidate,
                        arc_state.failure,
                        by=_worker(),
                        note="terminal arc failure detected at tick",
                    )
                    newly_failed.append(candidate.spec.id)
                    continue
            record = candidate
            break
        if record is None:
            record = next_runnable(queue)
            if record is None:
                if newly_failed:
                    gitio.push(queue.root)
                stranded = retired_ready(queue)
                _emit(
                    {
                        "ok": True,
                        "action": "idle",
                        "note": "no runnable tasks",
                        **({"failed_tasks": newly_failed} if newly_failed else {}),
                        **(
                            {"retired_ready": stranded,
                             "retired_note": "these ready tasks have retired arcs; archive them or re-open the ask as a graph task"}
                            if stranded else {}
                        ),
                    }
                )
                return
            record = queue.claim(record.spec.id, _worker())
        arc = _arc_for(queue, record.spec.id)
        try:
            if not arc.state_path.exists():
                state = arc.start(
                    record.spec.id, record.spec.slug, record.spec.ask, record.spec.context
                )
                arc.save(state)
                _checkpoint_task(queue, record.spec.id, "arc started")
            else:
                state = arc.load()
                expired = arc.expire_timeouts(state)
                if expired:
                    # expire_timeouts leaves authoritative state on disk (a timeout
                    # that completes the verify panel finalizes into a FRESH object)
                    # — reload; saving this reference could revert a terminal state
                    state = arc.load()
                    _checkpoint_task(queue, record.spec.id, f"expired timeouts: {expired}")
            # pending_calls() stamps first-issue times (N1) — compute once, save so
            # the stamps survive the process, then reuse the same list in the emit.
            calls = arc.pending_calls(state)
            arc.save(state)
        except (gitio.GitError, QueueError):
            raise  # infra failures are transient/operator-level, not the task's
        except Exception as exc:
            # F7 extended (audit OP-3): an arc that RAISES while resuming (a
            # misconfigured model env tripping the maker≠checker guard, a bug
            # in call synthesis) would otherwise be re-selected and re-raise on
            # every tick, starving the whole queue behind one poisoned task.
            # Park it failed, loudly, and keep the queue moving.
            _fail_task(
                queue,
                record,
                f"{type(exc).__name__} at tick: {exc}",
                by=_worker(),
                note="arc raised while resuming; parked failed so the queue keeps moving (F7)",
            )
            newly_failed.append(record.spec.id)
            gitio.push(queue.root)
            _emit(
                {
                    "ok": True,
                    "action": "parked-failed",
                    "task_id": record.spec.id,
                    "failure": f"{type(exc).__name__}: {exc}",
                    "failed_tasks": newly_failed,
                    "hint": "fix the cause, then `chimera retry <task-id>` to re-open it; run `chimera tick` again for the next task",
                }
            )
            return
        gitio.push(queue.root)
        _emit(
            {
                "ok": True,
                "action": "work",
                "task_id": record.spec.id,
                "arc_phase": state.phase,
                "pending_calls": [c.model_dump() for c in calls],
                **({"failed_tasks": newly_failed} if newly_failed else {}),
            }
        )


def cmd_arc_next(args: argparse.Namespace) -> None:
    # next stamps first-issue times and saves state — take the same writer
    # lock as submit so a concurrent write can't be lost (audit OP-2)
    queue = Queue()
    with tick_lock(queue.root, wait=True):
        _arc_next_locked(queue, args)


def _arc_next_locked(queue: Queue, args: argparse.Namespace) -> None:
    arc = _arc_for(queue, args.task_id)
    state = arc.load()
    expired = arc.expire_timeouts(state)
    if expired:
        # same reload-not-save contract as cmd_tick — a stale save here could
        # revert a timeout-finalized terminal state
        state = arc.load()
        _checkpoint_task(queue, args.task_id, f"expired timeouts: {expired}")
    # pending_calls() stamps first-issue times (N1) — save before emitting.
    calls = arc.pending_calls(state)
    arc.save(state)
    _emit(
        {
            "ok": True,
            "task_id": args.task_id,
            "arc_phase": state.phase,
            "failure": state.failure,
            "pending_calls": [c.model_dump() for c in calls],
        }
    )


def cmd_arc_submit(args: argparse.Namespace) -> None:
    # a phase's nodes legitimately land in parallel; without serialization the
    # load->mutate->save race loses whichever submit finishes first while
    # still exiting 0 (audit OP-2) — wait on the queue-state writer lock
    queue = Queue()
    with tick_lock(queue.root, wait=True):
        _arc_submit_locked(queue, args)


def _arc_submit_locked(queue: Queue, args: argparse.Namespace) -> None:
    arc = _arc_for(queue, args.task_id)
    state = arc.load()
    if args.null:
        payload = None
    elif args.file:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    elif args.json:
        payload = json.loads(args.json)
    else:
        _die(
            "provide --file, --json, or --null",
            hint="submit the agent result as `--json '<object>'`, `--file result.json`, or `--null` for a degraded call",
        )
        return
    # Use the state submit() returns: arcs whose verify-finalize step reloads a
    # fresh terminal state (build/reflect) would otherwise be reverted by saving a
    # stale local copy. `or state` guards any path that mutates in place + returns None.
    state = arc.submit(state, args.label, payload, kind=args.kind) or state
    # pending_calls() stamps first-issue times (N1) — compute before the save
    # so freshly-emitted labels' stamps persist (cmd_tick/cmd_arc_next parity)
    calls = arc.pending_calls(state)
    arc.save(state)
    _checkpoint_task(queue, args.task_id, f"submit {args.label}")

    response: dict = {
        "ok": True,
        "task_id": args.task_id,
        "arc_phase": state.phase,
        "pending_calls": [c.model_dump() for c in calls],
    }

    if state.phase == "complete":
        verdict = arc.verify_verdict(state)
        queue.record_verification(args.task_id, verdict)
        record = queue.transition(
            args.task_id, "awaiting-signoff", by=_worker(), note="arc complete"
        )
        digest_path = digest_mod.write(queue)
        # this checkpoint's push carries the verification + transition commits
        # too — but checkpoint only pushes when its own commit is non-empty, and
        # a same-day rework regenerates a byte-identical digest. Push explicitly
        # then, or the terminal transition dies with the container.
        if runner.checkpoint(queue.root, [digest_path], "chimera: digest rollup") is None:
            gitio.push(queue.root)
        artifact_name = getattr(arc, "ARTIFACT_FILENAME", "findings.md")
        artifact_rel = str(
            (queue.artifacts_dir(args.task_id) / artifact_name).relative_to(queue.root)
        )
        response["signoff_comment"] = notify.signoff_comment(record, verdict, artifact_rel)
        response["verification"] = verdict.model_dump()
    elif state.phase == "failed":
        response["failure"] = state.failure
        record = queue.load(args.task_id)
        if record.state == "running":
            # F7: a terminal arc failure leaves `running` immediately so it can
            # never block the tick loop; digest/status surface it from here.
            _fail_task(queue, record, state.failure, by=_worker(), note="arc failed at submit")
            response["state"] = "failed"
        gitio.push(queue.root)
    # No unconditional trailing push (F8 dedupe): the in-progress path was
    # already pushed by _checkpoint_task above, and the complete path by the
    # digest checkpoint.
    _emit(response)


def cmd_approve(args: argparse.Namespace) -> None:
    queue = Queue()
    record = g2_approve(queue, args.task_id, by=args.by)
    _push_and_emit_state(queue, record)


def cmd_reject(args: argparse.Namespace) -> None:
    queue = Queue()
    record = g2_reject(queue, args.task_id, by=args.by, note=args.note)
    _push_and_emit_state(queue, record)


def cmd_abandon(args: argparse.Namespace) -> None:
    """Manually park a jammed running task as failed (F7 unjam verb).

    Covers the case tick cannot auto-detect: an arc that is wedged (hung
    pending call, corrupt state) without having reached a terminal
    arc_phase. From `failed`, `chimera retry` restarts or `chimera archive`
    retires."""
    queue = Queue()
    record = queue.load(args.task_id)
    if record.state != "running":
        _die(f"{args.task_id} is {record.state}; abandon applies to running tasks")
        return
    failure = args.note
    if failure is None:
        arc = _arc_for(queue, args.task_id)
        if arc.state_path.exists():
            try:
                failure = arc.load().failure
            except (ValueError, OSError):
                # the docstring promises abandon covers corrupt state —
                # an unreadable arc file must not block the unjam verb
                failure = "corrupt arc state (unreadable)"
    _fail_task(queue, record, failure, by=_worker(), note="abandoned")
    gitio.push(queue.root)
    _emit({"ok": True, "task_id": record.spec.id, "state": "failed", "failure": failure})


def cmd_retry(args: argparse.Namespace) -> None:
    """failed -> ready with a fresh arc start. The failed arc state is
    preserved under a timestamped name (audit trail, durable-state-first);
    the next tick claims the task and starts the arc over."""
    from .models import utcnow_iso

    queue = Queue()
    record = queue.load(args.task_id)
    if record.state != "failed":
        _die(f"{args.task_id} is {record.state}; retry applies to failed tasks")
        return
    arc = _arc_for(queue, args.task_id)
    preserved = None
    if arc.state_path.exists():
        stamp = utcnow_iso().replace("-", "").replace(":", "")
        preserved = arc.state_path.with_name(f"arc-state.failed-{stamp}.json")
        arc.state_path.rename(preserved)
    # transition() commits the whole task dir, picking up the rename with it
    record = queue.transition(
        args.task_id, "ready", by=_worker(), note="retry after failure — arc state reset"
    )
    gitio.push(queue.root)
    _emit(
        {
            "ok": True,
            "task_id": record.spec.id,
            "state": record.state,
            "preserved_arc_state": str(preserved.name) if preserved else None,
        }
    )


def cmd_archive(args: argparse.Namespace) -> None:
    queue = Queue()
    record = queue.load(args.task_id)
    _capture_memory(queue, record)
    record = queue.transition(args.task_id, "archived", by=_worker(), note="memory captured")
    _push_and_emit_state(queue, record)


def _capture_memory(queue: Queue, record) -> None:
    """Memory capture on archive: one pattern row per task (dedup by title).

    Writes to memory.DEFAULT_DB_PATH — the same file the search shim reads
    (F10 part 1: reads and writes were split across two DB files before)."""
    from . import memory

    db_path = memory.DEFAULT_DB_PATH
    with memory._connect(db_path) as conn:
        conn.executescript(memory.SCHEMA_SQL)
        conn.executescript(memory.FTS_SYNC_SQL)
        title = f"task_{record.spec.id}"
        body = f"arc={record.spec.arc} ask={record.spec.ask}"
        # legacy records only: no live arc writes result.json (v6 research
        # artifact) — kept so archiving a pre-v7 record still enriches memory
        result_path = queue.artifacts_dir(record.spec.id) / "result.json"
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            recs = [s.get("recommendation") for s in result.get("syntheses", [])]
            body += " | recommendations: " + "; ".join(r for r in recs if r)
        existing = conn.execute(
            "SELECT id FROM memories WHERE agent = ? AND title = ?", ("chimera", title)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO memories (agent, type, title, body, tags) VALUES (?, ?, ?, ?, ?)",
                ("chimera", "pattern", title, body, record.spec.arc),
            )
        conn.commit()


def cmd_migrate_tasks(args: argparse.Namespace) -> None:
    """One-shot task-record migration: strip spec fields removed from the
    schema (e.g. `tournament`, removed 2026-07-02) from tasks/*/task.json.
    Legacy records fail load() with a pointer here; there is deliberately no
    silent compat shim."""
    queue = Queue()
    migrated = queue.migrate_task_records()
    if migrated:
        gitio.commit(
            queue.root,
            [queue.tasks_dir],
            f"chimera: migrate {len(migrated)} task record(s) to current schema",
        )
        gitio.push(queue.root)
    _emit({"ok": True, "migrated": migrated})


def _pending_call_age(queue: Queue, task_id: str) -> dict | None:
    """Oldest pending-call label + age in seconds, fail-open (any exception ->
    None, omitted by the caller). Reads arc-state.json as plain JSON so a
    schema-drifted or unreadable state file never breaks `chimera status`.
    Stage arcs stamp `first_issued`; research stamps `pending[label].issued_at`."""
    try:
        data = json.loads((queue.task_dir(task_id) / ARC_STATE_FILE).read_text(encoding="utf-8"))
        if data.get("first_issued"):
            stamps = data["first_issued"]
        elif data.get("pending"):
            stamps = {label: call["issued_at"] for label, call in data["pending"].items()}
        else:
            return None
        if not stamps:
            return None
        oldest_label = min(stamps, key=lambda label: stamps[label])
        issued = datetime.strptime(stamps[oldest_label], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        age_s = int((datetime.now(timezone.utc) - issued).total_seconds())
        return {"oldest_label": oldest_label, "oldest_age_s": age_s}
    except Exception:
        return None


def cmd_init(args: argparse.Namespace) -> None:
    """First-run setup / preflight. The only command safe to run before
    anything is configured, so it resolves the repo root itself rather than
    assuming a working queue."""
    from .memory import PROJECT_ROOT
    from .setup_wizard import init as run_init

    code = run_init(
        Path(args.root).resolve() if args.root else PROJECT_ROOT,
        non_interactive=args.non_interactive,
        force=args.force,
        check_only=args.check,
    )
    if code:
        raise SystemExit(code)


def cmd_status(args: argparse.Namespace) -> None:
    queue = Queue()
    tasks = []
    for r in queue.list_tasks():
        entry = {
            "id": r.spec.id,
            "state": r.state,
            "arc": r.spec.arc,
            "claimed_by": r.claimed_by,
            "ask": r.spec.ask,
        }
        if r.state == "running":
            pending = _pending_call_age(queue, r.spec.id)
            if pending is not None:
                entry["pending"] = pending
        tasks.append(entry)
    _emit(
        {
            "ok": True,
            # M6 rider: push health up front — a lost push race or a dead
            # remote must be visible within one status call
            "push": gitio.push_health(queue.root),
            "tasks": tasks,
        }
    )


def cmd_digest(args: argparse.Namespace) -> None:
    queue = Queue()
    path = digest_mod.write(queue)
    runner.checkpoint(queue.root, [path], "chimera: digest rollup")
    sys.stdout.write(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Role install — the six fenced roles are the whole roster
# ---------------------------------------------------------------------------


def cmd_install_agents(args: argparse.Namespace) -> None:
    from . import agents

    target = Path(args.target) if args.target else None
    _emit(agents.install_roles(target, dry_run=bool(args.dry_run)))


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="G1 intake — open task(s) from one ask")
    p.add_argument("ask")
    p.add_argument(
        "--outcome",
        default="proceed-top",
        choices=["proceed-top", "ask", "parallel-ab"],
        help="Socratic lens outcome (the session runs agents.socratic() first)",
    )
    p.add_argument("--question", action="append", help="repeatable; required with --outcome ask")
    p.add_argument("--context", default=None)
    p.add_argument("--slug", default=None)
    p.add_argument(
        "--shape",
        default=None,
        choices=["straight", "diamond", "pipeline"],
        help="pin the run shape (you pick, the framework only recommends); "
        "omit to let the planner propose within the levers",
    )
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("answer", help="record G1 answers, unpark the task")
    p.add_argument("task_id")
    p.add_argument("--answer", nargs=2, action="append", metavar=("QID", "TEXT"))
    p.set_defaults(func=cmd_answer)

    p = sub.add_parser("tick", help="claim/resume a runnable task; print pending agent calls")
    p.set_defaults(func=cmd_tick)

    arc = sub.add_parser("arc", help="arc step protocol").add_subparsers(
        dest="arc_cmd", required=True
    )
    p = arc.add_parser("next", help="pending agent calls (expires timeouts)")
    p.add_argument("task_id")
    p.set_defaults(func=cmd_arc_next)
    p = arc.add_parser("submit", help="submit one agent result (or --null)")
    p.add_argument("task_id")
    p.add_argument("label")
    p.add_argument("--file", default=None)
    p.add_argument("--json", default=None)
    p.add_argument("--null", action="store_true")
    p.add_argument("--kind", default="null", choices=["null", "timeout", "threw"])
    p.set_defaults(func=cmd_arc_submit)

    p = sub.add_parser("approve", help="G2 sign-off -> done (verify gate enforced)")
    p.add_argument("task_id")
    p.add_argument("--by", default="operator")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="G2 rework -> back to running")
    p.add_argument("task_id")
    p.add_argument("--by", default="operator")
    p.add_argument("--note", required=True)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("abandon", help="running -> failed (unjam a wedged/failed arc)")
    p.add_argument("task_id")
    p.add_argument("--note", default=None, help="failure reason for the audit trail")
    p.set_defaults(func=cmd_abandon)

    p = sub.add_parser("retry", help="failed -> ready (fresh arc start; old state preserved)")
    p.add_argument("task_id")
    p.set_defaults(func=cmd_retry)

    p = sub.add_parser("archive", help="done|failed -> archived (+ memory capture)")
    p.add_argument("task_id")
    p.set_defaults(func=cmd_archive)

    sub.add_parser(
        "migrate-tasks", help="one-shot: strip removed spec fields from tasks/*/task.json"
    ).set_defaults(func=cmd_migrate_tasks)

    sub.add_parser("status", help="list tasks").set_defaults(func=cmd_status)
    sub.add_parser("digest", help="write + commit digest/YYYY-MM-DD.md").set_defaults(
        func=cmd_digest
    )

    p = sub.add_parser("init", help="first-run setup: write .env + preflight checks")
    p.add_argument("--check", action="store_true",
                   help="run preflight checks only; writes nothing")
    p.add_argument("--non-interactive", action="store_true",
                   help="accept all defaults without prompting")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing .env instead of backing it up")
    p.add_argument("--root", default=None, help="repo root (default: autodetect)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("install-agents", help="write the six role .md files → ~/.claude/agents/")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--target", default=None, help="override target dir (default ~/.claude/agents)")
    p.set_defaults(func=cmd_install_agents)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except QueueError as exc:
        _die(str(exc))
    except SchemaGateError as exc:
        _die(
            str(exc),
            hint="re-run the agent once with this error appended; second failure -> submit --null (three-strike rule)",
        )
    except json.JSONDecodeError as exc:
        _die(
            f"invalid JSON payload: {exc}",
            hint="fix the --json quoting or the --file contents and re-run the same submit",
        )
    except FileNotFoundError as exc:
        _die(
            f"file not found: {getattr(exc, 'filename', None) or exc}",
            hint="check the --file path; relative paths resolve against the current directory",
        )
    except gitio.GitError as exc:
        _die(
            f"git failure: {exc}",
            hint="check `git status` and the remote config; committed state is durable — re-run once git is healthy",
            state_note="local commits are durable; the push may be behind (see chimera status)",
        )
    except runner.CeilingExceeded as exc:
        _die(
            str(exc),
            hint="the 250-call ceiling aborted this run; `chimera abandon <task-id>` then `chimera retry` for a fresh start",
        )
    except Exception as exc:  # last resort: framed, never a raw traceback (N8)
        # match the ArcError family by name so any arc error gets the
        # routine-mistake hint instead of 'unexpected'
        if isinstance(exc, GraphArcError) or type(exc).__name__.endswith("ArcError"):
            _die(
                str(exc),
                hint="run `chimera arc next <task-id>` to see the pending labels, then resubmit",
            )
            return
        if os.environ.get("CHIMERA_DEBUG"):
            raise
        _die(
            f"unexpected {type(exc).__name__}: {exc}",
            hint="re-run with CHIMERA_DEBUG=1 for the full traceback",
        )


if __name__ == "__main__":
    main()

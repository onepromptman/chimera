"""Shared helpers for chimera arc modules.

The arc spine, written when eight arcs shared it; since the v7 consolidation
the one live arc is graph, and this module IS its duck-typed
contract — a second arc ships by building on exactly these pieces and
registering in tests/arc_drivers.py::ARCS.

Centralizes:
  - ARC_STATE_FILE — the persisted-state filename
  - arc_save() / arc_load() — the generic persistence pair
  - read_verify_result() — reads verification.json into a VerifyResult
  - dispatch_null() — the recoverable-null dispatcher (M3), counting through
    runner.issue_call so the 250-call ceiling binds on the null path too
  - VERIFY_KEYS — the verify-stage finalize key set
  - stamp_first_issued() / expired_labels() — shared per-call expiry (N1)
  - gate_critic_opinion() — verify-stage CriticOpinion through the schema gate
  - PriorsSeed / priors_block() — fail-open Layer-2 seed-read (M5 part 3)
  - repair_brief() / finalize_with_repair() — the bounded critique -> rewrite
    verify finalize (critique-rewrite): on genuine refutation, feed the
    critique back and re-run verify, lever-bounded, before halting
  - accumulate_verify_opinion() — the shared _submit_verify body (stage guard,
    slot the payload into state.verify_opinions via the schema gate, and once
    VERIFY_KEYS is complete, decode + dispatch to the arc's own finalize)
  - finalize_verify_with_repair() — the stage guard + lite.verdict +
    finalize_with_repair composition the graph arc's verify stage runs
  - load_task_record() — reads tasks/<id>/task.json into a TaskRecord,
    fail-open (None) on a missing file or the two narrowed error classes

No imports from arc modules here; circular-import guard.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, Type, TypeVar

from pydantic import Field

from .. import runner
from ..models import AgentCall, CriticOpinion, _Strict
from ..verify import lite, schema_gate

# ---------------------------------------------------------------------------
# Shared file-name constants (identical value in every arc that declares them)
# ---------------------------------------------------------------------------

ARC_STATE_FILE = "arc-state.json"

# ---------------------------------------------------------------------------
# Generic save / load
# ---------------------------------------------------------------------------

_S = TypeVar("_S")


def arc_save(state_path: Path, state: Any, error_cls: type[Exception]) -> Path:
    """Write *state* to *state_path* as indented JSON.

    Raises *error_cls* if *state* is None (mirrors per-arc guard). Creates
    parent directories as needed. Does NOT append a trailing newline (that
    is ResearchArc's unique contract — it stays in research.py).
    """
    if state is None:
        raise error_cls("save requires a state")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.model_dump_json(indent=2))
    return state_path


def arc_load(state_path: Path, model_cls: Type[_S], error_cls: type[Exception]) -> _S:
    """Read *state_path* and return a validated instance of *model_cls*.

    Raises *error_cls* with a standard message if the file does not exist.
    """
    if not state_path.exists():
        raise error_cls(f"no arc state at {state_path} — run start first")
    return model_cls.model_validate_json(state_path.read_text())  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# verify_verdict reader
# ---------------------------------------------------------------------------


def read_verify_result(task_dir: Path, error_cls: type[Exception]):
    """Read tasks/<id>/verification.json and return a VerifyResult.

    Raises *error_cls* with a standard message when the file has not been
    written yet (i.e. finalize_verify has not run).  The state object is
    passed in only for the error message; the read is unconditional once the
    file exists.
    """
    from ..models import VerifyResult

    verification_path = task_dir / "verification.json"
    if not verification_path.exists():
        raise error_cls(
            "verify_verdict called before verification.json was written"
        )
    return VerifyResult.model_validate_json(verification_path.read_text())


# ---------------------------------------------------------------------------
# dispatch_null — the recoverable-null dispatcher (M3)
# ---------------------------------------------------------------------------


def dispatch_null(
    state: Any,
    label: str,
    kind: str,
    *,
    recoverable: tuple[str, ...],
    route: Callable[[Any, str], Any],
    save: Callable[[Any], Any],
    halt_note: str = "primary maker",
    on_halt: Callable[[Any], None] | None = None,
) -> Any:
    """Route one degraded submission (null | timeout | threw).

    A label prefixed by anything in *recoverable* degrades: *route* slots a
    None into the arc's own panel structure and runs the arc's own
    tally/close logic (routers mutate *state* in place and return it). Any
    other label halts exactly as the pre-M3 arcs did.

    Counter semantics follow runner.py: record_null does NOT touch
    agent_calls_attempted, so the attempted increment happens here — through
    issue_call, so the 250-call ceiling binds on the null path too (audit OP-4).
    """
    try:
        runner.issue_call(state.audit, label)
    except runner.CeilingExceeded as exc:
        runner.record_null(state.audit, label, kind=kind)
        state.stage = "halted"
        state.failure = str(exc)
        if hasattr(state, "log"):
            state.log.append(f"CEILING label={label}: run aborted at the call ceiling")
        save(state)
        return state
    runner.record_null(state.audit, label, kind=kind)
    if hasattr(state, "log"):  # duck-typed: a future arc may carry no log field
        state.log.append(f"NULL_AGENT label={label} kind={kind}")
    state.first_issued.pop(label, None)
    if any(label.startswith(p) for p in recoverable):
        return route(state, label)
    state.stage = "halted"
    state.failure = f"null submission at label {label!r} ({halt_note})"
    if on_halt is not None:
        on_halt(state)
    save(state)
    return state


# Verify-stage finalize key set (label format per verify/lite.py:79). Every
# stage arc's finalize trigger is VERIFY_KEYS.issubset(state.verify_opinions)
# instead of a count (a count cannot distinguish 3 valid opinions from 2
# valid + 1 key collision, and doesn't survive None-slotted recoverable nulls).
VERIFY_KEYS = frozenset({"verify:critic1", "verify:critic2", "verify:critic3"})


# ---------------------------------------------------------------------------
# stamp_first_issued / expired_labels — shared per-call expiry (N1)
# ---------------------------------------------------------------------------


def stamp_first_issued(state: Any, calls: Sequence[AgentCall]) -> bool:
    """Record ISO-Z first-issue time per pending label (keep the earliest).
    Returns True if any stamp was added (caller may persist)."""
    added = False
    for call in calls:
        if call.label not in state.first_issued:
            state.first_issued[call.label] = call.issued_at
            added = True
    return added


def expired_labels(
    state: Any,
    calls: Sequence[AgentCall],
    *,
    ceilings: Mapping[str, int] | None = None,
    default_s: int = runner.PER_BRANCH_TIMEOUT_S,
    now: datetime | None = None,
) -> list[str]:
    """Stamped labels among *calls* older than their ceiling. The ceiling for
    a label is ceilings.get(label.split(":", 1)[0], default_s)."""
    ceilings = ceilings or {}
    now = now or datetime.now(timezone.utc)
    expired: list[str] = []
    for call in calls:
        stamp = state.first_issued.get(call.label)
        if stamp is None:
            continue
        issued = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        ceiling = ceilings.get(call.label.split(":", 1)[0], default_s)
        if (now - issued).total_seconds() > ceiling:
            expired.append(call.label)
    return expired


# ---------------------------------------------------------------------------
# gate_critic_opinion — verify-stage schema gate (N1)
# ---------------------------------------------------------------------------


def gate_critic_opinion(payload: Any) -> CriticOpinion:
    """Route a verify-stage critic payload through the schema gate. Accepts a
    JSON string or an already-decoded object. Raises SchemaGateError — the
    CLI frames it (N8) and the pending call stays intact."""
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    validated = schema_gate.validate("CriticOpinion", payload)
    return validated  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PriorsSeed / priors_block — fail-open Layer-2 seed-read (M5 part 3)
# ---------------------------------------------------------------------------


class PriorsSeed(_Strict):
    rows: list[int] = Field(default_factory=list)  # consumed memory row ids
    flag: str | None = None  # set on degraded read -> digest
    block: str = ""  # prompt block ("" = no priors)


def priors_block(arc_kind: str, query_text: str, *, k: int = 3) -> PriorsSeed:
    """Layer-2 seed-read. STRICTLY fail-open: absent/empty DB -> empty seed;
    corrupt DB / any exception -> empty seed + flag. Never raises, never
    blocks an arc start."""
    try:
        from .. import arc_memory  # lazy: keeps arc import time clean

        words = re.findall(r"[A-Za-z0-9]{3,}", query_text)[:8]
        fts = " OR ".join(words)
        rows = arc_memory.arc_search(arc_kind=arc_kind, query=fts, limit=k)
        if not rows:
            rows = arc_memory.arc_search(arc_kind=arc_kind, limit=k)  # recency fallback
        if not rows:
            return PriorsSeed()
        bullets = [f"- {r['title']}: {(r.get('body') or '')[:200]}" for r in rows]
        block = (
            f"PRIORS — lessons from previous {arc_kind} runs "
            "(consider, do not follow blindly):\n" + "\n".join(bullets)
        )
        return PriorsSeed(rows=[r["id"] for r in rows], block=block)
    except Exception as exc:
        return PriorsSeed(flag=f"priors seed-read degraded: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# Bounded critique -> rewrite loop — shared verify finalize
# (generate-critique-rewrite, then score-and-retry)
# ---------------------------------------------------------------------------

# On a genuine verify refutation, feed the critique back to the wrap maker and
# re-run verify at most this many times before halting. 1 mirrors CLAUDE.md
# rule #7 (one retry, then degrade). The graph arc passes its lever value
# (CHIMERA_GRAPH_REPAIR_LAPS) through finalize_verify_with_repair; this
# constant is the lever's default.
MAX_VERIFY_REPAIRS = 1

REWRITE_STAGE = "wrap"


def repair_brief(opinions: Sequence[Any], attempt: int) -> str:
    """Turn the refuting verify opinions into a rewrite brief for the wrap maker.

    Instead of discarding the critique when verify fails, feed it back so the
    next wrap resolves it. Injected into each arc's `_wrap_prompt`.
    """
    refuting = [o for o in opinions if o is not None and getattr(o, "refuted", False)]
    if refuting:
        bullets = "\n".join(f"- [{o.severity}] {o.reason}" for o in refuting)
    else:
        bullets = (
            "- (panel refused to substantiate the artifact; strengthen sourcing "
            "and internal consistency)"
        )
    return (
        f"PRIOR DRAFT (repair attempt {attempt}) WAS REFUTED by the verify panel. "
        "Produce a corrected artifact that resolves every point below. Keep all "
        "inputs verbatim — do not re-grade or invent evidence:\n"
        f"{bullets}"
    )


def finalize_with_repair(
    state: Any,
    result: Any,
    opinions: Sequence[Any],
    *,
    arc_name: str,
    verification_path: Path,
    save: Callable[[Any], Any],
    on_pass: Callable[[Any], None] | None = None,
    rewrite_stage: str = REWRITE_STAGE,
    max_repairs: int = MAX_VERIFY_REPAIRS,
) -> Any:
    """Shared verify finalize for the wrap+lite-verify arcs.

    Given a computed VerifyResult (`result`) and the raw `opinions`:
      - pass -> stage='done', run on_pass (memory summary), write verification.json.
      - fail with genuine refutation (>=2 valid critics) and repair budget left ->
        stash the critique as state.repair_brief, reset state.verify_opinions,
        rewind to `rewrite_stage`. No verification.json (non-terminal).
      - fail thin (<2 valid) or budget exhausted -> stage='halted', verification.json.

    Requires state to carry: stage, verify_repairs (int), repair_brief (str|None),
    verify_opinions (dict), failure (str|None). Uses state.log if present
    (duck-typed: a future arc without one still finalizes). Mutates and
    returns state.
    """
    if result.passed:
        state.stage = "done"
        if on_pass is not None:
            on_pass(state)
        verification_path.write_text(result.model_dump_json(indent=2))
        save(state)
        return state

    genuine = result.valid_critic_count >= 2
    if genuine and state.verify_repairs < max_repairs:
        state.verify_repairs += 1
        state.repair_brief = repair_brief(opinions, state.verify_repairs)
        state.verify_opinions = {}  # fresh panel for the next lap
        state.stage = rewrite_stage
        if hasattr(state, "log"):
            state.log.append(
                f"VERIFY_REPAIR attempt={state.verify_repairs} "
                f"({result.unrefuted_count}/{result.valid_critic_count} unrefuted) "
                f"-> re-{rewrite_stage}"
            )
        save(state)
        return state

    state.stage = "halted"
    if not genuine:
        state.failure = (
            f"{arc_name} arc halted: lite verify failed — thin panel "
            f"({result.valid_critic_count} valid critic(s), cannot repair)"
        )
    else:
        state.failure = (
            f"{arc_name} arc halted: lite verify failed after {state.verify_repairs} "
            f"repair(s) ({result.unrefuted_count}/{result.valid_critic_count} unrefuted)"
        )
    verification_path.write_text(result.model_dump_json(indent=2))
    save(state)
    return state


# ---------------------------------------------------------------------------
# accumulate_verify_opinion — the shared _submit_verify body (all seven arcs)
# ---------------------------------------------------------------------------


def accumulate_verify_opinion(
    state: Any,
    label: str,
    payload_obj: Any,
    *,
    error_cls: type[Exception],
    finalize: Callable[[list], Any],
    save: Callable[[Any], Any],
    load: Callable[[], Any],
    stage_error: Callable[[str, Any], str] | None = None,
) -> Any:
    """Shared `_submit_verify` body for the seven lite-verify arcs.

    Stage-guards on "verify" (raising *error_cls*; *stage_error* lets a future
    arc override the default message format), slots *payload_obj* (or None,
    for a recoverable null) into state.verify_opinions[label] through the
    schema gate, pops the label's first_issued stamp, and saves. Once
    VERIFY_KEYS is fully present, decodes the three CriticOpinions (None for
    a degraded slot) and dispatches to *finalize*, then returns load() — the
    fresh, finalize-persisted state — rather than the pre-finalize object, so
    a repair lap's stage rewind is reflected immediately. Otherwise returns
    the (non-terminal) state as-is.
    """
    if state.stage != "verify":
        message = (
            stage_error(label, state.stage)
            if stage_error is not None
            else f"verify submission '{label}' but arc is in stage {state.stage}"
        )
        raise error_cls(message)
    if payload_obj is None:
        state.verify_opinions[label] = None
    else:
        opinion = gate_critic_opinion(payload_obj)
        state.verify_opinions[label] = opinion.model_dump()
    state.first_issued.pop(label, None)
    save(state)
    if VERIFY_KEYS.issubset(state.verify_opinions):
        opinions = []
        for i in (1, 2, 3):
            raw = state.verify_opinions.get(f"verify:critic{i}")
            opinions.append(CriticOpinion.model_validate(raw) if raw else None)
        finalize(opinions)
        return load()
    return state


# ---------------------------------------------------------------------------
# finalize_verify_with_repair — the shared finalize_verify body (six arcs)
# ---------------------------------------------------------------------------


def finalize_verify_with_repair(
    task_dir: Path,
    load: Callable[[], Any],
    opinions: Sequence[Any],
    *,
    error_cls: type[Exception],
    arc_name: str,
    save: Callable[[Any], Any],
    on_pass: Callable[[Any], None] | None = None,
    stage_error: Callable[[Any], str] | None = None,
    max_repairs: int = MAX_VERIFY_REPAIRS,
) -> Any:
    """Shared `finalize_verify` body for the six wrap+lite-verify arcs
    Reloads a fresh state via *load* (the 2026-07-02 fatal-revert fix: the
    state finalized is always the freshest persisted one, never a stale
    in-memory object from before the verify-key completion), stage-guards on
    "verify" (raising *error_cls*; *stage_error* lets a future arc override
    the default message format), computes the lite verdict, and dispatches to
    finalize_with_repair. *max_repairs* passes the graph arc's lever value
    (CHIMERA_GRAPH_REPAIR_LAPS); MAX_VERIFY_REPAIRS is its default.
    """
    state = load()
    if state.stage != "verify":
        message = (
            stage_error(state.stage)
            if stage_error is not None
            else f"finalize_verify in wrong stage: {state.stage}"
        )
        raise error_cls(message)
    result = lite.verdict("lite", opinions)
    return finalize_with_repair(
        state,
        result,
        opinions,
        arc_name=arc_name,
        verification_path=task_dir / "verification.json",
        save=save,
        on_pass=on_pass,
        max_repairs=max_repairs,
    )


# ---------------------------------------------------------------------------
# load_task_record — hoisted task.json loader (M5-adjacent convenience)
# ---------------------------------------------------------------------------


def load_task_record(task_dir: Path) -> Any:
    """Read tasks/<id>/task.json into a TaskRecord, or None if absent/corrupt.

    Fail-open on the two error classes each arc's own loader independently
    narrowed to (ValidationError, OSError) — a real bug elsewhere is not
    swallowed. Returns None (not raise) so start()/initialize() callers can
    surface their own arc-specific "missing required field" error.
    """
    from pydantic import ValidationError

    from ..models import TaskRecord

    record_path = task_dir / "task.json"
    if not record_path.exists():
        return None
    try:
        return TaskRecord.model_validate_json(record_path.read_text())
    except (ValidationError, OSError):
        return None

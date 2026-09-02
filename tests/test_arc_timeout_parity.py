"""N1 — shared expiry parity (spec §3).

1. Pure-function boundary tests on `expired_labels` itself (exact `now`
   injection, no arc involved): graph's `node:` labels get the 1800s work
   ceiling, not the 300s default.
2. Per-arc integration: stamps persist via `pending_calls` (keep-earliest);
   an aged stamp expires the label and routes kind="timeout" through the
   recoverable-null path (a verify-critic label degrades; the arc's
   first-stage/primary-maker label halts).
3. graph node-ceiling integration: a `node:` label is NOT expired at
   300s < t < 1800s and IS expired past 1800s, through the real
   `arc.expire_timeouts` path — and an expired work node DEGRADES
   (recoverable), it never halts the run.
4. Counters land in `agent_calls_timed_out`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chimera.arcs._common import expired_labels
from chimera.arcs.graph import CALL_CEILINGS as GRAPH_CEILINGS
from chimera.models import AgentCall
from tests.arc_drivers import (
    ARC_IDS,
    ARCS,
    _gr_node_payload,
    _gr_plan_payload,
    ago,
    get_issued_at,
    graph_fresh,
    set_issued_at,
    valid_opinion,
)

STAGE_ARCS = ARCS
STAGE_ARC_IDS = ARC_IDS

# ---------------------------------------------------------------------------
# Pure-function boundary tests — no arc involved, exact `now` injection
# ---------------------------------------------------------------------------


class _FirstIssuedState:
    def __init__(self, first_issued: dict[str, str]):
        self.first_issued = first_issued


def _call(label: str, issued_at: str) -> AgentCall:
    return AgentCall(label=label, prompt="x", schema_name="GraphNodeOutput", model="opus",
                      phase="run", issued_at=issued_at)


def test_node_label_not_expired_between_default_and_work_ceiling():
    """graph's `node:` labels carry the 1800s ceiling, not the 300s default —
    at t=1000s (> 300s, < 1800s) they must NOT be expired."""
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    stamp = t0.strftime("%Y-%m-%dT%H:%M:%SZ")
    state = _FirstIssuedState({"node:gather-a": stamp})
    calls = [_call("node:gather-a", stamp)]
    now = t0 + timedelta(seconds=1000)
    assert expired_labels(state, calls, ceilings=GRAPH_CEILINGS, now=now) == []


def test_node_label_expired_past_work_ceiling():
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    stamp = t0.strftime("%Y-%m-%dT%H:%M:%SZ")
    state = _FirstIssuedState({"node:gather-a": stamp})
    calls = [_call("node:gather-a", stamp)]
    now = t0 + timedelta(seconds=1900)
    assert expired_labels(state, calls, ceilings=GRAPH_CEILINGS, now=now) == ["node:gather-a"]


def test_default_300s_ceiling_applies_to_unlisted_labels():
    """Labels without a CALL_CEILINGS entry (the verify critics) ride the
    300s default. plan/wrap moved to the 1800s ceiling in the 2026-08-28
    batch (audit OP-15) — a container reclaim mid-plan no longer kills the
    task on a hair trigger."""
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    stamp = t0.strftime("%Y-%m-%dT%H:%M:%SZ")
    state = _FirstIssuedState({"verify:critic1": stamp})
    calls = [_call("verify:critic1", stamp)]
    assert expired_labels(state, calls, ceilings=GRAPH_CEILINGS,
                           now=t0 + timedelta(seconds=200)) == []
    assert expired_labels(state, calls, ceilings=GRAPH_CEILINGS,
                           now=t0 + timedelta(seconds=400)) == ["verify:critic1"]


def test_plan_and_wrap_ride_the_long_ceiling():
    """OP-15: plan/wrap are frontier-tier tool loops; they expire at the node
    ceiling (1800s), not the 300s default — and expiry there still HALTS
    (the halting class is unchanged, only the trigger is sane)."""
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    stamp = t0.strftime("%Y-%m-%dT%H:%M:%SZ")
    for label in ("plan", "wrap"):
        state = _FirstIssuedState({label: stamp})
        calls = [_call(label, stamp)]
        assert expired_labels(state, calls, ceilings=GRAPH_CEILINGS,
                               now=t0 + timedelta(seconds=400)) == []
        assert expired_labels(state, calls, ceilings=GRAPH_CEILINGS,
                               now=t0 + timedelta(seconds=1900)) == [label]


# ---------------------------------------------------------------------------
# Per-arc integration drills
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_stamps_persist_via_pending_calls(tmp_path, harness):
    arc, state, _ = harness.fresh(tmp_path)
    arc.pending_calls(state)  # stamps first_issued
    first = get_issued_at(harness.arc_kind, state, harness.first_stage_label)
    assert first is not None
    arc.pending_calls(state)  # calling again must NOT reset the stamp (keep earliest)
    assert get_issued_at(harness.arc_kind, state, harness.first_stage_label) == first


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_aged_primary_stage_label_expires_and_halts(tmp_path, harness):
    """A timeout at the arc's first (primary-maker) stage is a learnable null
    that still halts — same class as an explicit --null, never a new halt
    path (M3)."""
    arc, state, _ = harness.fresh(tmp_path)
    arc.pending_calls(state)
    set_issued_at(harness.arc_kind, state, harness.first_stage_label, ago(10_000))
    expired = arc.expire_timeouts(state)
    assert harness.first_stage_label in expired
    assert state.phase == "failed"
    assert state.audit.agent_calls_timed_out >= 1


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_aged_verify_critic_label_expires_and_degrades(tmp_path, harness):
    """A timeout at a recoverable verify-critic label slots a None and does
    NOT halt the arc — it never deadlocks; the other two critics are still
    awaitable and the arc still completes."""
    arc, state, _ = harness.to_verify(tmp_path)
    arc.pending_calls(state)
    set_issued_at(harness.arc_kind, state, "verify:critic1", ago(10_000))
    expired = arc.expire_timeouts(state)
    assert "verify:critic1" in expired
    assert state.phase == "verify"
    assert state.audit.agent_calls_timed_out >= 1
    state = arc.submit(state, "verify:critic2", valid_opinion(False), kind="null")
    state = arc.submit(state, "verify:critic3", valid_opinion(False), kind="null")
    assert state.phase == "complete"


# ---------------------------------------------------------------------------
# graph node ceiling — real arc.expire_timeouts, not the pure fn; and an
# expired WORK node is recoverable (degrades), never a halt.
# ---------------------------------------------------------------------------


def _to_run(tmp_path):
    arc, state, task_dir = graph_fresh(tmp_path)
    state = arc.submit(state, "plan", _gr_plan_payload(), kind="null")
    return arc, state, task_dir


def test_node_label_not_expired_between_default_and_ceiling_real_arc(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    arc.pending_calls(state)
    set_issued_at("graph", state, "node:gather-a", ago(1000))  # 300s < 1000s < 1800s
    expired = arc.expire_timeouts(state)
    assert "node:gather-a" not in expired


def test_expired_node_label_degrades_and_the_run_continues(tmp_path):
    arc, state, _ = _to_run(tmp_path)
    arc.pending_calls(state)
    set_issued_at("graph", state, "node:gather-a", ago(1900))  # past the 1800s ceiling
    expired = arc.expire_timeouts(state)
    assert expired == ["node:gather-a"]
    assert state.stage == "run"  # degraded, not halted
    assert state.outputs["node:gather-a".split(":", 1)[1]] is None
    assert state.audit.agent_calls_timed_out >= 1
    # the run still completes through the barrier
    state = arc.submit(state, "node:gather-b", _gr_node_payload("gather-b"), kind="null")
    assert {c.label for c in arc.pending_calls(state)} == {"node:judge-gathers"}


# ---------------------------------------------------------------------------
# N4 — the timeout-completes-the-verify-panel drill.
#
# finalize_verify (triggered from inside _handle_null's recoverable route)
# reloads arc-state.json and saves a FRESH terminal object; expire_timeouts
# must rebind to it so a later save cannot revert the terminal state on disk.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("harness", STAGE_ARCS, ids=STAGE_ARC_IDS)
def test_timeout_that_completes_verify_panel_finalizes_durably(tmp_path, harness):
    arc, state, task_dir = harness.to_verify(tmp_path)
    arc.pending_calls(state)  # stamps first_issued for all three verify:criticN labels
    state = arc.submit(state, "verify:critic1", valid_opinion(False), kind="null")
    state = arc.submit(state, "verify:critic2", valid_opinion(False), kind="null")
    # only verify:critic3 is still pending — the panel is not yet finalized
    assert state.phase == "verify"
    set_issued_at(harness.arc_kind, state, "verify:critic3", ago(10_000))

    # mimic the CLI (cmd_tick/cmd_arc_next): call expire_timeouts, then reload
    # rather than re-saving the (now stale) local `state` reference.
    expired = arc.expire_timeouts(state)
    assert expired == ["verify:critic3"]

    fresh = arc.load()
    # 2 valid unrefuted opinions survive proportional-majority (2 > 2/2) —
    # the timeout is the completing event and must finalize to "complete" ON
    # DISK, not revert to "verify".
    assert fresh.phase == "complete"
    assert (task_dir / "verification.json").exists()

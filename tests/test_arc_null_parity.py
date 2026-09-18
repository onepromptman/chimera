"""M3 — null-degrade unification (spec §3), updated for the 2-critic panel
(2026-09-16, tasks/20260916-optimize-dag/artifacts/graph-output.md):

1. A null at verify:critic* slots as a recoverable None (the arc never
   crashes or deadlocks on it) rather than a non-recoverable halt. With only
   2 critic slots total, a single null already leaves just 1 valid opinion —
   too thin to pass (survives() requires >= 2 valid) — so the panel still
   reaches a TERMINAL state (failed), just not a passing one; the acceptance
   gate a null-tolerant 3-critic panel used to demonstrate no longer exists
   at panel size 2.
2. Both critics valid and unrefuted -> complete, computed over 2 valid
   opinions (the actual acceptance path at this panel size).
3. A primary-maker null still halts (the arc's first stage) via the
   non-recoverable branch of the arc's own null handler.
"""

from __future__ import annotations

import pytest

from tests.arc_drivers import ARC_IDS, ARCS, valid_opinion


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_both_critics_valid_reaches_complete(tmp_path, harness):
    arc, state, _ = harness.to_verify(tmp_path)
    state = arc.submit(state, "verify:critic1", valid_opinion(refuted=False), kind="null")
    state = arc.submit(state, "verify:critic2", valid_opinion(refuted=False), kind="null")
    assert state.phase == "complete"
    verdict = arc.verify_verdict(state)
    assert verdict.passed is True
    assert verdict.valid_critic_count == 2
    assert verdict.unrefuted_count == 2


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_one_null_one_valid_degrades_but_is_too_thin_to_pass(tmp_path, harness):
    """The null still slots recoverably (no crash, no deadlock) — the arc
    completes the panel and reaches a terminal state — but with only 1 valid
    opinion left, survives() fails it as too thin rather than passing it."""
    arc, state, _ = harness.to_verify(tmp_path)
    state = arc.submit(state, "verify:critic1", None, kind="null")
    state = arc.submit(state, "verify:critic2", valid_opinion(refuted=False), kind="null")
    assert state.phase == "failed"
    verdict = arc.verify_verdict(state)
    assert verdict.passed is False
    assert verdict.valid_critic_count == 1


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_primary_maker_null_still_halts(tmp_path, harness):
    arc, state, _ = harness.fresh(tmp_path)
    state = arc.submit(state, harness.first_stage_label, None, kind="null")
    assert state.phase == "failed"
    assert state.failure is not None

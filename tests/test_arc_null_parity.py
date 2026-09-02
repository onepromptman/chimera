"""M3 — null-degrade unification: the acceptance gate (spec §3).

1. THE acceptance gate: 2 valid CriticOpinion + 1 --null at verify:critic* ->
   arc reaches its terminal/complete phase and verify_verdict computes over
   the 2 valid opinions.
2. A primary-maker null still halts (the arc's first stage) via
   dispatch_null's default path.
"""

from __future__ import annotations

import pytest

from tests.arc_drivers import ARC_IDS, ARCS, valid_opinion


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_two_valid_one_null_verify_reaches_complete(tmp_path, harness):
    arc, state, _ = harness.to_verify(tmp_path)
    state = arc.submit(state, "verify:critic1", None, kind="null")
    state = arc.submit(state, "verify:critic2", valid_opinion(refuted=False), kind="null")
    state = arc.submit(state, "verify:critic3", valid_opinion(refuted=False), kind="null")
    assert state.phase == "complete"
    verdict = arc.verify_verdict(state)
    assert verdict.passed is True
    assert verdict.valid_critic_count == 2
    assert verdict.unrefuted_count == 2


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_primary_maker_null_still_halts(tmp_path, harness):
    arc, state, _ = harness.fresh(tmp_path)
    state = arc.submit(state, harness.first_stage_label, None, kind="null")
    assert state.phase == "failed"
    assert state.failure is not None

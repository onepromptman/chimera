"""N1 — verify-stage CriticOpinion payloads route through the schema gate in
all seven arcs (spec §3): a malformed payload raises SchemaGateError and
leaves state untouched; the same label accepts a valid resubmission
afterward (state-safety, N8)."""

from __future__ import annotations

import pytest

from chimera.verify.schema_gate import SchemaGateError
from tests.arc_drivers import ARC_IDS, ARCS, malformed_opinion, valid_opinion


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_malformed_verify_critic_payload_raises_and_state_survives(tmp_path, harness):
    arc, state, _ = harness.to_verify(tmp_path)
    before_opinions = dict(state.verify_opinions)
    before_phase = state.phase

    with pytest.raises(SchemaGateError):
        arc.submit(state, "verify:critic1", malformed_opinion(), kind="null")

    assert state.phase == before_phase
    assert dict(state.verify_opinions) == before_opinions

    # State-safety (N8): the same label accepts a valid resubmission.
    state = arc.submit(state, "verify:critic1", valid_opinion(refuted=False), kind="null")
    assert "verify:critic1" in state.verify_opinions

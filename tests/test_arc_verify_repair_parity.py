"""Verify-repair parity — the bounded critique -> rewrite loop fires identically
across the wrap+lite-verify arcs (critique-rewrite, score-and-retry).

Invariant (arcs/_common.finalize_with_repair):
  - a GENUINE verify refutation (>=2 valid critics all refuting) does NOT halt;
    it stashes the critique as state.repair_brief, resets verify_opinions, and
    rewinds to the wrap maker for one bounded lap. No verification.json yet.
  - the re-issued wrap call carries the critique ("REFUTED ...").
  - a THIN panel (<2 valid critics) can't be fixed by rewriting, so it halts
    immediately without spending a repair lap.

research and design are excluded on purpose: research finalizes through its own
judge-panel machinery (the fleet's reference implementation), and design realizes
critique -> rewrite natively (max-5 iterations + plateau). Budget exhaustion and
repair-then-pass are covered per-arc in each arc's own suite.
"""

from __future__ import annotations

import pytest

from tests.arc_drivers import ARCS, valid_opinion

# The seven arcs wired to the shared finalize_with_repair helper (graph joins
# the six wrap+lite-verify arcs; its lap budget is lever-set but defaults to
# the same MAX_VERIFY_REPAIRS=1, so the parity contract is identical).
REPAIR_ARCS = [h for h in ARCS if h.arc_kind in {"proposal", "build", "n8n", "comms", "reflect", "gemini", "graph"}]
REPAIR_ARC_IDS = [h.arc_kind for h in REPAIR_ARCS]


@pytest.mark.parametrize("harness", REPAIR_ARCS, ids=REPAIR_ARC_IDS)
def test_genuine_refutation_triggers_one_repair(tmp_path, harness):
    arc, state, task_dir = harness.to_verify(tmp_path)
    assert state.phase == "verify"

    for slot in (1, 2, 3):
        state = arc.submit(state, f"verify:critic{slot}", valid_opinion(refuted=True), kind="null")

    # Looped back to the wrap maker instead of halting.
    assert state.phase == "wrap"
    assert state.verify_repairs == 1
    assert state.repair_brief and "REFUTED" in state.repair_brief
    assert dict(state.verify_opinions) == {}  # fresh panel for the next lap
    # No terminal verdict written on a repair lap.
    assert not (task_dir / "verification.json").exists()
    # The re-issued wrap call carries the critique.
    calls = arc.pending_calls(state)
    assert len(calls) == 1 and calls[0].label == "wrap"
    assert "REFUTED" in calls[0].prompt


@pytest.mark.parametrize("harness", REPAIR_ARCS, ids=REPAIR_ARC_IDS)
def test_thin_panel_halts_without_repair(tmp_path, harness):
    arc, state, task_dir = harness.to_verify(tmp_path)

    # One valid refuting critic; the other two degrade to null -> only 1 valid.
    state = arc.submit(state, "verify:critic1", valid_opinion(refuted=True), kind="null")
    state = arc.submit(state, "verify:critic2", None, kind="null")
    state = arc.submit(state, "verify:critic3", None, kind="null")

    assert state.phase == "failed"  # halted
    assert state.verify_repairs == 0
    assert "thin panel" in (state.failure or "")
    assert (task_dir / "verification.json").exists()

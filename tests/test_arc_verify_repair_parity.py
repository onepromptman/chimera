"""Verify-repair parity — the bounded critique -> rewrite loop, held for every
arc in tests/arc_drivers.py::ARCS (Gohel loop patterns #1/#2).

Panel is 2 critics (2026-09-16, S1/S4): a FAILING panel needs a `fatal`
refutation — under the fatal-only veto, a `material`/`cosmetic` refutation
survives and lands in open_findings instead of halting or repairing.

Invariant (the finalize tail of each arc's _submit_verify):
  - a GENUINE verify refutation (>=2 valid critics, a fatal veto fires) does
    NOT halt; it stashes the critique as state.repair_brief, resets
    verify_opinions, and rewinds to the wrap maker for one bounded lap. No
    verification.json yet.
  - the re-issued wrap call carries the critique ("REFUTED ...").
  - a THIN panel (<2 valid critics) can't be fixed by rewriting, so it halts
    immediately without spending a repair lap.

Budget exhaustion and repair-then-pass are covered per-arc in each arc's own
suite.
"""

from __future__ import annotations

import pytest

from tests.arc_drivers import ARCS, valid_opinion

# Every arc in ARCS finalizes through the wrap + lite-verify shape today (the
# graph arc's lap budget is lever-set, defaulting to 1). An arc that finalized
# some other way would drop out of this list, not out of the suite.
REPAIR_ARCS = ARCS
REPAIR_ARC_IDS = [h.arc_kind for h in REPAIR_ARCS]


@pytest.mark.parametrize("harness", REPAIR_ARCS, ids=REPAIR_ARC_IDS)
def test_genuine_refutation_triggers_one_repair(tmp_path, harness):
    arc, state, task_dir = harness.to_verify(tmp_path)
    assert state.phase == "verify"

    for slot in (1, 2):
        state = arc.submit(
            state,
            f"verify:critic{slot}",
            valid_opinion(refuted=True, severity="fatal"),
            kind="null",
        )

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

    # One valid refuting critic; the other degrades to null -> only 1 valid.
    state = arc.submit(state, "verify:critic1", valid_opinion(refuted=True), kind="null")
    state = arc.submit(state, "verify:critic2", None, kind="null")

    assert state.phase == "failed"  # halted
    assert state.verify_repairs == 0
    assert "thin panel" in (state.failure or "")
    assert (task_dir / "verification.json").exists()

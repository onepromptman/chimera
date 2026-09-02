"""M5 part 3 — fail-open Layer-2 seed-read parity (spec §3), all seven arcs:

1. absent DB -> arc starts clean, priors.block == "", no flag.
2. seeded DB (via arc_memory.arc_write) -> priors.rows non-empty AND the
   first pending call's prompt actually CONTAINS the block — "wired but
   unread" is F10's own failure mode, so this asserts consumption, not just
   wiring.
3. corrupt DB file -> arc still starts, flag set, block stays "".

Every test already runs on a per-test isolated memory DB via conftest's
autouse `_isolated_memory_db` fixture, which monkeypatches
`chimera.arc_memory.DEFAULT_DB_PATH` (the exact patch point `priors_block`
resolves at call time, since it calls `arc_memory.arc_search(...)` with no
`db_path` override) — declaring it as a parameter here just gets its Path.
"""

from __future__ import annotations

import json

import pytest

from chimera import arc_memory, digest
from chimera.queue import Queue
from tests.arc_drivers import ARC_IDS, ARCS
from tests.test_queue import make_spec


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_absent_db_starts_clean(tmp_path, harness, _isolated_memory_db):
    assert not _isolated_memory_db.exists()
    arc, state, _ = harness.fresh(tmp_path)
    assert state.priors is not None
    assert state.priors.block == ""
    assert state.priors.rows == []
    assert state.priors.flag is None


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_seeded_db_is_consumed_in_first_prompt(tmp_path, harness, _isolated_memory_db):
    arc_memory.arc_write(
        arc_kind=harness.arc_kind,
        arc_id="seed-1",
        title="lesson from a previous run",
        body="watch out for the thing that broke last time",
        tags="terminal",
    )
    arc, state, _ = harness.fresh(tmp_path)
    assert state.priors is not None
    assert state.priors.rows, "priors seed-read found nothing despite a seeded row"
    calls = arc.pending_calls(state)
    assert calls, "arc has no pending call to check consumption against"
    assert state.priors.block != ""
    assert state.priors.block in calls[0].prompt


@pytest.mark.parametrize("harness", ARCS, ids=ARC_IDS)
def test_corrupt_db_degrades_to_empty_priors_with_flag(tmp_path, harness, _isolated_memory_db):
    _isolated_memory_db.write_bytes(b"not a sqlite database")
    arc, state, _ = harness.fresh(tmp_path)
    assert state.priors is not None
    assert state.priors.block == ""
    assert state.priors.rows == []
    assert state.priors.flag is not None
    assert "priors seed-read degraded" in state.priors.flag


# ---------------------------------------------------------------------------
# spec §1.4 — a degraded priors seed-read flag surfaces to the digest.
# _arc_flags (digest.py) reads arc-state.json as plain JSON and appends
# priors.flag to the task's flag list when present, fail-open on any read
# error. Crib: Queue fixture from conftest (`queue`), make_spec from
# test_queue.py.
# ---------------------------------------------------------------------------


def _write_arc_state_with_priors_flag(queue: Queue, task_id: str, flag: str) -> None:
    state_path = queue.task_dir(task_id) / "arc-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"priors": {"rows": [], "flag": flag, "block": ""}}), encoding="utf-8"
    )


def test_priors_degraded_flag_surfaces_in_digest(queue: Queue):
    tid = queue.create(make_spec("priors-flagged"), "ready", by="t").spec.id
    queue.claim(tid, "w")
    flag = "priors seed-read degraded: OperationalError"
    _write_arc_state_with_priors_flag(queue, tid, flag)

    digest_text = digest.render(queue)
    assert flag in digest_text
    assert tid in digest_text


def test_priors_flag_absent_when_not_degraded(queue: Queue):
    tid = queue.create(make_spec("priors-clean"), "ready", by="t").spec.id
    queue.claim(tid, "w")
    state_path = queue.task_dir(tid) / "arc-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"priors": {"rows": [1], "flag": None, "block": "PRIORS — ..."}}),
        encoding="utf-8",
    )

    digest_text = digest.render(queue)
    assert "priors seed-read degraded" not in digest_text


def test_missing_arc_state_does_not_break_digest(queue: Queue):
    """No arc-state.json yet (parked/awaiting-input task) — _arc_flags must
    not raise, and the digest must still render."""
    tid = queue.create(make_spec("priors-no-state"), "awaiting-input", by="t").spec.id
    digest_text = digest.render(queue)
    assert tid in digest_text

"""M5 part 3 — fail-open Layer-2 seed-read parity (spec §3), every arc in ARCS:

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
import sqlite3

import pytest

from chimera import arc_memory, digest, memory
from chimera.arcs import _common
from chimera.arcs._common import priors_block
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
# P1-7 (audit 2026-09-02) — a reserved FTS word in the ask must not empty the
# seed, and the recency fallback lives OUTSIDE the primary query's guard so a
# failed match still returns rows.
# ---------------------------------------------------------------------------


def test_reserved_fts_words_in_the_ask_still_return_priors(_isolated_memory_db):
    """Reproduces the audit bug: "NOT"/"AND"/"OR"/"NEAR" are FTS5 operators, so
    the unquoted OR-join raised a syntax error and the blanket except returned
    an empty seed with no flag and no fallback."""
    arc_memory.arc_write(
        arc_kind="graph",
        arc_id="seed-fts",
        title="run-summary-1",
        body="the previous run compared two options",
        tags="terminal",
    )
    seed = priors_block("graph", "compare NOT and OR near 2026")
    assert seed.flag is None
    assert seed.rows, "reserved words emptied the priors seed"
    assert "run-summary-1" in seed.block


def test_every_fts_token_is_quoted():
    assert memory.fts_or_query("compare NOT and OR near 2026") == (
        '"compare" OR "NOT" OR "and" OR "near" OR "2026"'
    )
    assert memory.fts_or_query("!!! ?? ..") == ""


def test_non_latin_and_accented_asks_reach_layer_one(_isolated_memory_db):
    """The tokenizer is `\\w{3,}` (unicode), not `[A-Za-z0-9]{3,}`: a CJK or
    accented ask used to tokenize to nothing, so layer 1 was silently
    unreachable for it — an empty query is treated as "no query"."""
    _write_l1(
        _isolated_memory_db,
        title="データベース の 方針",
        body="データベース は sqlite を選んだ",
    )
    _write_l1(
        _isolated_memory_db,
        mem_type="project",
        title="décision sur le café",
        body="le café est déjà choisi",
    )

    assert memory.fts_or_query("データベース の 方針") == '"データベース"'
    assert memory.fts_or_query("décision café") == '"décision" OR "café"'

    cjk = memory.search_general(
        query=memory.fts_or_query("データベース の 方針"), db_path=_isolated_memory_db
    )
    assert [r["title"] for r in cjk] == ["データベース の 方針"]

    accented = memory.search_general(
        query=memory.fts_or_query("décision café"), db_path=_isolated_memory_db
    )
    assert [r["title"] for r in accented] == ["décision sur le café"]

    seed = priors_block("graph", "データベース の 方針")
    assert "データベース" in seed.block and seed.flag is None


def test_punctuation_only_ask_falls_back_to_recency(_isolated_memory_db):
    arc_memory.arc_write(
        arc_kind="graph", arc_id="seed-recency", title="run-summary-1", body="recent lesson"
    )
    seed = priors_block("graph", "!!! ?? ..")
    assert seed.flag is None
    assert seed.rows and "recent lesson" in seed.block


def test_recency_fallback_survives_a_raising_primary_query(monkeypatch, _isolated_memory_db):
    """The structural half of P1-7: the fallback must sit OUTSIDE the primary
    query's guard. Quoting closed the known syntax path, so this proves the
    shape directly — a raising keyword search still returns the recency rows,
    with the degrade flagged for the digest."""
    arc_memory.arc_write(
        arc_kind="graph", arc_id="seed-fb", title="run-summary-1", body="a prior lesson"
    )
    real_search = arc_memory.arc_search

    def only_recency_works(*, query=None, **kwargs):
        if query:
            raise sqlite3.OperationalError("fts5: syntax error near something")
        return real_search(query=None, **kwargs)

    monkeypatch.setattr(arc_memory, "arc_search", only_recency_works)
    seed = priors_block("graph", "anything at all")
    assert seed.rows, "a failed keyword search must not skip the recency fallback"
    assert "a prior lesson" in seed.block
    assert seed.flag is not None and "OperationalError" in seed.flag


def test_a_failing_layer_one_read_does_not_lose_the_arc_rows(monkeypatch, _isolated_memory_db):
    arc_memory.arc_write(
        arc_kind="graph", arc_id="seed-l1", title="run-summary-1", body="a prior lesson"
    )

    def boom(**kwargs):
        raise sqlite3.OperationalError("no such table: memories_fts")

    monkeypatch.setattr(memory, "search_general", boom)
    seed = priors_block("graph", "a prior lesson")
    assert "a prior lesson" in seed.block
    assert "GENERAL PRIORS" not in seed.block
    assert seed.flag is not None


# ---------------------------------------------------------------------------
# Proposal §7 — the priors block also reads layer 1 (the operator's own
# feedback/project/pattern rows), under its own sub-header, still fail-open.
# ---------------------------------------------------------------------------


def _write_l1(db_path, *, agent="user", mem_type="feedback", title="t", body="b") -> None:
    with memory._connect(db_path) as conn:
        conn.executescript(memory.SCHEMA_SQL)
        conn.executescript(memory.FTS_SYNC_SQL)
        conn.execute(
            "INSERT INTO memories (agent, type, title, body) VALUES (?, ?, ?, ?)",
            (agent, mem_type, title, body),
        )
        conn.commit()


def test_general_priors_surface_from_layer_one(_isolated_memory_db):
    _write_l1(
        _isolated_memory_db,
        title="dashboards need a source line",
        body="feedback: always cite the dashboard source",
    )
    seed = priors_block("graph", "build the dashboard")
    assert "GENERAL PRIORS" in seed.block
    assert "dashboards need a source line" in seed.block
    assert seed.rows


def test_general_priors_ignore_arc_rows_and_other_types(_isolated_memory_db):
    _write_l1(
        _isolated_memory_db,
        mem_type="reference",
        title="dashboard reference sheet",
        body="raw dashboard lookup",
    )
    seed = priors_block("graph", "build the dashboard")
    assert "GENERAL PRIORS" not in seed.block


def test_priors_block_is_bounded(_isolated_memory_db):
    for n in range(3):
        arc_memory.arc_write(
            arc_kind="graph",
            arc_id=f"seed-{n}",
            title=f"run-summary-{n} dashboard",
            body="dashboard " * 500,
        )
    for n in range(3):
        _write_l1(
            _isolated_memory_db,
            title=f"dashboard feedback {n}",
            body="dashboard " * 500,
        )
    seed = priors_block("graph", "build the dashboard")
    assert _common.PRIORS_BLOCK_MAX_CHARS == 1500  # the number CLAUDE.md claims
    assert 0 < len(seed.block) <= _common.PRIORS_BLOCK_MAX_CHARS


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
    """No arc-state.json yet (freshly created task, not yet ticked) —
    _arc_flags must not raise, and the digest must still render."""
    tid = queue.create(make_spec("priors-no-state"), "ready", by="t").spec.id
    digest_text = digest.render(queue)
    assert tid in digest_text

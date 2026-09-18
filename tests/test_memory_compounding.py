"""Memory redesign (audit 2026-09-02, §5.2) — the compounding contract.

Four invariants, one file, because they are one story: what a terminal arc
writes to layer 2, and whether the write can ever hurt the run.

P1-6  attempt-suffixed titles — a PASS and a later FAIL on the same arc_id
      keep their own rows; the second attempt never clobbers the first.
§1    content, not counters — the PASS body carries the ask, the wrap BLUF
      and the critic reasons that refuted during the run, not phase counts.
P2-12 fail-open parity — the archive capture and the PASS summary write are
      as fail-open as the FAIL write already was: a dead DB never blocks a
      transition or a finalize.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera import arc_memory, memory
from chimera.arcs import graph
from tests.arc_drivers import _gr_wrap_payload, graph_to_verify, valid_opinion

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def drive_to_pass(tmp_path: Path):
    """graph -> verify -> unanimous PASS (fires on_pass / _summarize_to_memory)."""
    arc, state, task_dir = graph_to_verify(tmp_path)
    for slot in (1, 2):
        state = arc.submit(state, f"verify:critic{slot}", valid_opinion(refuted=False), kind="null")
    assert state.phase == "complete"
    return arc, state, task_dir


def _run_cli(capsys, *argv) -> dict:
    from chimera.cli import main

    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    assert exc.value.code == 0, capsys.readouterr().err
    return json.loads(capsys.readouterr().out)


def fake_record(task_id: str, ask: str = "answer the demo ask", arc: str = "graph"):
    """The two attributes _write_failed_l2 reads off a TaskRecord."""
    return SimpleNamespace(spec=SimpleNamespace(id=task_id, ask=ask, arc=arc))


def run_summaries(arc_id: str, db_path: Path) -> list[dict]:
    rows = arc_memory.arc_search(arc_kind="graph", arc_id=arc_id, db_path=db_path)
    return [r for r in rows if str(r["title"]).startswith("run-summary")]


# ---------------------------------------------------------------------------
# P1-6 — attempt-suffixed L2 titles
# ---------------------------------------------------------------------------


def test_pass_then_fail_on_one_arc_id_keeps_both_rows(tmp_path, _isolated_memory_db):
    """The repro from the audit: a second terminal write for the same task id
    used to UPDATE the first in place, so the retry erased what attempt 1
    learned. Both attempts must survive, with distinct titles."""
    from chimera.cli import _write_failed_l2

    _, state, _ = drive_to_pass(tmp_path)
    _write_failed_l2(fake_record(state.spec_id), "planner refused three times")

    rows = run_summaries(state.spec_id, _isolated_memory_db)
    assert len(rows) == 2, [r["title"] for r in rows]
    assert {r["title"] for r in rows} == {"run-summary-1", "run-summary-2"}
    bodies = " || ".join(r["body"] for r in rows)
    assert "planner refused three times" in bodies


def test_repeated_failures_keep_appending(tmp_path, _isolated_memory_db):
    from chimera.cli import _write_failed_l2

    for n in range(3):
        _write_failed_l2(fake_record("20260902-retried"), f"failure {n}")
    rows = run_summaries("20260902-retried", _isolated_memory_db)
    assert [r["title"] for r in rows] == [
        "run-summary-3",
        "run-summary-2",
        "run-summary-1",
    ] or sorted(r["title"] for r in rows) == [
        "run-summary-1",
        "run-summary-2",
        "run-summary-3",
    ]
    assert len(rows) == 3


def test_attempt_rows_do_not_leak_across_arc_ids(tmp_path, _isolated_memory_db):
    from chimera.cli import _write_failed_l2

    _write_failed_l2(fake_record("20260902-a"), "a failed")
    _write_failed_l2(fake_record("20260902-b"), "b failed")
    assert [r["title"] for r in run_summaries("20260902-a", _isolated_memory_db)] == [
        "run-summary-1"
    ]
    assert [r["title"] for r in run_summaries("20260902-b", _isolated_memory_db)] == [
        "run-summary-1"
    ]


# ---------------------------------------------------------------------------
# Proposal §1 — content, not counters
# ---------------------------------------------------------------------------


def test_pass_summary_carries_the_ask_and_the_bluf(tmp_path, _isolated_memory_db):
    """The pre-fix body was "phase=complete phases=2 nodes=3 degraded_nodes=0
    …" — telemetry a later run cannot act on. The abstract must carry the ask
    and the wrap artifact's BLUF."""
    _, state, _ = drive_to_pass(tmp_path)
    (row,) = run_summaries(state.spec_id, _isolated_memory_db)
    body = row["body"]
    assert "ASK: answer the demo ask" in body
    assert "BLUF: Consolidated answer from the winning gather." in body
    assert "SHAPE:" in body and "2 phases, 3 nodes" in body
    assert "RUN: outcome=complete degraded_nodes=0" in body
    # the counters survive as a footer, not as the whole story
    assert not body.startswith("phase=")


def test_pass_summary_carries_the_critique_that_forced_a_repair(tmp_path, _isolated_memory_db):
    """state.verify_opinions is wiped on every repair lap, so the reasons have
    to be captured when the lap fires or they are gone by the terminal write."""
    arc, state, _ = graph_to_verify(tmp_path)
    for slot in (1, 2):
        state = arc.submit(
            state,
            f"verify:critic{slot}",
            {"refuted": True, "reason": f"source {slot} is unverifiable", "severity": "fatal"},
            kind="null",
        )
    assert state.phase == "wrap"  # repair lap, panel reset
    assert state.verify_notes and len(state.verify_notes) == 2

    state = arc.submit(state, "wrap", _gr_wrap_payload(), kind="null")
    for slot in (1, 2):
        state = arc.submit(state, f"verify:critic{slot}", valid_opinion(refuted=False), kind="null")
    assert state.phase == "complete"

    (row,) = run_summaries(state.spec_id, _isolated_memory_db)
    assert "REFUTED EN ROUTE:" in row["body"]
    assert "[fatal] source 1 is unverifiable" in row["body"]
    assert "verify_repairs=1" in row["body"]


def test_pass_summary_carries_a_refutation_that_did_not_block_the_run(
    tmp_path, _isolated_memory_db
):
    """F3: a PASS can carry a non-fatal refutation (fatal-only veto) — the
    verify_notes must be recorded on the PASS branch too, not only on the
    repair branch, or run_abstract's "REFUTED EN ROUTE" block is silently
    empty for a run the panel actually refuted something in."""
    arc, state, _ = graph_to_verify(tmp_path)
    state = arc.submit(
        state, "verify:critic1", valid_opinion(refuted=True, severity="material"), kind="null"
    )
    state = arc.submit(state, "verify:critic2", valid_opinion(refuted=False), kind="null")
    assert state.phase == "complete"  # material-only refutation survives the veto

    (row,) = run_summaries(state.spec_id, _isolated_memory_db)
    assert "REFUTED EN ROUTE:" in row["body"]
    assert "[material] checked" in row["body"]


def test_verify_notes_are_bounded(tmp_path):
    """5 entries x 300 chars: a critic reason is unbounded input (P2-5)."""
    state = SimpleNamespace(verify_notes=[])
    long_reason = "x" * 900
    opinions = [
        SimpleNamespace(refuted=True, reason=long_reason, severity="fatal") for _ in range(9)
    ]
    graph.record_verify_notes(state, opinions)
    assert len(state.verify_notes) == graph.VERIFY_NOTE_LIMIT
    assert all(len(n) <= graph.VERIFY_NOTE_CHARS + len("[fatal] ") for n in state.verify_notes)


def test_verify_notes_skip_the_critics_that_passed(tmp_path):
    state = SimpleNamespace(verify_notes=[])
    graph.record_verify_notes(
        state,
        [
            SimpleNamespace(refuted=False, reason="fine", severity="cosmetic"),
            None,
            SimpleNamespace(refuted=True, reason="not fine", severity=None),
        ],
    )
    assert state.verify_notes == ["[unrated] not fine"]


def test_record_verify_notes_is_a_noop_without_the_field():
    """Duck-typed: an arc whose state has no verify_notes must still finalize."""
    state = SimpleNamespace()
    graph.record_verify_notes(state, [SimpleNamespace(refuted=True, reason="r", severity=None)])
    assert not hasattr(state, "verify_notes")


def test_first_paragraph_skips_headings_and_rules():
    body = "## BLUF\n\n---\n\nThe answer is 42.\n\n## Detail\n\nmore"
    assert graph.first_paragraph(body) == "The answer is 42."
    assert graph.first_paragraph("## only a heading") == ""
    assert graph.first_paragraph("") == ""
    assert len(graph.first_paragraph("word " * 500)) == graph.ABSTRACT_BLUF_CHARS


def test_failed_summary_leads_with_the_ask(tmp_path, _isolated_memory_db):
    from chimera.cli import _write_failed_l2

    _write_failed_l2(fake_record("20260902-f", ask="ship the thing"), "planner refused")
    (row,) = run_summaries("20260902-f", _isolated_memory_db)
    assert row["body"].startswith("ASK: ship the thing")
    assert "arc FAILED: planner refused" in row["body"]


# ---------------------------------------------------------------------------
# P2-12 — fail-open parity: archive and the PASS summary are as fail-open as
# the FAIL row already was. An unwritable DB path degrades the row, never the
# transition or the finalize.
# ---------------------------------------------------------------------------


def _unwritable_db(tmp_path: Path) -> Path:
    """A DB path whose PARENT is a regular file, so mkdir/connect both fail."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    return blocker / "memory.db"


def test_pass_finalize_survives_a_dead_memory_db(tmp_path, monkeypatch):
    dead = _unwritable_db(tmp_path)
    monkeypatch.setattr("chimera.memory.DEFAULT_DB_PATH", dead)
    monkeypatch.setattr("chimera.arc_memory.DEFAULT_DB_PATH", dead)

    arc, state, task_dir = graph_to_verify(tmp_path / "run")
    for slot in (1, 2):
        state = arc.submit(state, f"verify:critic{slot}", valid_opinion(refuted=False), kind="null")

    assert state.phase == "complete"
    assert (task_dir / "verification.json").exists()
    assert any("MEMORY_WRITE_DEGRADED" in line for line in state.log)
    # the degrade is durable: it survived the save, so the digest can see it
    assert any("MEMORY_WRITE_DEGRADED" in line for line in arc.load().log)


def test_failed_row_survives_a_dead_memory_db(tmp_path, monkeypatch):
    """The FAIL write is the path that must never wedge: `failed` is already
    committed by the time it runs, so a dead DB can only cost the row."""
    from chimera.cli import _write_failed_l2

    dead = _unwritable_db(tmp_path)
    monkeypatch.setattr("chimera.memory.DEFAULT_DB_PATH", dead)
    monkeypatch.setattr("chimera.arc_memory.DEFAULT_DB_PATH", dead)

    _write_failed_l2(fake_record("20260903-dead"), "planner refused three times")

    assert not dead.exists()


def test_archive_survives_a_dead_memory_db(repo, monkeypatch, capsys, tmp_path):
    from chimera.queue import Queue
    from tests.test_queue import make_spec, passing_verification

    monkeypatch.chdir(repo)
    queue = Queue(root=repo)
    tid = queue.create(make_spec("archive-degraded"), "ready", by="t").spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "awaiting-signoff", by="w")
    queue.record_verification(tid, passing_verification())
    queue.record_approval(tid, "operator")
    queue.transition(tid, "done", by="w")

    monkeypatch.setattr("chimera.memory.DEFAULT_DB_PATH", _unwritable_db(tmp_path))
    out = _run_cli(capsys, "archive", tid)

    assert out["state"] == "archived"
    assert out["memory_captured"] is False
    assert Queue(root=repo).load(tid).state == "archived"


def test_archive_reports_a_successful_capture(repo, monkeypatch, capsys, _isolated_memory_db):
    from chimera.queue import Queue
    from tests.test_queue import make_spec, passing_verification

    monkeypatch.chdir(repo)
    queue = Queue(root=repo)
    tid = queue.create(make_spec("archive-ok"), "ready", by="t").spec.id
    queue.claim(tid, "w")
    queue.transition(tid, "awaiting-signoff", by="w")
    queue.record_verification(tid, passing_verification())
    queue.record_approval(tid, "operator")
    queue.transition(tid, "done", by="w")

    out = _run_cli(capsys, "archive", tid)
    assert out["memory_captured"] is True
    with memory._connect(_isolated_memory_db) as conn:
        titles = [r[0] for r in conn.execute("SELECT title FROM memories")]
    assert f"task_{tid}" in titles

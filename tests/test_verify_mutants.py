"""Seeded-mutant panel credential (C15) — the pure half.

`verify/mutants.py` seeds exactly ONE deterministic defect into a copy of the
rendered artifact, with no model call: the maker never authors its own
credential. This file pins the operators, their priority, the determinism of
`apply`, the derived cadence and the aggregate catch rate. The arc wiring (who
dispatches it, what it may never touch) lives in tests/test_graph_arc.py.
"""

from __future__ import annotations

import json

import pytest

from chimera import levers
from chimera.verify import mutants

# ---------------------------------------------------------------------------
# The lever: default-restrictive, and it cannot be switched OFF through the
# environment (0 is out of range, so it reads as unset like any typo).
# ---------------------------------------------------------------------------


def test_mutant_cadence_default_is_five():
    assert levers.mutant_cadence() == 5
    assert levers.VERIFY_MUTANT_CADENCE_DEFAULT == 5


@pytest.mark.parametrize("raw", ["0", "five", "", "-1", "51", "5.0", " 5"])
def test_mutant_cadence_lever_rejects_zero_and_a_typo(monkeypatch, raw):
    """`0` would silently disable the control, and a typo is not a decision —
    both read as unset, which is the cadence, never off."""
    monkeypatch.setenv("CHIMERA_VERIFY_MUTANT_CADENCE", raw)
    assert levers.mutant_cadence() == levers.VERIFY_MUTANT_CADENCE_DEFAULT


@pytest.mark.parametrize("raw,expected", [("1", 1), ("7", 7), ("50", 50)])
def test_mutant_cadence_lever_accepts_its_documented_range(monkeypatch, raw, expected):
    monkeypatch.setenv("CHIMERA_VERIFY_MUTANT_CADENCE", raw)
    assert levers.mutant_cadence() == expected


# ---------------------------------------------------------------------------
# Fixtures — a rendered artifact in the shape _render_artifact produces
# ---------------------------------------------------------------------------

FRONTMATTER = (
    "---\narc: graph\nslug: demo\ncreated: 2026-09-16T00:00:00Z\n"
    "phases: 2\nnodes: 3\nstatus: complete\n---\n\n"
)


def rendered(body: str) -> str:
    return FRONTMATTER + body + "\n"


def test_m1_breaks_a_real_cited_path_only(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "levers.py").write_text("x = 1\n", encoding="utf-8")
    art = rendered("## BLUF\n\nThe lever lives at `src/levers.py:81` and holds.\n")

    defect = mutants.seed(art, tmp_path)

    assert defect is not None
    assert defect.operator == "path-anchor"
    assert defect.defect_class == "citation-path"
    assert defect.angle == 1  # GREP-ANCHOR
    assert defect.original == "`src/levers.py:81`"
    assert defect.mutated == "`src/levers-chimera-mutant.py:81`"
    assert art[defect.offset : defect.offset + len(defect.original)] == defect.original


def test_m1_skips_a_path_that_does_not_exist(tmp_path):
    """Breaking a path that was already unfindable measures nothing — the
    panel would refute the artifact for a defect chimera did not seed."""
    art = rendered("## BLUF\n\nSee `src/ghost.py:12` for the rule.\n")

    defect = mutants.seed(art, tmp_path)

    assert defect is None or defect.operator != "path-anchor"


def test_m2_deletes_the_first_open_finding(tmp_path):
    art = rendered(
        "## BLUF\n\nAll good.\n\n## Open findings\n\n"
        "- the retry loop never re-reads the lockfile\n"
        "- a second finding that must survive\n"
    )

    defect = mutants.seed(art, tmp_path)

    assert defect is not None
    assert defect.operator == "open-finding"
    assert defect.defect_class == "deleted-disclosure"
    assert defect.angle == 2  # the executing CONFIRM angle owns the disclosure rule
    assert defect.mutated == ""
    mutated = mutants.apply(defect, art)
    assert "the retry loop never re-reads the lockfile" not in mutated
    assert "a second finding that must survive" in mutated


def test_m3_shifts_the_first_quantity(tmp_path):
    art = rendered("## BLUF\n\nThe panel dispatches 3 calls per run.\n")

    defect = mutants.seed(art, tmp_path)

    assert defect is not None
    assert defect.operator == "quantity"
    assert defect.defect_class == "load-bearing-number"
    assert defect.angle == 2
    assert defect.original == "3"
    assert defect.mutated == "22"  # 3 * 7 + 1 — never the original, never plausible
    assert mutants.apply(defect, art).count("22 calls per run") == 1


def test_operator_priority_is_m1_m2_m3(tmp_path):
    """One defect per run, so "caught" is unambiguous — and the operator whose
    catch condition the panel is literally instructed to perform wins."""
    (tmp_path / "real.md").write_text("x\n", encoding="utf-8")
    body = "## BLUF\n\nThe 4 lanes cite `real.md:2`.\n\n## Open findings\n\n- one disclosed gap\n"
    art = rendered(body)

    m1 = mutants.seed(art, tmp_path)
    assert m1 is not None and m1.operator == "path-anchor"

    # drop the citation: M2 takes over
    art_no_path = rendered(body.replace("`real.md:2`", "the same file"))
    m2 = mutants.seed(art_no_path, tmp_path)
    assert m2 is not None and m2.operator == "open-finding"

    # drop the open-findings block too: M3 takes over
    art_no_findings = rendered("## BLUF\n\nThe 4 lanes cite the same file.\n")
    m3 = mutants.seed(art_no_findings, tmp_path)
    assert m3 is not None and m3.operator == "quantity"


def test_seed_returns_none_when_no_operator_applies(tmp_path):
    art = rendered("## BLUF\n\nNo citations, no findings, no numbers here.\n")
    assert mutants.seed(art, tmp_path) is None


def test_apply_is_deterministic_and_reproducible(tmp_path):
    (tmp_path / "real.md").write_text("x\n", encoding="utf-8")
    art = rendered("## BLUF\n\nSee `real.md:2`.\n")

    first = mutants.seed(art, tmp_path)
    second = mutants.seed(art, tmp_path)
    assert first is not None and second is not None
    assert first.model_dump(exclude={"offset"}) == second.model_dump(exclude={"offset"})
    assert first.offset == second.offset
    # re-applied from the RECORD, never re-searched: same bytes every time
    assert mutants.apply(first, art) == mutants.apply(second, art)
    assert mutants.apply(first, art) != art
    # exactly one defect: the rest of the artifact is untouched
    out = mutants.apply(first, art)
    assert len(out) == len(art) + len(first.mutated) - len(first.original)


def test_apply_refuses_a_defect_that_no_longer_matches(tmp_path):
    (tmp_path / "real.md").write_text("x\n", encoding="utf-8")
    art = rendered("## BLUF\n\nSee `real.md:2`.\n")
    defect = mutants.seed(art, tmp_path)
    assert defect is not None
    with pytest.raises(mutants.MutantError):
        mutants.apply(defect, rendered("## BLUF\n\nA completely different body.\n"))


def test_frontmatter_is_never_mutated(tmp_path):
    """The frontmatter is schema-validated (GraphFrontmatter); mutating it
    would produce a defect the panel catches as a parse error, not as the
    class chimera meant to measure."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "levers.py").write_text("x\n", encoding="utf-8")
    art = rendered("## BLUF\n\nThe rule lives at `src/levers.py:1`.\n")

    defect = mutants.seed(art, tmp_path)

    assert defect is not None
    assert defect.offset >= len(FRONTMATTER)
    assert mutants.apply(defect, art).startswith(FRONTMATTER)


def test_frontmatter_numbers_are_not_a_quantity_target(tmp_path):
    """`phases: 2` is frontmatter, not prose — M3 must not reach it even when
    the body carries no number of its own."""
    art = rendered("## BLUF\n\nNo numbers in this body at all.\n")
    assert mutants.seed(art, tmp_path) is None


def test_defect_past_the_payload_cap_is_not_measured(tmp_path):
    """A defect the panel's truncation window never shows it would score a
    guaranteed MISS — that is not a measurement, so it is refused here."""
    (tmp_path / "real.md").write_text("x\n", encoding="utf-8")
    art = rendered("## BLUF\n\nfiller.\n\n" + ("padding line\n" * 200) + "\nSee `real.md:2`.\n")
    defect = mutants.seed(art, tmp_path)
    assert defect is not None

    assert mutants.within_payload(defect, len(art)) is True
    assert mutants.within_payload(defect, defect.offset) is False
    assert mutants.within_payload(None, len(art)) is False


# ---------------------------------------------------------------------------
# Cadence + catch rate — derived from committed records, never stored
# ---------------------------------------------------------------------------


def _verdict_record(tasks_root, task_id: str, credential: str | None = None) -> None:
    body: dict[str, object] = {
        "mode": "lite",
        "passed": True,
        "maker_model": "opus",
        "critic_model": "sonnet",
        "opinions": [],
        "valid_critic_count": 2,
        "unrefuted_count": 2,
    }
    if credential is not None:
        body["panel_credential"] = credential
    path = tasks_root / task_id / "verification.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_cadence_is_derived_from_the_committed_verdict_population(tmp_path):
    """No counter file and no in-memory state: the population is the count of
    committed `tasks/*/verification.json`, so the cadence survives a session
    restart because the queue's own commits carry it."""
    assert mutants.verdict_population(tmp_path) == 0
    assert mutants.due(tmp_path, cadence=1) is True  # every run
    assert mutants.due(tmp_path, cadence=5) is False  # run 1 of 5

    for n in range(1, 5):
        _verdict_record(tmp_path, f"2026091{n}-task")
        assert mutants.verdict_population(tmp_path) == n
    assert mutants.due(tmp_path, cadence=5) is True  # the 5th verdict is due
    _verdict_record(tmp_path, "20260915-task")
    assert mutants.due(tmp_path, cadence=5) is False  # run 6 of the next cycle


def test_cadence_reads_are_fail_open(tmp_path):
    """A missing tasks root is not a reason to block a run."""
    assert mutants.verdict_population(tmp_path / "absent") == 0
    assert mutants.due(tmp_path / "absent", cadence=1) is True


def test_catch_rate_ignores_unmeasured_and_corrupt_records(tmp_path):
    _verdict_record(tmp_path, "20260901-caught", "mutant-caught")
    _verdict_record(tmp_path, "20260902-missed", "mutant-missed")
    _verdict_record(tmp_path, "20260903-notmeasured", "mutant-not-measured")
    _verdict_record(tmp_path, "20260904-uncredentialed")
    (tmp_path / "20260905-corrupt").mkdir()
    (tmp_path / "20260905-corrupt" / "verification.json").write_text("{not json", encoding="utf-8")

    assert mutants.catch_rate(tmp_path) == (1, 2)
    assert mutants.catch_rate(tmp_path / "absent") == (0, 0)

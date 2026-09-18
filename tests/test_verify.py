"""Fatal-only veto + open findings (2026-09-16, reversing the 2026-09-11
proportional-majority rule). Evidence, frozen in tests/data/: four panels, 12
committed opinions, 0 graded fatal, 3 runs failed on material-only refutations
whose authors wrote the conclusion survives (summed below)."""

import pytest

from chimera.models import CriticOpinion
from chimera.verify import lite


def op(refuted: bool, severity: str | None = None) -> CriticOpinion:
    return CriticOpinion(refuted=refuted, reason="x", severity=severity)


# (valid opinions as (refuted, severity) or None for a degraded critic,
#  expected survives)
CASES = [
    # 2 valid, no refutations
    ([(False, None), (False, None)], True),
    # 2 valid, a material/cosmetic refutation does NOT fail the run
    ([(False, None), (True, "material")], True),
    ([(True, "cosmetic"), (True, "material")], True),
    # 2 valid, a single fatal refutation vetoes alone
    ([(False, None), (True, "fatal")], False),
    ([(True, "fatal"), (True, "fatal")], False),
    # 3 valid: one fatal among otherwise-clean opinions still vetoes
    ([(False, None), (False, None), (True, "fatal")], False),
    # 3 valid, all refuted but none fatal: still survives (count-agnostic)
    ([(True, "material"), (True, "material"), (True, "cosmetic")], True),
    # 0-1 valid: too thin, regardless of content
    ([(False, None), None, None], False),
    ([(True, "fatal"), None, None], False),
    ([None, None, None], False),
]


@pytest.mark.parametrize("specs,expected", CASES)
def test_survives(specs, expected):
    opinions = [op(*s) if s is not None else None for s in specs]
    survives, valid, unrefuted = lite.survives(opinions)
    assert survives is expected
    assert valid == sum(1 for s in specs if s is not None)
    assert unrefuted == sum(1 for s in specs if s is not None and s[0] is False)


def test_critic_calls_use_critic_model():
    calls = lite.critic_calls("verify", "payload")
    assert len(calls) == 2
    assert calls[0].model == lite.resolve_models().critic
    assert calls[1].model == lite.resolve_models().research
    assert all(c.schema_name == "CriticOpinion" for c in calls)
    assert [c.label for c in calls] == ["verify:critic1", "verify:critic2"]


def test_verdict_shape():
    v = lite.verdict("lite", [op(False), op(True, "material")])
    assert v.passed is True
    assert v.valid_critic_count == 2
    assert v.unrefuted_count == 1
    assert v.maker_model != v.critic_model
    assert v.open_findings == ["[material] x"]


# --- the rule, after the 2026-09-16 reversal --------------------------------
# Only `fatal` blocks. `material`/`cosmetic` refutations are recorded as open
# findings and do not change the verdict. This reverses 2026-09-11's
# proportional-majority rule: across 4 real runs, every refutation that ever
# killed a run was material, and its own author's reason said the conclusion
# survives.


@pytest.mark.parametrize("severity", ["cosmetic", "material"])
def test_a_non_fatal_refutation_never_fails(severity):
    """Non-fatal refutations no longer participate in survival at all —
    there is no majority count left to tie or lose."""
    survived, valid, unrefuted = lite.survives([op(False), op(True, severity)])
    assert survived is True
    assert (valid, unrefuted) == (2, 1)


def test_a_lone_fatal_vetoes_at_every_checker_count():
    for n in (2, 3, 4, 5):
        opinions = [op(False) for _ in range(n - 1)] + [op(True, "fatal")]
        survived, valid, _ = lite.survives(opinions)
        assert survived is False, f"the fatal veto must hold at valid={n}"
        assert valid == n


def test_the_old_even_count_tie_hole_no_longer_exists():
    """Under the old proportional-majority rule, valid=4/refuted=2 was a tie
    that failed the run. That majority test is gone: with no fatal among
    them, the run now survives regardless of how many non-fatal refutations
    are filed — the count that used to tie no longer decides anything."""
    mixed = [op(True, "material"), op(True, "cosmetic"), op(False), op(False)]
    assert lite.survives(mixed) == (True, 4, 2)


def test_blocks_is_fatal_only():
    assert lite.blocks(op(True, "cosmetic")) is False
    assert lite.blocks(op(True, "material")) is False
    assert lite.blocks(op(True, "fatal")) is True
    assert lite.blocks(op(False, "fatal")) is False  # not refuted -> never blocks


def test_unrefuted_count_is_not_reported_via_blocks():
    """A refuted-but-non-fatal opinion is not blocking, but it IS refuted —
    unrefuted_count must not misreport it as unrefuted."""
    survived, valid, unrefuted = lite.survives([op(False), op(True, "material")])
    assert survived is True
    assert unrefuted == 1  # the material refutation does NOT count as unrefuted


def test_advisory_count_is_informational_only():
    """`cosmetic` refutations are still recorded for the digest; they do not
    buy or cost a different verdict."""
    v = lite.verdict("lite", [op(False), op(True, "cosmetic")])
    assert v.passed is True
    assert v.advisory_count == 1
    assert v.unrefuted_count == 1


def test_open_findings_record_every_non_fatal_refutation():
    v = lite.verdict("lite", [op(True, "material"), op(True, "cosmetic")])
    assert v.passed is True
    assert v.open_findings == ["[material] x", "[cosmetic] x"]


def test_open_findings_empty_when_no_refutations():
    v = lite.verdict("lite", [op(False), op(False)])
    assert v.open_findings == []


def test_open_findings_excludes_the_fatal_veto():
    """A fatal refutation fails the run; it is the veto, not an open
    finding — open_findings is for defects the run survived."""
    v = lite.verdict("lite", [op(False), op(True, "fatal")])
    assert v.passed is False
    assert v.open_findings == []


def test_gate_is_not_a_rubber_stamp_a_fatal_refutation_still_fails():
    """The gate is not a rubber stamp: a genuinely fatal defect still fails
    the run outright, at any checker count."""
    survived, valid, _ = lite.survives([op(False), op(False), op(True, "fatal")])
    assert survived is False
    assert valid == 3


def test_gate_is_not_a_rubber_stamp_a_thin_panel_still_fails():
    """Fewer than two valid opinions is insufficient signal regardless of
    content — the gate refuses to decide on a single reader."""
    survived, valid, _ = lite.survives([op(False)])
    assert survived is False
    assert valid == 1


# --- F4: severity is load-bearing now, so the schema gates it --------------
# refuted=true with severity omitted used to silently escape the fatal veto
# (blocks() reads opinion.severity == "fatal", and None never equals that).
# The model now refuses to construct such an opinion at all — an ungraded
# refutation is rejected at submit, not accepted and quietly treated as safe.


def test_refuted_without_severity_is_rejected():
    with pytest.raises(ValueError, match="severity"):
        CriticOpinion(refuted=True, reason="x")


def test_refuted_without_severity_is_rejected_even_with_explicit_none():
    with pytest.raises(ValueError, match="severity"):
        CriticOpinion(refuted=True, reason="x", severity=None)


@pytest.mark.parametrize("severity", ["fatal", "material", "cosmetic"])
def test_refuted_with_a_graded_severity_is_accepted(severity):
    op(True, severity)  # constructs without raising


def test_unrefuted_without_severity_is_accepted():
    """There is nothing to grade when the opinion did not refute."""
    op(False)  # constructs without raising


# --- regression: the three committed verification.json records -------------
# Deserialized straight off disk rather than retyped, so the NEW rule's
# outcome on real panel opinions is pinned, not asserted from a hand-built
# fixture. Skips rather than fails if the record is absent: tasks/ is engine
# state, not a fixture, so CI need not carry it.


def _load_record(task_id: str) -> dict:
    import json
    from pathlib import Path

    # Frozen snapshots (tests/data/, taken at commit 0e9fc72), NOT the live
    # tasks/ records: a retry rewrites tasks/<id>/verification.json, so the
    # live file is mutable state while these tests pin the HISTORICAL panels
    # that the 2026-09-11 rule failed and the fatal-only rule passes.
    record = Path(__file__).resolve().parent / "data" / f"verification-{task_id}.json"
    return json.loads(record.read_text(encoding="utf-8"))


def test_tick_stamp_durability_record_passes_clean():
    data = _load_record("20260904-tick-stamp-durability")
    opinions = [CriticOpinion.model_validate(o) for o in data["opinions"]]
    v = lite.verdict("lite", opinions)
    assert v.passed is True
    assert v.open_findings == []


def test_pr_review_as_g2_record_now_survives_on_the_new_rule():
    """This record FAILED under the 2026-09-11 proportional-majority rule
    (1/3 unrefuted). Both refutations on it are graded `material`, never
    `fatal` — under the fatal-only veto the run survives, with both
    refutations preserved as open findings rather than silently dropped."""
    data = _load_record("20260904-pr-review-as-g2")
    opinions = [CriticOpinion.model_validate(o) for o in data["opinions"]]
    v = lite.verdict("lite", opinions)
    assert v.passed is True
    assert len(v.open_findings) == 2
    assert all(f.startswith("[material]") for f in v.open_findings)


def test_clean_sheet_record_survives_with_two_open_findings():
    data = _load_record("20260916-clean-sheet")
    opinions = [CriticOpinion.model_validate(o) for o in data["opinions"]]
    v = lite.verdict("lite", opinions)
    assert v.passed is True
    assert len(v.open_findings) == 2
    assert all(f.startswith("[material]") for f in v.open_findings)


def test_optimize_dag_record_fails_on_a_grep_anchor_miss():
    """The fourth committed record: two `material` refutations (a citation
    anchor that failed to grep) and no `fatal` — survives on the fatal-only
    veto, same as the other refuted-but-not-fatal records."""
    data = _load_record("20260916-optimize-dag")
    opinions = [CriticOpinion.model_validate(o) for o in data["opinions"]]
    v = lite.verdict("lite", opinions)
    assert v.passed is True
    assert len(v.open_findings) == 2


# --- deserializing pre-existing records (no open_findings on disk) ---------

# All four task ids with a committed verification.json, gathered once so the
# record-compat check (F4: does CriticOpinion's new "refuted requires
# severity" validator reject anything actually on disk?) and the
# pre-open_findings deserialization check both walk the same set.
ALL_COMMITTED_RECORDS = (
    "20260904-tick-stamp-durability",
    "20260904-pr-review-as-g2",
    "20260916-clean-sheet",
    "20260916-optimize-dag",
)


def test_pre_existing_records_deserialize_without_open_findings():
    """All four committed records predate `open_findings`; the field must
    default to [] rather than fail validation."""
    from chimera.models import VerifyResult

    for task_id in ALL_COMMITTED_RECORDS:
        data = _load_record(task_id)
        assert "open_findings" not in data
        result = VerifyResult.model_validate(data)
        assert result.open_findings == []


def test_pre_existing_records_carry_panel_uncredentialed():
    """The seeded-mutant credential (2026-09-16) is additive: every record
    committed before it existed must still load under extra="forbid", and must
    read as UNCREDENTIALED — never as a silent pass of a control that never
    ran."""
    from chimera.models import VerifyResult

    for task_id in ALL_COMMITTED_RECORDS:
        data = _load_record(task_id)
        assert "panel_credential" not in data
        assert "mutant" not in data
        result = VerifyResult.model_validate(data)
        assert result.panel_credential == "panel-uncredentialed"
        assert result.mutant is None


def test_mutant_run_defaults_to_pending():
    """A scheduled control starts pending with no opinion and no grade: the
    maker never authors its own credential, so nothing is assumed until the
    panel's answer lands."""
    from chimera.models import MutantRun

    run = MutantRun()
    assert run.status == "pending"
    assert run.defect is None
    assert run.opinion is None
    assert run.graded_fatal is False
    assert run.label == "verify:mutant1"


def test_every_committed_record_satisfies_the_new_severity_validator():
    """F4 record-compat check: every refuted opinion on disk already carries
    a graded severity, so CriticOpinion's new validator (refuted=true
    requires severity) rejects nothing that was actually committed."""
    from chimera.models import VerifyResult

    for task_id in ALL_COMMITTED_RECORDS:
        data = _load_record(task_id)
        result = VerifyResult.model_validate(data)  # must not raise
        for opinion in result.opinions:
            if opinion.refuted:
                assert opinion.severity is not None, (task_id, opinion.reason)


def test_the_committed_panel_record_matches_the_documented_tally():
    """The sentence every live surface cites — four panels, 12 committed
    opinions, 0 graded fatal, 3 runs failed on material-only refutations — is
    summed here from the frozen fixtures, so the docs cannot drift from the
    record they cite (Behavioral Rule 6)."""
    from chimera.models import VerifyResult

    results = [VerifyResult.model_validate(_load_record(t)) for t in ALL_COMMITTED_RECORDS]
    assert len(results) == 4
    assert sum(r.valid_critic_count for r in results) == 12
    assert sum(1 for r in results if not r.passed) == 3
    assert all(o.severity != "fatal" for r in results for o in r.opinions)
    # every recorded kill was a false veto: refuted somewhere, fatal nowhere
    for r in results:
        if not r.passed:
            assert any(o.refuted for o in r.opinions)


def test_no_live_surface_asserts_the_retired_panel_tally():
    """The retired wording must not resurface on any live surface: "4 runs
    killed" overclaimed (one of the four lite-verify kills in git left no
    record, so its severities are unknown). A repo-wide grep, not a hand
    list — the hand-kept list of sites missed a sixth one. tasks/ and
    decisions/references/ are frozen records and exempt."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    # assembled at runtime so this file does not match itself
    needles = ["12 panel" + " opinions", "4 runs" + " killed"]
    surfaces = [repo / "CLAUDE.md", repo / "CHANGELOG.md"]
    surfaces += sorted((repo / "decisions").glob("ADR-*.md"))
    surfaces += sorted((repo / "src").rglob("*.py")) + sorted((repo / "tests").rglob("*.py"))
    hits = [
        f"{path.relative_to(repo)}: {needle!r}"
        for path in surfaces
        for needle in needles
        if needle in path.read_text(encoding="utf-8")
    ]
    assert not hits, hits


def test_verify_panel_constant_matches_the_angle_roles():
    """graph._VERIFY_PANEL is the panel's width in the call accounting and
    carried a "must equal len(ANGLE_ROLES)" comment that nothing enforced;
    a panel widened in lite.py alone would silently mis-estimate every run."""
    from chimera.graph import _VERIFY_PANEL

    assert _VERIFY_PANEL == len(lite.ANGLE_ROLES)


# --- the panel executes ----------------------------------------------------
# Until 2026-09-11 all angles dispatched on `critic`, which holds no shell, so
# the gate that decided `done` could not run anything. Angle 2 now rides
# `executor`. The panel dropped from 3 angles to 2 on 2026-09-16: "general
# REFUTE" (the only non-executing, non-verifying angle) was dropped; GREP-
# ANCHOR (verifies citations) and CONFIRM (executes) remain.


def test_angle_two_dispatches_on_executor():
    calls = lite.critic_calls("verify", "payload")
    assert [c.subagent_type for c in calls] == ["critic", "executor"]
    assert lite.ANGLE_ROLES == ("critic", "executor")


def test_the_executing_angle_still_differs_from_the_maker():
    """maker != checker is about MODELS. The executing angle rides the
    research tier, which must not collide with the maker tier."""
    from chimera.agents import resolve_models

    models = resolve_models()
    calls = lite.critic_calls("verify", "payload")
    assert calls[1].model == models.research
    assert calls[1].model != models.maker


def test_the_executing_angle_demands_exit_codes_not_opinions():
    body = lite.critic_calls("verify", "payload")[1].prompt
    assert "EXPECTED exit code" in body
    assert "do not interpret, do not fix" in body
    assert "CONSTRUCT checks from its claims" in body


def test_critic_prompt_header_says_2():
    calls = lite.critic_calls("verify", "payload")
    assert calls[0].prompt.startswith("Critic 1 of 2 —")
    assert calls[1].prompt.startswith("Critic 2 of 2 —")


def test_subagent_type_none_disables_role_routing():
    calls = lite.critic_calls("verify", "payload", subagent_type=None)
    assert all(c.subagent_type is None for c in calls)


# --- the panel refuses to build on the maker's model, at EVERY tier ---------
# verdict() calls assert_panel_models_differ (the wide guard), not the
# narrower critic-only check it replaced — that narrower check missed the
# executing angle's research-tier collision entirely.


def test_panel_refuses_when_the_research_tier_collides_with_the_maker(monkeypatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "sonnet")
    with pytest.raises(lite.MakerCheckerViolation) as exc:
        lite.critic_calls("verify", "payload")
    assert "research" in str(exc.value)


def test_panel_refuses_when_the_critic_tier_collides_with_the_maker(monkeypatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "sonnet")
    with pytest.raises(lite.MakerCheckerViolation) as exc:
        lite.critic_calls("verify", "payload")
    assert "critic" in str(exc.value)


def test_panel_builds_when_every_tier_differs(monkeypatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "haiku")
    calls = lite.critic_calls("verify", "payload")
    assert [c.model for c in calls] == ["sonnet", "haiku"]


# --- the seeded-mutant control (C15) ---------------------------------------
# One extra call, dispatched on the angle the seeded defect targets. It is a
# CONTROL: the agent must not be able to tell it is grading one, it never
# enters the verdict, and it never gates.


def _mutant_run(status: str, **extra):
    from chimera.models import MutantRun

    return MutantRun(status=status, **extra)


@pytest.mark.parametrize("angle", [1, 2])
def test_the_mutant_prompt_is_indistinguishable_from_the_real_angle(angle):
    """Byte-identical to the angle's own prompt, for the same payload: a
    critic that could SEE it was grading a control would grade the control,
    not the panel. Only the label differs."""
    real = lite.critic_calls("verify", "payload")[angle - 1]
    mutant = lite.mutant_call("verify", "payload", angle)

    assert mutant.prompt == real.prompt
    assert "mutant" not in mutant.prompt.lower()
    assert (mutant.model, mutant.subagent_type, mutant.schema_name) == (
        real.model,
        real.subagent_type,
        real.schema_name,
    )
    assert mutant.label == "verify:mutant1"


def test_mutant_call_refuses_on_a_maker_checker_collision(monkeypatch):
    """The control walks the same wide guard as the panel it measures — a
    checker on the maker's model is not an independent check, credential or
    not."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "sonnet")
    with pytest.raises(lite.MakerCheckerViolation):
        lite.mutant_call("verify", "payload", 2)


def test_verdict_without_a_mutant_is_panel_uncredentialed():
    v = lite.verdict("lite", [op(False), op(False)])
    assert v.panel_credential == "panel-uncredentialed"
    assert v.mutant is None


def test_verdict_with_a_caught_mutant_is_credentialed():
    run = _mutant_run("caught", opinion=op(True, "fatal"), graded_fatal=True)
    v = lite.verdict("lite", [op(False), op(False)], mutant=run)
    assert v.panel_credential == "mutant-caught"
    assert v.mutant is not None and v.mutant.status == "caught"
    assert v.passed is True


@pytest.mark.parametrize(
    "status,expected",
    [
        ("caught", "mutant-caught"),
        ("missed", "mutant-missed"),
        ("not-measured", "mutant-not-measured"),
        ("pending", "mutant-not-measured"),  # never landed => never measured
    ],
)
def test_every_mutant_status_maps_to_one_credential(status, expected):
    assert lite.credential(_mutant_run(status)) == expected
    assert (
        lite.verdict("lite", [op(False), op(False)], mutant=_mutant_run(status)).panel_credential
        == expected
    )


def test_a_mutant_never_enters_opinions_or_open_findings():
    """The control's opinion refutes a defect chimera seeded — counting it as
    a panel finding would put a manufactured refutation on the record."""
    run = _mutant_run("caught", opinion=op(True, "fatal"), graded_fatal=True)
    v = lite.verdict("lite", [op(False), op(True, "material")], mutant=run)
    assert len(v.opinions) == 2
    assert v.open_findings == ["[material] x"]
    assert v.valid_critic_count == 2
    assert v.advisory_count == 0


def test_a_missed_mutant_never_changes_the_verdict():
    """The credential is a fact on the record, not a veto: a panel that missed
    its control still passes if the two real critics did not refute fatally."""
    clean = lite.verdict("lite", [op(False), op(False)])
    missed = lite.verdict("lite", [op(False), op(False)], mutant=_mutant_run("missed"))
    assert missed.passed is clean.passed is True
    assert missed.panel_credential == "mutant-missed"


def test_verdict_refuses_on_maker_checker_collision(monkeypatch):
    """verdict() must itself call the wide guard (not the narrower, deleted
    assert_maker_neq_checker) — a research-tier collision must be caught at
    verdict time, not just at critic_calls time."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "sonnet")
    with pytest.raises(lite.MakerCheckerViolation):
        lite.verdict("lite", [op(False), op(False)])

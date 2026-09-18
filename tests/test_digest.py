"""digest._arc_flags's two verdict-render branches (F2): the passing phrase
that must never read as a bare certification, and the open_findings bullets
that surface material/cosmetic refutations the fatal-only veto let survive.
"""

from __future__ import annotations

import re

from chimera.digest import _arc_flags
from chimera.models import TaskRecord, TaskSpec, VerifyResult
from chimera.queue import Queue


def _make_task(queue: Queue, task_id: str) -> TaskRecord:
    spec = TaskSpec(id=task_id, slug=task_id.split("-", 1)[1], ask="a", arc="graph")
    return queue.create(spec, "ready", "test")


def _write_verdict(queue: Queue, task_id: str, verdict: VerifyResult) -> None:
    path = queue.verification_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(verdict.model_dump_json(indent=2), encoding="utf-8")


def _verdict(
    *, passed: bool, open_findings: list[str], mutant_status: str | None = None
) -> VerifyResult:
    from chimera.models import MutantRun, SeededDefect
    from chimera.verify import lite

    mutant = None
    if mutant_status is not None:
        mutant = MutantRun(
            status=mutant_status,  # type: ignore[arg-type]
            defect=SeededDefect(
                operator="quantity",
                defect_class="load-bearing-number",
                offset=42,
                original="3",
                mutated="22",
                expected_catch="re-measure the number",
                angle=2,
            ),
        )
    return VerifyResult(
        mode="lite",
        passed=passed,
        maker_model="opus",
        critic_model="sonnet",
        opinions=[],
        valid_critic_count=2,
        unrefuted_count=1 if open_findings else 2,
        open_findings=open_findings,
        panel_credential=lite.credential(mutant),
        mutant=mutant,
    )


def test_passing_verdict_says_refutation_attempted_and_survived_never_bare_verified(queue):
    task_id = "20260916-digestpass"
    record = _make_task(queue, task_id)
    _write_verdict(queue, task_id, _verdict(passed=True, open_findings=[]))

    joined = "\n".join(_arc_flags(queue, record))

    assert "refutation-attempted and survived" in joined
    # a passing panel REFUTE-attempted and survived; it did not certify the
    # artifact correct, so the bare word "verified" must never appear.
    assert not re.search(r"\bverified\b", joined)


def test_open_findings_render_as_bullets_when_passing(queue):
    task_id = "20260916-digestfindpass"
    record = _make_task(queue, task_id)
    findings = [
        "[material] the retry loop never re-reads the lockfile",
        "[cosmetic] stale count in the docstring",
    ]
    _write_verdict(queue, task_id, _verdict(passed=True, open_findings=findings))

    joined = "\n".join(_arc_flags(queue, record))

    assert "Open findings:" in joined
    for finding in findings:
        assert f"- {finding}" in joined


def test_open_findings_render_as_bullets_when_failing(queue):
    task_id = "20260916-digestfindfail"
    record = _make_task(queue, task_id)
    findings = ["[material] a real defect that must be fixed"]
    _write_verdict(queue, task_id, _verdict(passed=False, open_findings=findings))

    joined = "\n".join(_arc_flags(queue, record))

    assert "VERIFICATION FAILED" in joined
    assert "Open findings:" in joined
    assert f"- {findings[0]}" in joined


def test_empty_open_findings_adds_no_section(queue):
    task_id = "20260916-digestnofind"
    record = _make_task(queue, task_id)
    _write_verdict(queue, task_id, _verdict(passed=True, open_findings=[]))

    joined = "\n".join(_arc_flags(queue, record))

    assert "Open findings:" not in joined


# --- the seeded-mutant panel credential (C15) on the digest -----------------
# Every verdict states what its own control measured, in the same words, every
# time: a verdict that does not carry its own limitations is read as a
# warranty. "caught", never "verified".


def test_uncredentialed_verdict_says_so_explicitly(queue):
    """Silence is not a disposition: a run with no control says so, rather
    than letting the reader assume the panel was measured."""
    task_id = "20260916-digestnocred"
    record = _make_task(queue, task_id)
    _write_verdict(queue, task_id, _verdict(passed=True, open_findings=[]))

    joined = "\n".join(_arc_flags(queue, record))

    assert "panel credential: none" in joined
    assert "no seeded-mutant control ran" in joined


def test_a_caught_mutant_renders_the_catch_rate(queue):
    task_id = "20260916-digestcaught"
    record = _make_task(queue, task_id)
    _write_verdict(queue, task_id, _verdict(passed=True, open_findings=[], mutant_status="caught"))

    joined = "\n".join(_arc_flags(queue, record))

    assert "panel credential: seeded mutant CAUGHT (quantity)" in joined
    assert "catch rate 1/1" in joined
    # a caught mutant is a floor-raiser, never a certificate
    assert not re.search(r"\bverified\b", joined)


def test_a_missed_mutant_is_flagged_with_a_warning(queue):
    task_id = "20260916-digestmissed"
    record = _make_task(queue, task_id)
    _write_verdict(queue, task_id, _verdict(passed=True, open_findings=[], mutant_status="missed"))

    joined = "\n".join(_arc_flags(queue, record))

    assert "⚠" in joined
    assert "seeded mutant MISSED (quantity)" in joined
    assert "catch rate 0/1" in joined
    # ... and it is a flag, not a block: the verdict still passed
    assert "VERIFICATION FAILED" not in joined


def test_an_unmeasured_mutant_is_neither_a_catch_nor_a_miss(queue):
    task_id = "20260916-digestnotmeasured"
    record = _make_task(queue, task_id)
    _write_verdict(
        queue, task_id, _verdict(passed=True, open_findings=[], mutant_status="not-measured")
    )

    joined = "\n".join(_arc_flags(queue, record))

    assert "seeded mutant not measured" in joined
    assert "CAUGHT" not in joined and "MISSED" not in joined


def test_a_fatal_refutations_reason_reaches_the_digest(queue):
    """The veto's reason is the one line the operator must see without opening
    verification.json — open_findings excludes fatal by design (advisor F-1)."""
    from chimera.models import CriticOpinion

    task_id = "20260916-digestfatal"
    record = _make_task(queue, task_id)
    verdict = VerifyResult(
        mode="lite",
        passed=False,
        maker_model="opus",
        critic_model="sonnet",
        opinions=[
            CriticOpinion(
                refuted=True,
                reason="the central claim does not hold: the cited file does not exist",
                severity="fatal",
            ),
            CriticOpinion(refuted=False, reason="anchors held", severity="cosmetic"),
        ],
        valid_critic_count=2,
        unrefuted_count=1,
    )
    _write_verdict(queue, task_id, verdict)

    joined = "\n".join(_arc_flags(queue, record))

    assert "VERIFICATION FAILED" in joined
    assert "Fatal refutation: the central claim does not hold" in joined

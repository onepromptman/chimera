"""notify.signoff_comment must surface VerifyResult.open_findings verbatim.

F1: the fatal-only veto means a PASSING panel can still carry material/
cosmetic refutations (recorded in open_findings, never blocking). Before this
test, signoff_comment rendered the tally but not the findings themselves, so
an auto-approved PASS posted an Issue comment that hid them.
"""

from __future__ import annotations

from chimera import notify
from chimera.models import TaskRecord, TaskSpec, Transition, VerifyResult


def _record(state: str = "done") -> TaskRecord:
    spec = TaskSpec(id="20260916-notifytest", slug="notifytest", ask="a", arc="graph")
    return TaskRecord(
        spec=spec,
        state=state,
        history=[Transition(from_state=None, to_state="ready", by="t")],
    )


def _verdict(*, passed: bool, open_findings: list[str]) -> VerifyResult:
    return VerifyResult(
        mode="lite",
        passed=passed,
        maker_model="opus",
        critic_model="sonnet",
        opinions=[],
        valid_critic_count=2,
        unrefuted_count=1 if open_findings else 2,
        open_findings=open_findings,
    )


def test_open_findings_render_verbatim_on_a_passing_verdict():
    findings = [
        "[material] the retry loop never re-reads the lockfile",
        "[cosmetic] stale count in the docstring",
    ]
    verdict = _verdict(passed=True, open_findings=findings)
    comment = notify.signoff_comment(_record(), verdict, "artifacts/graph-output.md")
    assert "Open findings:" in comment
    for finding in findings:
        assert finding in comment


def test_no_open_findings_section_when_there_are_none():
    verdict = _verdict(passed=True, open_findings=[])
    comment = notify.signoff_comment(_record(), verdict, "artifacts/graph-output.md")
    assert "Open findings:" not in comment


def test_signoff_comment_discloses_the_panel_credential():
    """An auto-approved PASS posts its own credential on the Issue: the reader
    must never have to assume the panel was measured. "caught", never
    "verified"."""
    from chimera.models import MutantRun
    from chimera.verify import lite

    plain = notify.signoff_comment(
        _record(), _verdict(passed=True, open_findings=[]), "artifacts/graph-output.md"
    )
    assert "- panel credential: panel-uncredentialed" in plain

    caught = _verdict(passed=True, open_findings=[])
    run = MutantRun(status="caught")
    credentialed = caught.model_copy(
        update={"mutant": run, "panel_credential": lite.credential(run)}
    )
    comment = notify.signoff_comment(_record(), credentialed, "artifacts/graph-output.md")
    assert "- panel credential: mutant-caught (seeded defect caught" in comment
    assert "verified" not in comment


def test_open_findings_also_render_on_a_failing_verdict():
    findings = ["[fatal] the central claim does not hold"]
    verdict = _verdict(passed=False, open_findings=findings)
    comment = notify.signoff_comment(
        _record(state="awaiting-signoff"), verdict, "artifacts/graph-output.md"
    )
    assert "Open findings:" in comment
    assert findings[0] in comment

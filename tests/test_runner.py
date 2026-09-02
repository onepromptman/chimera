"""Runner policies: call ceiling, timeout detection, gap-collision guard."""

from datetime import datetime, timedelta, timezone

import pytest

from chimera import runner
from chimera.models import AgentCall, AuditTrail


def test_ceiling_aborts_run():
    audit = AuditTrail()
    for i in range(runner.AGENT_CALL_CEILING):
        runner.issue_call(audit, f"label-{i}")
    with pytest.raises(runner.CeilingExceeded, match="250"):
        runner.issue_call(audit, "one-too-many")
    assert audit.agent_calls_ceiling_exceeded == 1


def test_null_degrade_counters():
    audit = AuditTrail()
    runner.record_null(audit, "a")
    runner.record_null(audit, "b", kind="timeout")
    runner.record_null(audit, "c", kind="threw")
    assert audit.agent_calls_returned_null == 1
    assert audit.agent_calls_timed_out == 1
    assert audit.agent_calls_threw == 1


# (runner.is_timed_out was the v6 research arc's native expiry helper — deleted
# in the 2026-08-28 optimization batch; per-call expiry lives in
# _common.expired_labels, covered by the timeout parity suite.)

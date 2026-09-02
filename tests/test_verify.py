"""Proportional-majority survival rule + maker≠checker."""

import pytest

from chimera.models import CriticOpinion
from chimera.verify import lite


def op(refuted: bool) -> CriticOpinion:
    return CriticOpinion(refuted=refuted, reason="x")


# (valid opinions as refuted-flags, expected survives) — None = degraded critic
CASES = [
    # 3 valid: strict majority -> need 2+ unrefuted (v2.0 rule preserved)
    ([False, False, False], True),
    ([False, False, True], True),
    ([False, True, True], False),
    ([True, True, True], False),
    # 2 valid: unanimous required (no plurality possible)
    ([False, False, None], True),
    ([False, True, None], False),
    ([True, True, None], False),
    # 0-1 valid: too thin -> drop
    ([False, None, None], False),
    ([True, None, None], False),
    ([None, None, None], False),
]


@pytest.mark.parametrize("flags,expected", CASES)
def test_proportional_majority(flags, expected):
    opinions = [op(f) if f is not None else None for f in flags]
    survives, valid, unrefuted = lite.survives(opinions)
    assert survives is expected
    assert valid == sum(1 for f in flags if f is not None)
    assert unrefuted == sum(1 for f in flags if f is False)


def test_maker_neq_checker_enforced():
    with pytest.raises(lite.MakerCheckerViolation):
        lite.assert_maker_neq_checker(maker_model="opus", critic_model="opus")
    lite.assert_maker_neq_checker(maker_model="opus", critic_model="sonnet")


def test_default_models_differ():
    lite.assert_maker_neq_checker()  # must not raise with shipped constants


def test_critic_calls_use_critic_model():
    calls = lite.critic_calls("verify", "payload")
    assert len(calls) == 3
    assert all(c.model == lite.resolve_models().critic for c in calls)
    assert all(c.schema_name == "CriticOpinion" for c in calls)
    assert [c.label for c in calls] == ["verify:critic1", "verify:critic2", "verify:critic3"]


def test_verdict_shape():
    v = lite.verdict("lite", [op(False), op(False), op(True)])
    assert v.passed is True
    assert v.valid_critic_count == 3
    assert v.unrefuted_count == 2
    assert v.maker_model != v.critic_model

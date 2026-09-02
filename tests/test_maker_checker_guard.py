"""M4 — maker ≠ checker, enforced in code, resolved at call time.

derive_research_critic_model draws ONLY from the operator's configured tiers
(the 2026-08-28 audit's OP-13 killed the hardcoded-alias fallback); when no
configured tier differs from the producer it refuses rather than pretending,
and lite's panel refuses a forced-equal override.
"""

from __future__ import annotations

import pytest

from chimera import graph
from chimera.agents import (
    MakerCheckerViolation,
    ResolvedModels,
    derive_research_critic_model,
    resolve_models,
)
from chimera.models import GraphPlan
from chimera.verify import lite

_KEYS = (
    "CHIMERA_MAKER_MODEL",
    "CHIMERA_CRITIC_MODEL",
    "CHIMERA_RESEARCH_MODEL",
    "CHIMERA_JUDGE_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(
    "producer,models,expected",
    [
        # default posture: critic differs from a fast producer -> critic tier
        ("sonnet", ResolvedModels("opus", "sonnet", "sonnet", "opus"), "opus"),
        ("opus", ResolvedModels("opus", "sonnet", "sonnet", "opus"), "sonnet"),
        # an unknown producer id still draws a CONFIGURED tier, never a literal
        ("claude-x", ResolvedModels("opus", "sonnet", "sonnet", "opus"), "sonnet"),
        # critic pinned equal to the producer -> falls to the MAKER tier
        ("pinned", ResolvedModels("other", "pinned", "pinned", "other"), "other"),
    ],
)
def test_derive_checker_model_table(producer, models, expected):
    assert derive_research_critic_model(producer, models) == expected


def test_derive_refuses_when_no_configured_tier_differs():
    """Every tier pinned to one model: no distinct checker exists — refuse,
    never invent an alias the operator did not configure (audit OP-13)."""
    models = ResolvedModels("one", "one", "one", "one")
    with pytest.raises(MakerCheckerViolation, match="no configured model tier"):
        derive_research_critic_model("one", models)


def _fast_producer_plan() -> GraphPlan:
    return GraphPlan(
        goal="g",
        rationale="r",
        phases=[
            {"name": "make", "nodes": [
                {"id": "m", "role": "researcher", "tier": "fast", "brief": "x"}
            ]},
            {"name": "check", "nodes": [
                {"id": "c", "role": "critic", "brief": "x", "reads": ["m"]}
            ]},
        ],
    )


def test_graph_checker_node_model_is_distinct_under_forced_equal_env(monkeypatch):
    """Even when an operator pins research and critic to the SAME model, an
    in-graph checker over a fast node still derives distinct — by
    construction, not by trusting the env. No reload needed: call-time."""
    monkeypatch.setenv("CHIMERA_RESEARCH_MODEL", "sonnet")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "sonnet")
    plan = _fast_producer_plan()
    checker = plan.phases[1].nodes[0]
    assert graph.node_model(plan, checker) != resolve_models().research


def test_readless_judge_rides_the_judge_tier(monkeypatch):
    """A checker with no producer reads runs the JUDGE tier: the maker alias
    by default, raised by CHIMERA_JUDGE_MODEL alone — so raising the fan-in's
    tier can never collapse maker ≠ checker."""
    plan = GraphPlan(
        goal="g",
        rationale="r",
        phases=[{"name": "merge", "nodes": [{"id": "j", "role": "judge", "brief": "x"}]}],
    )
    judge = plan.phases[0].nodes[0]
    assert graph.node_model(plan, judge) == resolve_models().maker
    monkeypatch.setenv("CHIMERA_JUDGE_MODEL", "fable")
    assert graph.node_model(plan, judge) == "fable"


def test_forced_equal_override_raises_maker_checker_violation(monkeypatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "opus")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "opus")
    with pytest.raises(lite.MakerCheckerViolation):
        lite.assert_maker_neq_checker()

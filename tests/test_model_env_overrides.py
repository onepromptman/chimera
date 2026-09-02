"""CHIMERA_*_MODEL env levers — resolved at CALL time (2026-08-28 audit,
roadmap #4).

resolve_models() reads the environment on every call, so overrides need no
module reload, the maker≠checker guard compares CURRENT values, and
CHIMERA_JUDGE_MODEL rides the maker alias until deliberately raised.
"""

from __future__ import annotations

import pytest

from chimera.agents import MakerCheckerViolation, resolve_models
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


def test_defaults_when_no_env():
    m = resolve_models()
    assert (m.maker, m.critic, m.research) == ("opus", "sonnet", "sonnet")
    assert m.judge == m.maker  # judge rides the maker tier until raised


def test_overrides_apply_without_reload(monkeypatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "claude-sonnet-4-7")
    m = resolve_models()
    assert m.maker == "claude-opus-4-8"
    assert m.critic == "claude-sonnet-4-7"
    assert m.judge == "claude-opus-4-8"  # follows the raised maker


def test_empty_env_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "   ")
    m = resolve_models()
    assert (m.maker, m.critic) == ("opus", "sonnet")


def test_judge_lever_raises_planner_tier_without_touching_others(monkeypatch):
    monkeypatch.setenv("CHIMERA_JUDGE_MODEL", "fable")
    m = resolve_models()
    assert m.judge == "fable"
    assert m.maker == "opus"
    assert m.critic == "sonnet"


def test_env_changed_after_import_reaches_the_guard(monkeypatch):
    """The audit's OP-13 scenario: an env set AFTER import must not slip past
    the maker≠checker guard — resolution is call-time, not import-time."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "same-model")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "same-model")
    with pytest.raises(MakerCheckerViolation):
        lite.assert_maker_neq_checker()


def test_installed_role_files_carry_current_models(monkeypatch, tmp_path):
    """install-agents renders the operator's CURRENT posture, not the
    import-time one."""
    from chimera import agents

    monkeypatch.setenv("CHIMERA_JUDGE_MODEL", "claude-judge-x")
    agents.install_roles(tmp_path)
    planner_md = (tmp_path / "planner.md").read_text(encoding="utf-8")
    judge_md = (tmp_path / "judge.md").read_text(encoding="utf-8")
    maker_md = (tmp_path / "maker.md").read_text(encoding="utf-8")
    assert "model: claude-judge-x" in planner_md
    assert "model: claude-judge-x" in judge_md
    assert "model: opus" in maker_md

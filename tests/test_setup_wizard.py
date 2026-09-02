"""First-run setup: preflight honesty and write safety.

Two properties matter here and neither is cosmetic.

`run_checks` is the only place a new operator learns their model tiers
collide *before* an arc spends maker calls and then refuses at the checker's
phase. If it ever reports a clean bill on a same-model install, the check is
worse than absent, because it manufactures confidence. So the collision case
is asserted directly, not just the happy path.

`init` writes a file into a repo the operator already has. Setup that
silently replaces prior configuration is a data-loss bug, so the refuse and
backup paths are pinned too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera import setup_wizard


def _lookup(results: list[tuple[str, bool, str]], name: str) -> tuple[bool, str]:
    for got, ok, detail in results:
        if got == name:
            return ok, detail
    raise AssertionError(f"no check named {name!r} in {[r[0] for r in results]}")


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def test_preflight_passes_maker_checker_when_tiers_differ(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "model-a")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "model-b")
    ok, _ = _lookup(setup_wizard.run_checks(repo), "maker != checker")
    assert ok


def test_preflight_fails_loudly_when_maker_equals_critic(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """The load-bearing check. A same-model panel still RUNS -- it just is
    not adversarial -- so nothing else in the system would surface this."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "model-a")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "model-a")
    results = setup_wizard.run_checks(repo)
    ok, detail = _lookup(results, "maker != checker")
    assert not ok
    assert "model-a" in detail
    # and the overall verdict must be failure, not a warning buried in a list
    assert setup_wizard.print_checks(results) is False


def test_preflight_flags_a_non_git_root(tmp_path: Path):
    """chimera's durability IS git; a non-repo cannot checkpoint at all."""
    ok, detail = _lookup(setup_wizard.run_checks(tmp_path), "git repo")
    assert not ok
    assert "not a git repo" in detail


def test_preflight_reports_levers_and_writes_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CHIMERA_GRAPH_WIDTH", "7")
    before = sorted(p.name for p in repo.iterdir())
    _, detail = _lookup(setup_wizard.run_checks(repo), "autonomy levers")
    assert "width_max=7" in detail
    assert sorted(p.name for p in repo.iterdir()) == before


def test_check_only_mode_creates_no_env(repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "model-a")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "model-b")
    assert setup_wizard.init(repo, check_only=True) == 0
    assert not (repo / ".env").exists()


# ---------------------------------------------------------------------------
# Write safety
# ---------------------------------------------------------------------------

def test_init_writes_every_configured_lever(repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "model-a")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "model-b")
    assert setup_wizard.init(repo, non_interactive=True) == 0

    body = (repo / ".env").read_text(encoding="utf-8")
    for key in (
        "CHIMERA_AUTHOR",
        "CHIMERA_MAKER_MODEL",
        "CHIMERA_CRITIC_MODEL",
        "CHIMERA_RESEARCH_MODEL",
        "CHIMERA_JUDGE_MODEL",
        "CHIMERA_DB_PATH",
        "CHIMERA_GRAPH_WIDTH",
        "CHIMERA_GRAPH_PHASES",
        "CHIMERA_GRAPH_CALL_BUDGET",
        "CHIMERA_GRAPH_REPAIR_LAPS",
        "CHIMERA_RESEARCH_MCP_TOOLS",
    ):
        assert f"{key}=" in body, f"{key} missing from generated .env"


def test_init_refuses_to_clobber_an_existing_env(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "model-a")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "model-b")
    env = repo / ".env"
    env.write_text("CHIMERA_AUTHOR=do-not-lose-me\n", encoding="utf-8")

    assert setup_wizard.init(repo, non_interactive=True) == 1
    assert "do-not-lose-me" in env.read_text(encoding="utf-8")


def test_force_overwrites_but_only_when_asked(repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "model-a")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "model-b")
    env = repo / ".env"
    env.write_text("CHIMERA_AUTHOR=stale\n", encoding="utf-8")

    assert setup_wizard.init(repo, non_interactive=True, force=True) == 0
    assert "stale" not in env.read_text(encoding="utf-8")


def test_init_reports_failure_when_the_install_is_misconfigured(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """A written .env plus a failing preflight must still exit nonzero --
    otherwise setup 'succeeds' into a install that cannot verify anything."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "same")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "same")
    assert setup_wizard.init(repo, non_interactive=True) == 1
    assert (repo / ".env").exists()


def test_generated_env_holds_no_secrets(repo: Path, monkeypatch: pytest.MonkeyPatch):
    """The package holds no tokens and setup must not invent a place for one:
    a generated file that looks like a credential store invites pasting one
    into a tracked path."""
    monkeypatch.setenv("CHIMERA_MAKER_MODEL", "model-a")
    monkeypatch.setenv("CHIMERA_CRITIC_MODEL", "model-b")
    setup_wizard.init(repo, non_interactive=True)
    body = (repo / ".env").read_text(encoding="utf-8").lower()
    for forbidden in ("api_key", "api-key", "token", "secret", "password"):
        assert forbidden not in body

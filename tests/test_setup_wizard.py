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

import subprocess
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


def test_preflight_reports_levers_and_writes_nothing(repo: Path, monkeypatch: pytest.MonkeyPatch):
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
        "CHIMERA_GRAPH_REPAIR_LAPS",
        "CHIMERA_RESEARCH_MCP_TOOLS",
    ):
        assert f"{key}=" in body, f"{key} missing from generated .env"


def test_init_refuses_to_clobber_an_existing_env(repo: Path, monkeypatch: pytest.MonkeyPatch):
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


def test_identity_comes_from_the_target_repo_not_the_cwd(tmp_path, monkeypatch):
    """`--root <elsewhere>` must stamp THAT repo's configured identity.

    Regression: _git_identity() ran bare `git config user.name`, inheriting
    the cwd, so initialising a different repo wrote the operator's identity
    from whichever checkout they happened to be standing in.
    """
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "config", "user.name", "Target Repo Owner"], cwd=target, check=True)

    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    subprocess.run(["git", "config", "user.name", "Somebody Else"], cwd=other, check=True)

    monkeypatch.chdir(other)
    setup_wizard.init(target, non_interactive=True)

    assert "CHIMERA_AUTHOR=Target Repo Owner" in (target / ".env").read_text()


def test_missing_git_remote_is_a_warning_not_a_failure(tmp_path):
    """A remote-less repo is a legitimate install: chimera's durability is the
    commit, and push health is reported separately by `chimera status`. A hard
    failure here would make a fresh `git init` look broken."""
    repo = tmp_path / "local-only"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    results = setup_wizard.run_checks(repo)
    remote = [r for r in results if r[0] == "git remote"]
    assert remote, "the remote check should still be reported"
    assert remote[0][1] is True, "no remote must not fail preflight"
    assert "never push" in remote[0][2]
    assert all(ok for _, ok, _ in results), "a remote-less repo passes preflight"

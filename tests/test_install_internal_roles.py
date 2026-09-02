"""Role export via install-agents — the six fenced roles, nothing else.

Covers:
- render_internal_role_md() produces valid frontmatter + body
- internal_roles() is exactly the six-role roster
- install_roles() writes <role>.md files into the target dir
- idempotency: a second install skips unchanged files
- dry_run: files are not written but counts are reported
"""
from __future__ import annotations

from chimera import agents

ROLE_NAMES = {"planner", "researcher", "maker", "executor", "critic", "judge"}


def test_render_produces_yaml_frontmatter():
    role = agents.ROSTER["judge"]
    md = agents.render_internal_role_md(role)
    assert md.startswith("---\n")
    assert "name: judge\n" in md
    assert "model: " in md
    # the documented harness form is a bare comma-separated string; the YAML
    # flow-list form (`tools: [...]`) is rejected at subagent launch and the
    # old test asserted the renderer's own bug back at it (audit SN-1)
    assert f"tools: {', '.join(role.allowed_tools)}\n" in md
    assert "tools: [" not in md


def test_render_description_is_one_liner():
    role = agents.ROSTER["planner"]
    md = agents.render_internal_role_md(role)
    desc_line = next(l for l in md.splitlines() if l.startswith("description:"))
    assert desc_line[len("description:"):].strip()


def test_render_body_contains_full_system_prompt():
    role = agents.ROSTER["maker"]
    md = agents.render_internal_role_md(role)
    _, _, body = md.partition("---\n\n")
    assert role.system_prompt.strip()[:40] in body


def test_internal_roles_is_exactly_the_six_role_roster():
    assert set(agents.internal_roles()) == ROLE_NAMES


def test_install_writes_all_six_role_files(tmp_path):
    summary = agents.install_roles(tmp_path)
    assert summary["ok"] is True
    assert sorted(summary["written"]) == sorted(ROLE_NAMES)
    for name in ROLE_NAMES:
        assert (tmp_path / f"{name}.md").exists()


def test_install_is_idempotent(tmp_path):
    agents.install_roles(tmp_path)
    second = agents.install_roles(tmp_path)
    assert second["written"] == []
    assert sorted(second["skipped"]) == sorted(ROLE_NAMES)


def test_install_dry_run_writes_nothing(tmp_path):
    summary = agents.install_roles(tmp_path, dry_run=True)
    assert summary["dry_run"] is True
    assert sorted(summary["written"]) == sorted(ROLE_NAMES)
    assert not list(tmp_path.glob("*.md"))


def test_mcp_lever_extends_researcher_and_critic_only(tmp_path, monkeypatch):
    """CHIMERA_RESEARCH_MCP_TOOLS (e.g. Exa) widens exactly the two read+web
    fences; the other four roles never see an mcp grant."""
    monkeypatch.setenv(
        "CHIMERA_RESEARCH_MCP_TOOLS",
        "mcp__exa__web_search_exa,mcp__exa__get_code_context_exa",
    )
    agents.install_roles(tmp_path)
    for name in ("researcher", "critic"):
        text = (tmp_path / f"{name}.md").read_text(encoding="utf-8")
        assert "mcp__exa__web_search_exa" in text
        assert "mcp__exa__get_code_context_exa" in text
    for name in ("planner", "maker", "executor", "judge"):
        text = (tmp_path / f"{name}.md").read_text(encoding="utf-8")
        assert "mcp__" not in text


def test_mcp_lever_is_strict_list_wide(monkeypatch):
    """Any malformed entry reads the WHOLE lever as unset — a typo is not a
    decision, and a partially-honored grant would be a silent one."""
    monkeypatch.setenv("CHIMERA_RESEARCH_MCP_TOOLS", "mcp__exa__ok, not-an-mcp-tool")
    assert agents.research_mcp_tools() == ()
    monkeypatch.setenv("CHIMERA_RESEARCH_MCP_TOOLS", "mcp__exa__ok,")
    assert agents.research_mcp_tools() == ()
    monkeypatch.setenv("CHIMERA_RESEARCH_MCP_TOOLS", "mcp__exa__ok, mcp__exa__also_ok")
    assert agents.research_mcp_tools() == ("mcp__exa__ok", "mcp__exa__also_ok")


def test_installed_tools_match_the_fences(tmp_path):
    """The rendered frontmatter carries the fence grant verbatim — the file a
    session resolves can never be wider than roles.FENCES."""
    from chimera.roles import FENCES

    agents.install_roles(tmp_path)
    for name in ROLE_NAMES:
        text = (tmp_path / f"{name}.md").read_text(encoding="utf-8")
        tools_line = next(l for l in text.splitlines() if l.startswith("tools:"))
        # parse the DOCUMENTED form (bare comma list), not the renderer's own
        # output format — a tautological parse can never catch a format bug
        assert not tools_line.startswith("tools: [")
        listed = {t.strip() for t in tools_line[len("tools: "):].split(",")}
        assert listed == set(FENCES[name].tools)

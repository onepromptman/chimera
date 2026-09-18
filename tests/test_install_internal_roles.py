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
    desc_line = next(line for line in md.splitlines() if line.startswith("description:"))
    assert desc_line[len("description:") :].strip()


def test_render_body_contains_full_system_prompt():
    role = agents.ROSTER["maker"]
    md = agents.render_internal_role_md(role)
    _, _, body = md.partition("---\n\n")
    assert role.system_prompt.strip()[:40] in body


def test_role_prompts_carry_the_prelude_the_calls_stopped_repeating():
    """The role prelude is delivered ONCE, by the installed role file.

    Two halves, both load-bearing: the roster prompt carries the identity,
    discipline and output-contract SHAPE that used to be re-authored in every
    per-call brief, and every graph role actually resolves to a roster
    subagent — so a call really does arrive with it. If subagent_for ever
    stopped resolving, the calls would be preludeless."""
    from chimera import graph
    from chimera.roles import FENCES

    for role in FENCES:
        subagent, reason = graph.subagent_for(role)
        assert subagent == role, f"{role} does not dispatch to its role file: {reason}"

    for role in ("researcher", "maker", "executor", "critic", "judge"):
        prompt = agents.ROSTER[role].system_prompt
        assert "GraphNodeOutput" in prompt and '"recommendation"' in prompt
    assert '"phases"' in agents.ROSTER["planner"].system_prompt  # GraphPlan shape
    assert "GraphArtifact" in agents.ROSTER["maker"].system_prompt
    assert '"refuted"' in agents.ROSTER["critic"].system_prompt  # CriticOpinion shape
    for role in ("critic", "judge"):  # the checker discipline, once per checker role
        assert "SURVIVES your rubric" in agents.ROSTER[role].system_prompt


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
        tools_line = next(line for line in text.splitlines() if line.startswith("tools:"))
        # parse the DOCUMENTED form (bare comma list), not the renderer's own
        # output format — a tautological parse can never catch a format bug
        assert not tools_line.startswith("tools: [")
        listed = {t.strip() for t in tools_line[len("tools: ") :].split(",")}
        assert listed == set(FENCES[name].tools)


# --- effort frontmatter ---------------------------------------------------
# Before 2026-09-11 all six roles inherited one session effort level. The
# renderer emitted only name/description/tools/model, so hand-editing the
# installed files was reverted by the next `chimera install-agents`. These
# tests are what stop that regression.


def test_every_role_pins_an_effort():
    from chimera.agents import ROSTER

    expected = {
        "planner": "xhigh",
        "researcher": "medium",
        "maker": "high",
        "executor": "low",
        "critic": "high",
        "judge": "xhigh",
    }
    assert {n: a.effort for n, a in ROSTER.items()} == expected


def test_renderer_emits_effort():
    from chimera.agents import ROSTER, render_internal_role_md

    for name, agent in ROSTER.items():
        md = render_internal_role_md(agent)
        assert f"effort: {agent.effort}\n" in md, name
        # frontmatter only — the pin must sit above the closing fence
        head = md.split("---")[1]
        assert f"effort: {agent.effort}" in head, name


def test_unset_effort_omits_the_line_entirely():
    """An empty value would pin the role to a level that does not exist;
    omitting the key is what makes it inherit the session level."""
    from chimera.agents import ROSTER, render_internal_role_md

    bare = ROSTER["critic"].model_copy(update={"effort": ""})
    assert "effort:" not in render_internal_role_md(bare)


def test_no_role_requests_max_effort():
    """`max` is not available on every model tier; pinning it where the
    shipped model does not support it is a configuration bug."""
    from chimera.agents import ROSTER

    assert all(a.effort != "max" for a in ROSTER.values())

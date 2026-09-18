"""The shippable plugin: one source of truth, and a guard against drift.

`plugins/chimera/` is what `/plugin marketplace add onepromptman/chimera`
installs. Its six agent files are GENERATED from `agents.ROSTER`, so the risk is
that someone edits a shipped file and the next `install-agents` silently reverts
it — the same class of bug that kept `effort:` off the roles. These tests make
that drift a red suite instead of a surprise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.agents import ROSTER, internal_roles, render_internal_role_md

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "chimera"
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"

# Portable skills ship in the plugin and run in any repo. Engine skills need this
# checkout's Python package, so they stay project-scoped under .claude/skills/.
SHIPPED_SKILLS = ("chimera-check", "chimera-graph")
ENGINE_SKILLS = ("chimera-dag",)


def _skill_path(skill: str) -> Path:
    if skill in SHIPPED_SKILLS:
        return PLUGIN / "skills" / skill / "SKILL.md"
    return REPO / ".claude" / "skills" / skill / "SKILL.md"


def test_marketplace_manifest_is_valid_and_points_at_the_plugin():
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert data["name"] == "chimera"
    assert data["owner"]["name"]
    entries = data["plugins"]
    assert len(entries) == 1, "one plugin; a second needs its own review"
    entry = entries[0]
    assert entry["name"] == "chimera"
    # relative sources resolve from the marketplace root and must start with ./
    assert entry["source"].startswith("./")
    assert (REPO / entry["source"]).is_dir()


def test_plugin_manifest_is_valid():
    data = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert data["name"] == "chimera"  # the one required field
    assert data["name"] == data["name"].lower().replace(" ", "")
    assert data["description"]


def test_component_dirs_sit_at_the_plugin_root_not_inside_claude_plugin():
    """Claude Code discovers agents/ and skills/ at the plugin ROOT. Nesting
    them under .claude-plugin/ yields a plugin that installs and does nothing."""
    assert (PLUGIN / "agents").is_dir()
    assert (PLUGIN / "skills").is_dir()
    assert not (PLUGIN / ".claude-plugin" / "agents").exists()
    assert not (PLUGIN / ".claude-plugin" / "skills").exists()


@pytest.mark.parametrize("skill", SHIPPED_SKILLS)
def test_the_skill_ships_exactly_once(skill: str):
    """One source of truth. A second copy under .claude/skills/ would be the
    duplication this packaging exists to avoid."""
    assert (PLUGIN / "skills" / skill / "SKILL.md").is_file()
    assert not (REPO / ".claude" / "skills" / skill).exists()


def test_the_plugin_ships_exactly_the_portable_skills():
    on_disk = {p.name for p in (PLUGIN / "skills").iterdir() if p.is_dir()}
    assert on_disk == set(SHIPPED_SKILLS)


@pytest.mark.parametrize("skill", ENGINE_SKILLS)
def test_engine_skills_stay_out_of_the_plugin(skill: str):
    """An engine skill drives `chimera new` / `tick` from this checkout. Shipped
    in the plugin it would install everywhere and fail everywhere but here."""
    assert _skill_path(skill).is_file()
    assert not (PLUGIN / "skills" / skill).exists()


@pytest.mark.parametrize("skill", SHIPPED_SKILLS + ENGINE_SKILLS)
def test_skill_frontmatter_parses(skill: str):
    text = _skill_path(skill).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front = text.split("---", 2)[1]
    assert f"name: {skill}" in front
    assert "description:" in front


@pytest.mark.parametrize("role", sorted(ROSTER))
def test_shipped_agent_matches_the_roster(role: str):
    """The drift guard.

    Compare against `internal_roles()`, not raw ROSTER: installation resolves
    each role's model through its tier and widens researcher/critic with any
    operator-granted MCP tools, so ROSTER is the source but internal_roles()
    is what ships.

    Regenerate from THIS checkout — a bare `python -m chimera` resolves the
    editable install to the main clone, which will silently render older code
    when you are in a worktree:

        PYTHONPATH=src python -m chimera install-agents \\
            --target plugins/chimera/agents
    """
    shipped = (PLUGIN / "agents" / f"{role}.md").read_text(encoding="utf-8")
    assert shipped == render_internal_role_md(internal_roles()[role]), (
        f"{role}.md has drifted — regenerate, do not hand-edit"
    )


def test_every_role_ships():
    on_disk = {p.stem for p in (PLUGIN / "agents").glob("*.md")}
    assert on_disk == set(ROSTER)


def test_shipped_agents_carry_their_effort_pin():
    for role, agent in ROSTER.items():
        text = (PLUGIN / "agents" / f"{role}.md").read_text(encoding="utf-8")
        assert f"effort: {agent.effort}" in text.split("---")[1], role


# --- the shipped docs may not describe a rule the code does not implement ---
# Three commits running, a mechanism changed and prose describing the OLD one
# survived somewhere: "severity-gated majority" in the README summary, "a
# cosmetic refutation does NOT fail the panel" in the skill, a stale comment on
# VerifyResult. Each was caught by a human-run panel, one at a time. This turns
# the class into a red suite.
#
# The rule: a live doc may NARRATE a removed mechanism in the past tense, but it
# may not STATE it in the present tense as how the thing works.

LIVE_DOCS = [
    "plugins/chimera/README.md",
    "plugins/chimera/skills/chimera-graph/SKILL.md",
    "plugins/chimera/skills/chimera-check/SKILL.md",
    ".claude/skills/chimera-dag/SKILL.md",
    "CLAUDE.md",
    # the two manifests are user-facing prose too — the marketplace description is
    # what a stranger reads in `/plugin marketplace`, and it advertised the retired
    # severity gate for a full commit after the code dropped it. The first version
    # of this guard listed only the markdown files and missed both.
    ".claude-plugin/marketplace.json",
    "plugins/chimera/.claude-plugin/plugin.json",
]

# phrases that describe mechanisms the code no longer implements
RETIRED_CLAIMS = [
    "severity-gated majority",
    "severity-gated**",
    "does NOT fail the panel",
    "is advisory and does not block",
    "refuted * 2 > valid",
    "unblocked > valid",
    "already installed globally",
    # 2026-09-16: the panel dropped from 3 critics to 2 (GREP-ANCHOR + the
    # executing CONFIRM angle; "general REFUTE" was dropped) and the
    # proportional-majority verdict was replaced by a fatal-only veto.
    "3-critic",
    "unrefuted > valid",
]


@pytest.mark.parametrize("doc", LIVE_DOCS)
def test_live_docs_do_not_state_a_retired_rule(doc: str):
    text = (REPO / doc).read_text(encoding="utf-8")
    hits = [claim for claim in RETIRED_CLAIMS if claim in text]
    assert not hits, (
        f"{doc} states retired mechanism(s) {hits}. The code no longer does this. "
        "Past-tense narration of the change is fine — rephrase, or update the list "
        "in this test if the mechanism genuinely came back."
    )


def test_the_shipped_rule_matches_the_code():
    """Every live doc that restates the verdict rule must state the ONE
    `lite.survives` actually implements: fatal vetoes alone, every other
    refutation is recorded and decides nothing. Merely containing the word
    "fatal" is not sufficient to pass this check — the retired
    proportional-majority rule also mentioned `fatal` (it was the exception
    carved out of the majority count), so a doc could state that whole
    retired rule and still slip past a bare "fatal" substring check. This
    also asserts the majority formula is gone outright, not just narrated."""
    from chimera.verify import lite

    RULE_MARKER = "SURVIVE"  # every doc that restates the ascii rule box uses it
    for doc in LIVE_DOCS:
        text = (REPO / doc).read_text(encoding="utf-8")
        if RULE_MARKER not in text:
            continue  # this doc does not restate the rule
        assert "fatal" in text, f"{doc} states the verdict rule but omits the veto"
        assert "unrefuted > valid" not in text, f"{doc} still states the retired majority rule"
        assert "unrefuted &gt; valid" not in text, f"{doc} still states the retired majority rule"

    # and the code really is the two-line rule the docs describe
    def op(refuted, severity=None):
        from chimera.models import CriticOpinion

        return CriticOpinion(refuted=refuted, reason="x", severity=severity)

    assert lite.survives([op(False)])[0] is False  # valid < 2
    assert lite.survives([op(False), op(False), op(True, "fatal")])[0] is False  # veto
    assert lite.survives([op(False), op(False), op(True, "material")])[0] is True
    # two non-fatal refutations, no veto among them -> still survives; there is
    # no majority count left to tie (the closed 2026-09-11 tie-hole)
    assert lite.survives([op(False), op(True, "material"), op(True, "cosmetic")])[0] is True

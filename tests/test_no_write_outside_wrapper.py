"""Committed verification hook: arc authors never commit, push, or
checkpoint directly — runner.checkpoint()/the CLI wrapper owns durability.
The deeper maker≠checker invariant rides along: critics are roster-defined
without Edit/Write/Bash, and arcs contain no git side-channels."""

import re
from pathlib import Path

from chimera.agents import ROSTER

ARCS_DIR = Path(__file__).resolve().parent.parent / "src" / "chimera" / "arcs"

FORBIDDEN = (
    re.compile(r"\bcheckpoint\s*\("),
    re.compile(r"\bgitio\b"),
    re.compile(r"\bsubprocess\b"),
    re.compile(r"git\s+(add|commit|push)"),
)


def test_arcs_never_checkpoint_or_touch_git():
    offenders = []
    for path in ARCS_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            for match in pattern.finditer(text):
                offenders.append(f"{path.name}: {match.group(0)!r}")
    assert not offenders, (
        "arc authors must not own durability — call sites found: " + "; ".join(offenders)
    )


def test_critics_cannot_edit_artifacts():
    """Critic identification is role-based (name == 'critic' or endswith
    '-critic'), not model-string based — a model=='sonnet' filter breaks the
    moment a critic's model is remapped (M4 pairs research's investigate
    critics off RESEARCH_CRITIC_MODEL, not the global CRITIC_MODEL)."""
    critics = [
        d for d in {**ROSTER, **{}}.values()
        if d.name == "critic" or d.name.endswith("-critic")
    ]
    assert critics, "roster must declare critics"
    for d in critics:
        assert not set(d.allowed_tools) & {"Edit", "Write", "Bash"}, (
            f"critic {d.name} must not hold write/exec tools"
        )

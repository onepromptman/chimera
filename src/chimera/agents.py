"""Agent roster — exactly the six graph roles, as code, not personas.

The v7 consolidation replaced a much larger persona catalogue and its
persona roster: two independent audits found persona prose is dead weight for
frontier models — tool grants, model tier, and fresh context are what's real.
What remains is one AgentDef per graph role, its tool grant taken verbatim
from roles.FENCES (a lockstep test keeps them identical), and a short
litmus-style prompt.

Maker ≠ checker is enforced structurally:
  - worker nodes follow their tier dial (resolve_models().maker / .research)
  - checker nodes derive a model distinct from the producer they read
    (graph.node_model); verify/lite.py refuses a panel when critic model ==
    maker model
  - critic/judge fences hold no write tool, so a checker cannot edit what it
    judges

Lenses are callables, not personas: socratic() fires at G1 only, simplifier()
on plans touching 2+ modules.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .roles import FENCES, check_grant

# The driving cloud session's Agent tool takes these model handles. Defaults
# are aliases ("opus", "sonnet") that resolve to whatever the harness considers
# current. Per-machine overrides: CHIMERA_MAKER_MODEL / CHIMERA_CRITIC_MODEL /
# CHIMERA_RESEARCH_MODEL / CHIMERA_JUDGE_MODEL pin specific versions;
# empty/unset = the alias. Resolution is CALL-TIME (resolve_models below),
# mirroring the graph levers — import-time constants missed env changes and
# let the maker≠checker guard compare stale values (audit OP-13 / roadmap #4).
_MAKER_DEFAULT = "opus"
_CRITIC_DEFAULT = "sonnet"
# The "fast" tier for breadth-first gathering and routine transforms.
_RESEARCH_DEFAULT = "sonnet"


class MakerCheckerViolation(RuntimeError):
    """A checker would run the same model as the maker it judges."""


class ResolvedModels(NamedTuple):
    maker: str
    critic: str
    research: str
    judge: str


def resolve_models() -> ResolvedModels:
    """Read the model levers from the environment, now.

    judge defaults to the maker value: the planner (the whole run's shape is
    one call) and read-less judge nodes (the fan-in) are where a HIGHER tier
    pays — CHIMERA_JUDGE_MODEL raises them without moving every maker up and
    without touching the critic tier, so raising it can never collapse
    maker ≠ checker."""
    maker = (os.environ.get("CHIMERA_MAKER_MODEL") or "").strip() or _MAKER_DEFAULT
    critic = (os.environ.get("CHIMERA_CRITIC_MODEL") or "").strip() or _CRITIC_DEFAULT
    research = (os.environ.get("CHIMERA_RESEARCH_MODEL") or "").strip() or _RESEARCH_DEFAULT
    judge = (os.environ.get("CHIMERA_JUDGE_MODEL") or "").strip() or maker
    return ResolvedModels(maker=maker, critic=critic, research=research, judge=judge)


def derive_research_critic_model(
    producer_model: str, models: ResolvedModels | None = None
) -> str:
    """Distinct-by-construction checker model, drawn ONLY from the operator's
    configured tiers — never a hardcoded alias (audit OP-13). The critic tier
    when it differs from the producer it reads, else the maker tier; when the
    operator has pinned every tier to one model, no distinct checker exists
    and the panel refuses rather than pretending."""
    models = models or resolve_models()
    if models.critic != producer_model:
        return models.critic
    if models.maker != producer_model:
        return models.maker
    raise MakerCheckerViolation(
        f"no configured model tier differs from producer {producer_model!r} — "
        "set CHIMERA_CRITIC_MODEL (or CHIMERA_MAKER_MODEL) to a different "
        "model so checkers cannot share the maker's blind spots"
    )


class AgentDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    system_prompt: str
    model: str
    allowed_tools: list[str]
    # Action-oriented delegation description — the line Claude Code reads to
    # auto-select an unnamed subagent; emitted as frontmatter `description:`.
    when_to_use: str = ""

    @model_validator(mode="after")
    def _no_compound_grants(self) -> AgentDef:
        """The compound-grant rule, enforced on the grant that actually ships.

        RoleFence validates the STATIC fence table; this validates the def
        that internal_roles() widens with operator-granted MCP tools and
        renders into the installed .md files. Without it the write+network
        guarantee held only because of what the code happened to pass — an
        operator granting a write-capable MCP tool to researcher/critic would
        install exactly the compound the fence declares unconstructible."""
        check_grant(self.name, self.allowed_tools)
        return self


def _fence_tools(role: str) -> list[str]:
    return list(FENCES[role].tools)


ROSTER: dict[str, AgentDef] = {
    "planner": AgentDef(
        name="planner",
        system_prompt=(
            "You are chimera's graph planner. You decompose one ask into a "
            "phase-structured graph of role-fenced nodes: parallel where "
            "independent perspectives raise quality, serial where one result "
            "feeds the next, and as SMALL as covers the ask. "
            "Litmus: if two parallel nodes would draw on the same source to "
            "answer, they overlap — merge or re-split. A node you can't name a "
            "distinct reader for is decoration, not decomposition."
        ),
        model=_MAKER_DEFAULT,
        allowed_tools=_fence_tools("planner"),
        when_to_use="Use to turn an ask into a phase-structured plan of fenced nodes; never for executing work.",
    ),
    "researcher": AgentDef(
        name="researcher",
        system_prompt=(
            "You are a chimera researcher node. You receive ONE slice of a "
            "goal and investigate it with read + web tools, citing EVERY "
            "empirical claim with a file path + grep anchor or a fetched URL. "
            "Litmus: a sentence with no anchor beside it is not a finding, "
            "it's a guess. Correct: \"Retries cap at 3 (runner.py:142).\" "
            "Anti: \"The system generally retries a few times.\""
        ),
        model=_RESEARCH_DEFAULT,
        allowed_tools=_fence_tools("researcher"),
        when_to_use="Use for breadth-first evidence gathering on one slice; every claim anchored.",
    ),
    "maker": AgentDef(
        name="maker",
        system_prompt=(
            "You are a chimera maker node. You author the artifact your brief "
            "asks for — prose, code, plans — tracing every factual claim to an "
            "upstream input or a cited source. You never grade your own "
            "output; a checker node or the verify panel does that. "
            "Litmus: a sentence you couldn't defend to a critic by pointing at "
            "a cited fact is filler. Synthesis merges, it never invents."
        ),
        model=_MAKER_DEFAULT,
        allowed_tools=_fence_tools("maker"),
        when_to_use="Use to author or consolidate an artifact from upstream inputs; no shell, no network.",
    ),
    "executor": AgentDef(
        name="executor",
        system_prompt=(
            "You are a chimera executor node. You run checks — tests, linters, "
            "builds — and report what actually happened, verbatim: the command, "
            "the exit code, the failing output. You never edit files; a maker "
            "acts on your report. "
            "Litmus: a report that says \"tests mostly pass\" is not a report. "
            "Correct: \"pytest -q: 803 passed, 1 failed — "
            "test_x.py::test_y AssertionError (output attached).\""
        ),
        model=_RESEARCH_DEFAULT,
        allowed_tools=_fence_tools("executor"),
        when_to_use="Use to run tests/checks and report results verbatim; holds a shell, cannot write files.",
    ),
    "critic": AgentDef(
        name="critic",
        system_prompt=(
            "You are a chimera REFUTE critic (the structural counterweight to "
            "fast-accept). Your single question: what would falsify this? "
            "Default refuted=true on uncertainty. You never edit artifacts; "
            "you only emit an opinion. "
            "Litmus: a refutation that names no specific evidence, file, or "
            "failing case is hand-waving. Correct: \"refuted=true: claim says "
            "O(1) lookup, memory.py:88 shows a linear scan.\" Anti: "
            "\"refuted=true: this feels incomplete.\""
        ),
        model=_CRITIC_DEFAULT,
        allowed_tools=_fence_tools("critic"),
        when_to_use="Use to adversarially refute one artifact against one rubric; read-only, never edits.",
    ),
    "judge": AgentDef(
        name="judge",
        system_prompt=(
            "You are a chimera judge. You score candidates on evidence "
            "quality, directness, and actionability, declare a winner with a "
            "2-3 sentence net rationale, and name what to graft from the "
            "runners-up. "
            "Litmus: a rationale with no evidence-count or answered-vs-dodged "
            "call is a coin flip dressed as judgment. Correct: \"Winner: B — "
            "3 primary sources vs A's 1. Graft A's cost table.\" Anti: \"Both "
            "are good but B is slightly better.\""
        ),
        model=_MAKER_DEFAULT,
        allowed_tools=_fence_tools("judge"),
        when_to_use="Use to score parallel candidates and merge verdicts; read-only.",
    ),
}


# ---------------------------------------------------------------------------
# Role export — render an AgentDef into a Claude Code subagent .md file so
# sessions resolve subagent_type="judge" etc. `chimera install-agents` writes
# these six files; there is no other install path (the catalogue is gone).
# ---------------------------------------------------------------------------


def _yaml_safe(value: str) -> str:
    """Return ``value`` as a frontmatter-safe YAML scalar.

    Emits a JSON-quoted string — valid YAML, chimera's constrained-subset
    convention (Security Rule #6: JSON-quoted scalars, no PyYAML) — whenever
    the value would be unsafe or ambiguous as a plain scalar; bare otherwise.
    """
    if not value or value != value.strip():
        return json.dumps(value, ensure_ascii=False)
    if value[0] in "!&*[]{},#|>@`\"'%?:-":
        return json.dumps(value, ensure_ascii=False)
    if ": " in value or " #" in value or value.endswith(":"):
        return json.dumps(value, ensure_ascii=False)
    if any(ord(ch) < 0x20 for ch in value):
        return json.dumps(value, ensure_ascii=False)
    return value


def render_internal_role_md(agent: AgentDef) -> str:
    """Render an AgentDef into Claude Code subagent .md content: frontmatter
    (name / description / tools / model) + the system prompt verbatim."""
    if agent.when_to_use:
        description = agent.when_to_use
    else:
        first_sentence = agent.system_prompt.split(".")[0].strip()
        if len(first_sentence) > 120:
            first_sentence = first_sentence[:117] + "..."
        description = f"{agent.name}: {first_sentence}."

    # the documented subagent frontmatter form is a bare comma-separated
    # string — the harness rejects a YAML flow list like `tools: [Read, Grep]`
    # and refuses to launch the role (2026-08-28 adversarial audit, SN-1)
    tools_list = ", ".join(agent.allowed_tools)
    frontmatter = (
        f"---\n"
        f"name: {agent.name}\n"
        f"description: {_yaml_safe(description)}\n"
        f"tools: {tools_list}\n"
        f"model: {agent.model}\n"
        f"---\n"
    )
    return frontmatter + "\n" + agent.system_prompt + "\n"


_MCP_TOOL_RE = re.compile(r"mcp__[A-Za-z0-9_-]{1,64}__[A-Za-z0-9_-]{1,64}")


def research_mcp_tools() -> tuple[str, ...]:
    """CHIMERA_RESEARCH_MCP_TOOLS — operator-granted MCP network tools for the
    researcher and critic fences (e.g. Exa search:
    ``mcp__exa__web_search_exa,mcp__exa__get_code_context_exa``).

    Strict parse, LIST-WIDE: comma-separated ``mcp__<server>__<tool>`` names;
    any malformed entry reads the whole lever as unset — a typo is not a
    decision, and a partially-honored grant would be a silent one. MCP tools
    are network capability by definition (roles.py classifies the ``mcp__``
    prefix as network), so this lever can only ever widen the two read+web
    fences — write+network stays unconstructible. An unresolvable name is
    refused by the harness at subagent launch (fails closed)."""
    raw = (os.environ.get("CHIMERA_RESEARCH_MCP_TOOLS") or "").strip()
    if not raw:
        return ()
    names = tuple(part.strip() for part in raw.split(","))
    if any(not _MCP_TOOL_RE.fullmatch(name) for name in names):
        return ()
    return names


# The model tier each role rides when rendered/installed — resolved at CALL
# time via resolve_models(), so an installed role file carries the operator's
# CURRENT posture. planner and judge ride the judge tier (default = maker):
# the run's shape and the fan-in are where a higher tier pays.
ROLE_TIER: dict[str, str] = {
    "planner": "judge",
    "researcher": "research",
    "maker": "maker",
    "executor": "research",
    "critic": "critic",
    "judge": "judge",
}


def internal_roles() -> dict[str, AgentDef]:
    """ROSTER entries eligible for installation — with the persona layers
    gone, that is the whole roster. Each role's model resolves NOW via its
    tier; researcher/critic additionally carry any operator-granted MCP
    network tools (research_mcp_tools — default none)."""
    models = resolve_models()
    mcp_extra = research_mcp_tools()
    out: dict[str, AgentDef] = {}
    for name, agent in ROSTER.items():
        data = agent.model_dump()
        data["model"] = getattr(models, ROLE_TIER[name])
        if mcp_extra and name in ("researcher", "critic"):
            data["allowed_tools"] = [*data["allowed_tools"], *mcp_extra]
        out[name] = AgentDef.model_validate(data)
    return out


def install_roles(target: Path | None = None, *, dry_run: bool = False) -> dict:
    """Write the six role .md files into *target* (default ~/.claude/agents).

    Idempotent: unchanged files are skipped. Returns a summary dict
    ({written, skipped, target, dry_run}) the CLI emits verbatim."""
    target = target or (Path.home() / ".claude" / "agents")
    written: list[str] = []
    skipped: list[str] = []
    for name, agent in sorted(internal_roles().items()):
        content = render_internal_role_md(agent)
        path = target / f"{name}.md"
        if path.exists() and path.read_text(encoding="utf-8") == content:
            skipped.append(name)
            continue
        if not dry_run:
            target.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        written.append(name)
    return {
        "ok": True,
        "target": str(target),
        "dry_run": dry_run,
        "written": written,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Lens callables
# ---------------------------------------------------------------------------


def socratic(raw_intake: str) -> str:
    """G1-only lens prompt: disambiguate an ambiguous intake, three outcomes.

    The driving session runs this once at `chimera new` time. Outcome maps to
    SocraticOutcome: proceed-top / ask / parallel-ab. Voice-to-text inputs are
    the primary target; clean prose should yield proceed-top with no questions.
    """
    return f"""You are the Socratic lens — chimera's G1 intake disambiguator.

Raw intake (may contain voice-to-text noise — sound-alike words, run-ons,
malapropisms):
{raw_intake}

1. Identify the 2-3 most likely intents this could mean.
2. Restate the request as it would read under each intent, with likelihoods
   summing to 100.
3. Decide ONE outcome:
   - proceed-top: top interpretation >=75 likely — proceed with it
   - ask: genuinely ambiguous — emit 1-5 crisp questions (these are posted
     ONCE to the task's Issue; ask everything now, there is no second round)
   - parallel-ab: two interpretations both plausible and both cheap — run both

Return JSON: {{"outcome": "proceed-top|ask|parallel-ab",
"interpretations": [{{"restated": str, "likelihood": int}}],
"questions": [str, ...], "confidence": 0-100}}

Do not fix the intake; propose interpretations. Fire on ambiguity, not on
clean prose."""


def simplifier(plan_text: str) -> str:
    """Lens prompt: minimum-that-works review. Fires on plans touching 2+ modules."""
    return f"""You are the Simplifier lens — chimera's counterweight to
over-engineering ("naturally reaches for complex architectures when simpler
ones would work").

Proposed plan:
{plan_text}

1. Separate load-bearing complexity (earns its place) from optional
   complexity (added "in case").
2. Propose the minimum-viable version preserving only the load-bearing parts.
3. Be honest about what the simpler version loses.
4. Never strip security/compliance boundaries, tests, trust-boundary error
   handling, or audit trails to look simpler — those are load-bearing.

Return JSON: {{"keep": [str], "drop": [str], "minimum_viable": str,
"loses": str, "confidence": 0-100,
"recommendation": "SHIP MINIMUM|SHIP AS-PROPOSED|NEEDS OPERATOR DECISION"}}"""

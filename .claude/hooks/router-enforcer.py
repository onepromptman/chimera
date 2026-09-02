#!/usr/bin/env python3
"""PreToolUse:Agent router — ADVISORY (log-only) hook over chimera.routing (v6).

Demoted from enforcement to telemetry after a routing-behavior review:
verb-substring
capability inference over prompt prose produced false-positive denies on
analysis tasks (any prompt *about* chimera infers the full cap set), and the
deny path bounced sessions to subagent types that need not resolve at the
session layer. The properties enforcement claimed to protect (read-only
critics, maker!=checker) are already enforced in AgentDef.allowed_tools,
verify/lite.py, and the pytest suite. A frontier-class session picking its own
subagent beats keyword heuristics second-guessing it.

The decision core (verb->capability inference, read-only fix, specialist
matching) lives in src/chimera/routing.py where it is unit-tested
(tests/test_routing.py preserves the V5.1-validated case suite) and its
deny-*/allow-* taxonomy is kept as the telemetry vocabulary: a would-deny is
logged with its suggested specialist so reflect can mine routing fit, but the
Agent call always proceeds.

This wrapper keeps only the Claude Code hook I/O contract and the
fail-open guarantee: any unhandled exception => {"continue": true}.
This hook is wired per-project via .claude/settings.json
($CLAUDE_PROJECT_DIR), not globally.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

# Resolve chimera from THIS file, never from CLAUDE_PROJECT_DIR: worktree
# checkouts get their own copy of this hook, and __file__ is always
# <checkout>/.claude/hooks/router-enforcer.py — two levels up is the checkout
# root where the routing core and telemetry log live.
CHIMERA_DIR = Path(__file__).resolve().parents[2]
LOG_PATH = CHIMERA_DIR / ".claude" / "telemetry" / "router-interceptions.jsonl"
_LOG_MAX_BYTES = 10 * 1024 * 1024

sys.path.insert(0, str(CHIMERA_DIR / "src"))
sys.path.insert(0, str(CHIMERA_DIR / "agents"))  # for catalogue.manifest (domain registry)


def _log(record: dict) -> None:
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
            LOG_PATH.rename(LOG_PATH.with_suffix(".jsonl.1"))
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _excerpt(text: str, n: int = 240) -> str:
    text = " ".join(text.split())
    return text[:n] + ("..." if len(text) > n else "")


def _emit_allow() -> None:
    sys.stdout.write(json.dumps({"continue": True}))
    sys.stdout.flush()


def main() -> None:
    sid = "unknown"
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            _emit_allow()
            return
        event = json.loads(raw)
        sid = event.get("session_id", "unknown")
        if event.get("tool_name", "") != "Agent":
            _emit_allow()
            return
        tool_input = event.get("tool_input", {}) or {}
        requested = (tool_input.get("subagent_type") or "").strip()
        prompt = str(tool_input.get("prompt", ""))
        description = str(tool_input.get("description", ""))

        if requested and requested != "general-purpose":
            _log(
                {
                    "ts": time.time(),
                    "sid": sid,
                    "subagent_type_requested": requested,
                    "decision": "allow-named",
                    "prompt_excerpt": _excerpt(prompt),
                }
            )
            _emit_allow()
            return

        from chimera.routing import catalogue_registries, decide_domain

        tool_registry, cap_registry = catalogue_registries()
        decision, reason, matches, required = decide_domain(
            prompt, description, tool_registry, cap_registry
        )
        _log(
            {
                "ts": time.time(),
                "sid": sid,
                "subagent_type_requested": requested or "general-purpose",
                "decision": decision,
                "advisory": not decision.startswith("allow"),
                "matched_specialists": matches,
                "required_caps": sorted(required),
                "reason": reason if not decision.startswith("allow") else None,
                "prompt_excerpt": _excerpt(prompt),
            }
        )
        _emit_allow()
    except Exception as exc:
        _log(
            {
                "ts": time.time(),
                "sid": sid,
                "decision": "allow-error",
                "error": repr(exc),
                "trace": traceback.format_exc(limit=3),
            }
        )
        _emit_allow()


if __name__ == "__main__":
    main()

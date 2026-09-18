#!/usr/bin/env python3
"""PreToolUse:Agent hook — excerpt-free telemetry. Never denies, never reads
prompt content.

Demoted from enforcement to telemetry 2026-07-25 (Fable discovery,
decisions/plans/2026-07-25-fable-discovery-synthesis.md): verb-substring
capability inference over prompt prose produced false-positive denies on
analysis tasks, and the properties enforcement claimed to protect (read-only
critics, maker!=checker) are already enforced in roles.FENCES, verify/lite.py
and the pytest suite.

Reduced to a counter 2026-09-02 (audit P1-5 / Q12). Two things changed:

1. The hook no longer imports chimera at all. The domain router it called
   ranked candidates from `agents/catalogue`, which ADR-010 deleted, so it
   scored an empty registry and allowed unconditionally; `chimera.routing`
   now carries only the pick-validation the graph runtime calls.
2. The hook NEVER writes prompt or description text. The old `prompt_excerpt`
   field put raw work content into a file that a rotation script then copied
   into tracked, mirror-eligible audits — the root cause of that audit's
   P0-1. The scrub is now structural: the text is never read into a record,
   so there is nothing to strip and no hand-scan to trust (Security Rule #1).

What is logged is a per-call counter: when, which session, which subagent
type was asked for, on what model, and whether that was a named specialist
or the generalist. Those three copied fields are identifiers, so each is
capped at 64 chars — prose planted in `subagent_type` is not a way back into
the log. The log stays gitignored and is rotated at 10 MB.

Contract: the Agent call ALWAYS proceeds. Any unhandled exception =>
{"continue": true}. Wired per-project via .claude/settings.json
($CLAUDE_PROJECT_DIR), not globally.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Resolve the log from THIS file, never from CLAUDE_PROJECT_DIR: worktree
# checkouts get their own copy of this hook, and __file__ is always
# <checkout>/.claude/hooks/router-enforcer.py — two levels up is the checkout
# root where the telemetry log lives.
CHIMERA_DIR = Path(__file__).resolve().parents[2]
LOG_PATH = CHIMERA_DIR / ".claude" / "telemetry" / "router-interceptions.jsonl"
_LOG_MAX_BYTES = 10 * 1024 * 1024

# Every key this hook may ever write. Free text (prompt, description, agent
# output, exception messages) is not on it and is never assembled into a
# record in the first place.
_FIELDS = ("ts", "sid", "subagent_type_requested", "model", "decision")

# The three fields that ARE copied from the payload are identifiers — a
# session id, a subagent type, a model name. Nothing legitimate is long, and
# a caller controls their content, so an oversized value is prose smuggled
# into an identifier field. Cap it where it is copied.
_FIELD_MAX_CHARS = 64


def _log(record: dict) -> None:
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
            LOG_PATH.rename(LOG_PATH.with_suffix(".jsonl.1"))
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        safe = {k: record[k] for k in _FIELDS if k in record}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(safe, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _emit_allow() -> None:
    sys.stdout.write(json.dumps({"continue": True}))
    sys.stdout.flush()


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            _emit_allow()
            return
        event = json.loads(raw)
        if event.get("tool_name", "") != "Agent":
            _emit_allow()
            return
        tool_input = event.get("tool_input", {}) or {}
        requested = (tool_input.get("subagent_type") or "").strip()[:_FIELD_MAX_CHARS]
        record = {
            "ts": time.time(),
            "sid": str(event.get("session_id", "unknown"))[:_FIELD_MAX_CHARS],
            "subagent_type_requested": requested or "general-purpose",
            "decision": (
                "allow-named" if requested and requested != "general-purpose" else "allow-general"
            ),
        }
        model = tool_input.get("model")
        if model:
            record["model"] = str(model)[:_FIELD_MAX_CHARS]
        _log(record)
    except Exception:
        # Fail open, and log nothing: an exception message can quote the
        # payload that caused it.
        pass
    _emit_allow()


if __name__ == "__main__":
    main()

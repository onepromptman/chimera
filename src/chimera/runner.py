"""Step wrapper — the runtime hardening policies.

Policies (same constants, same semantics as safeAgent + the v2.1 guards):
  - AGENT_CALL_CEILING = 250: hard cap on issued agent calls per task run
  - PER_BRANCH_TIMEOUT_S = 300: a pending call older than this is marked
    timed-out and treated as null
  - logged null-degrades: null / timeout / threw are counted, never raised
    (except the ceiling, which aborts the run)
  - checkpoint(): commit+push each artifact as it materializes. Called by
    the wrapper/CLI ONLY — never by arc authors. tests/test_no_write_outside_wrapper.py
    greps src/chimera/arcs/ to enforce this.
"""

from __future__ import annotations

from pathlib import Path

from . import gitio
from .models import AuditTrail

AGENT_CALL_CEILING = 250
PER_BRANCH_TIMEOUT_S = 300


class CeilingExceeded(RuntimeError):
    pass


def issue_call(audit: AuditTrail, label: str) -> None:
    """Count an issued agent call; abort the run past the ceiling."""
    audit.agent_calls_attempted += 1
    if audit.agent_calls_attempted > AGENT_CALL_CEILING:
        audit.agent_calls_ceiling_exceeded += 1
        raise CeilingExceeded(
            f"AGENT_CALL_CEILING ({AGENT_CALL_CEILING}) exceeded at label={label}; abort run"
        )
    audit.by_label[label] = audit.by_label.get(label, 0) + 1


def record_null(audit: AuditTrail, label: str, kind: str = "null") -> None:
    """Log a degraded call. kind: null | timeout | threw."""
    if kind == "timeout":
        audit.agent_calls_timed_out += 1
    elif kind == "threw":
        audit.agent_calls_threw += 1
    else:
        audit.agent_calls_returned_null += 1


def checkpoint(root: Path, paths: list[Path], message: str) -> str | None:
    """Commit+push an artifact the moment it materializes.

    Durable-state-first: uncommitted state dies with ephemeral containers.
    Push failure degrades to commit-only (push retries internally with
    backoff); the next checkpoint or tick pushes the backlog.
    """
    sha = gitio.commit(root, paths, message)
    if sha is not None:
        gitio.push(root)
    return sha

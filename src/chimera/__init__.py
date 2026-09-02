"""chimera v7 — git-durable background task engine.

The package is the deterministic skeleton; a Claude Code cloud session is
the runtime that drives it (tick protocol, see TICK_PROTOCOL.md). All task
state lives in git — every transition is a commit, so an ephemeral
container dying mid-arc costs nothing but the uncommitted in-flight call.
"""

__version__ = "7.1.1"  # kept in lockstep with pyproject.toml (tested)

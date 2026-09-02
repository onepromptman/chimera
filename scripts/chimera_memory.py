#!/usr/bin/env python3
"""CLI shim — the memory backend moved in-package (v6).

Canonical implementation: src/chimera/memory.py. This shim preserves the
`python scripts/chimera_memory.py <cmd>` invocation surface used by agents
and docs since v5. Memory-search-before-draft + dedup-on-write remain law.

DO NOT DELETE: 4 live invocation sites still call this exact path (CLAUDE.md
command table, README.md, .claude/skills/checkpoint/SKILL.md,
the checkpoint skill) — deleting it breaks those callers
even though all real logic lives in chimera/memory.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from chimera.memory import main  # noqa: E402

if __name__ == "__main__":
    main()

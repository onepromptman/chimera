#!/usr/bin/env python3
"""Rotate/scrub chimera router telemetry into a tracked, metadata-only audit.

The live log (``.claude/telemetry/router-interceptions.jsonl``) is gitignored and
may hold work content in ``prompt_excerpt``. Tracked audits
(``audits/router-audit-*.jsonl``) are publishable and MUST be
metadata-only. This strips every field NOT on the allowlist, so no hand-scan is
trusted and any future free-text field the hook adds cannot silently leak. The routing signal reflect globs (decision / matches / caps /
reason) is preserved.

Usage:
  rotate_router_audit.py scrub  <in.jsonl> [out.jsonl]
  rotate_router_audit.py rotate <YYYY-MM-DD>        # live log -> committed audit
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ALLOWLIST, not denylist: keep only known-safe routing metadata. Anything else
# (prompt_excerpt, error, and any field added later) is dropped by construction.
KEEP = {
    "ts",
    "sid",
    "subagent_type_requested",
    "decision",
    "advisory",
    "matched_specialists",
    "required_caps",
    "reason",
    "registry_size",
}

REPO = Path(__file__).resolve().parents[1]
LIVE_LOG = REPO / ".claude/telemetry/router-interceptions.jsonl"


def scrub_lines(lines):
    kept, dropped_keys = [], {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        for k in rec.keys() - KEEP:
            dropped_keys[k] = dropped_keys.get(k, 0) + 1
        kept.append({k: rec[k] for k in rec if k in KEEP})
    return kept, dropped_keys


def _write(path: Path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def cmd_scrub(inp: str, outp: str | None):
    src = Path(inp)
    records, dropped = scrub_lines(src.read_text(encoding="utf-8").splitlines())
    dst = Path(outp) if outp else src
    _write(dst, records)
    print(f"scrub: {src} -> {dst}  ({len(records)} records)")
    print(f"       dropped fields: {dropped or 'none'}")


def cmd_rotate(date: str):
    if not LIVE_LOG.exists():
        sys.exit(f"no live log at {LIVE_LOG}")
    records, dropped = scrub_lines(LIVE_LOG.read_text(encoding="utf-8").splitlines())
    dst = REPO / f"audits/router-audit-{date}.jsonl"
    _write(dst, records)
    print(f"rotate: {LIVE_LOG} -> {dst}  ({len(records)} records)")
    print(f"        dropped fields: {dropped or 'none'}")
    print("        live log NOT truncated (do that explicitly once the audit is committed)")


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd = argv[1]
    if cmd == "scrub" and len(argv) >= 3:
        cmd_scrub(argv[2], argv[3] if len(argv) > 3 else None)
    elif cmd == "rotate" and len(argv) >= 3:
        cmd_rotate(argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)

---
name: checkpoint
description: >
  Session save-point for chimera. Commits + pushes WIP, writes a dated
  checkpoint under wip/checkpoints/, and sweeps the harness auto-memory dir into
  the canonical ~/.chimera/memory.db so user/feedback/project notes are
  searchable next session via FTS5. Use at end of any substantive session, when switching
  workstreams, or before stopping. Trigger phrases: "checkpoint", "save
  point", "wrap up", "session end".
---

# Checkpoint Skill (chimera)

Chimera is durable-state-first: every queue transition is a commit, every
artifact is committed immediately. This skill closes the session by doing
the same for whatever's loose, then sweeping conversational memory into
the FTS5 store so the next session can search it.

## When to use

- End of any session that touched code, queue state, or arc payloads
- Before stopping when uncommitted work would otherwise die
- When the user says "checkpoint", "save", "wrap up"

## When NOT to use

- Mid-tick (let the arc finish its step — checkpoint() is wrapper-only)
- Quick Q&A with no changes (nothing to save)

## Protocol

### Step 1 — Assess state

Run in parallel:
- `git status --short` (what's uncommitted)
- `git log -1 --oneline` (last commit)
- `TaskList` (in-flight tasks)

### Step 2 — Commit outstanding work

If `git status` shows changes:
1. Stage only the files you intended to modify (never `git add -A`)
2. Commit with `chore: checkpoint — <one-line description>`
3. Push to the current branch (don't push if no upstream is set; tell the
   user to `git push -u origin <branch>` themselves)

If working tree is clean, skip this step.

### Step 3 — Sweep auto-memory into SQLite

The harness writes user/feedback/project/reference memories as markdown
under `~/.claude/projects/<project-slug>/memory/` with a `MEMORY.md`
index. Sweep them into the canonical store (`~/.chimera/memory.db`, the
default the CLI resolves to) so they're searchable via FTS5 next session.

```bash
# Project slug is the cwd with / replaced by -, prefixed with -
SLUG=$(echo -n "$PWD" | tr / -)
MEMDIR="$HOME/.claude/projects/${SLUG}/memory"

if [ -f "$MEMDIR/MEMORY.md" ]; then
  python scripts/chimera_memory.py migrate --source "$MEMDIR" --agent user
fi
```

`migrate` is idempotent — re-sweeping skips rows updated in the last 24h.
If `MEMORY.md` doesn't exist (fresh session, nothing remembered yet),
skip silently.

### Step 4 — Write checkpoint file

Write to `wip/checkpoints/<YYYY-MM-DD>-<slug>.md`, where `<slug>` names the
workstream (one file per workstream). `wip/` is gitignored, so checkpoints
stay local and never enter a commit. Create `wip/checkpoints/` if missing.

Keep only the last 3 checkpoint files: after writing, delete older ones so
the directory does not accumulate orphans (the prior pain was suffixed
`.claude-checkpoint-*.md` files piling up at the repo root).

```markdown
# Session Checkpoint

**Created**: <iso8601 UTC>
**Branch**: <branch>
**Last commit**: <hash> — <subject>

## Done this session
- <bullet of what got finished>

## In progress
- <files being edited, with line refs where useful>

## Remaining
- [ ] <next concrete step>

## Resume command
> Read the latest file in `wip/checkpoints/`, then continue from the Remaining list.
```

### Step 5 — Worktree GC + router-telemetry commit

Two hygiene passes:

**Worktree GC** — sweep merged/stale worktrees so `EnterWorktree` names never
collide and stale checkouts stop masquerading as live lanes:

```bash
git worktree list --porcelain | grep -E '^worktree' | awk '{print $2}' | while read wt; do
  case "$wt" in */.claude/worktrees/*) ;; *) continue ;; esac
  br=$(git -C "$wt" branch --show-current)
  # merged into main and clean → remove
  if [ -n "$br" ] && git merge-base --is-ancestor "$br" main 2>/dev/null \
     && [ -z "$(git -C "$wt" status --porcelain)" ]; then
    git worktree remove "$wt" && git branch -d "$br"
  fi
done
git worktree prune
```

Never remove the worktree the session is currently in, and never force-remove
a dirty worktree — list dirty/unmerged ones for the user instead.

**Router-telemetry commit** — the advisory router logs to gitignored
`.claude/telemetry/router-interceptions.jsonl`, but reflect only reads
committed `audits/router-audit-*.jsonl`. Close the loop by rotating
telemetry into a committed audit snapshot:

```bash
TEL=.claude/telemetry/router-interceptions.jsonl
if [ -s "$TEL" ]; then
  SCRUBBED=$(mktemp)
  python3 scripts/rotate_router_audit.py scrub "$TEL" "$SCRUBBED"
  cat "$SCRUBBED" >> "audits/router-audit-$(date +%Y-%m-%d).jsonl"
  rm -f "$SCRUBBED"
  : > "$TEL"
  git add audits/router-audit-*.jsonl
  git commit -m "chore(audits): rotate router telemetry into committed audit"
fi
```

`rotate_router_audit.py scrub` strips every field not on its routing-metadata
allowlist (`prompt_excerpt` included) before anything reaches a tracked file —
by construction, not by hand-scan (Security Rule #1: no hand-scan is trusted).
Route through it every time; never `cat "$TEL"` straight into the tracked
audit file, even for a "quick" rotation.

### Step 6 — Summary

Output one line per Step 1–5 result, including the migrate JSON
`inserted/updated/skipped` counts and any worktrees removed/kept so the user
can see the sweep took.

## What this skill does NOT do

- It does not call `chimera.runner.checkpoint()` — that's reserved for the
  arc-tick wrapper and is grep-enforced (`tests/test_no_write_outside_wrapper.py`)
- It does not transition queue state — use `chimera approve / reject /
  archive` for that
- It does not write to `agent-memory/` markdown files — chimera replaced
  that layer with `agents.py` callables; the auto-memory dir is the only
  markdown memory surface this skill sweeps

## Phase 2 note (when arc memory lands)

Once arcs start writing summaries via `arc_memory.arc_write(...)`, those rows
are already in `memory.db` — no extra sweep step needed. This skill only
moves markdown → SQLite. Arc memory is SQLite-native.

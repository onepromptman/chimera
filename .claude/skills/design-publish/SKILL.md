---
name: design-publish
description: >
  Publish a converged, signed-off chimera design deliverable to an external
  design tool — Claude Design (first-class), Figma, or Canva. Post-G2 and
  opt-in: it never publishes an unapproved deliverable and never auto-runs as an
  arc stage. Boundary-scans every asset + caption before any external call and
  confirms the destination first (work-zone content must never leave). Use after
  `chimera approve` when you want the deliverable pushed to a design tool.
  Trigger phrases: "publish the design", "push to Claude Design / Figma / Canva",
  "sync this deck to my design project", "export the deliverable".
---

# Design Publish Skill (chimera)

Chimera's design arc produces a converged deliverable (HTML/PNG assets +
`design-artifact.md`) and stops at G2. This skill is the **opt-in, post-sign-off
handoff** that gets that deliverable into a design tool. It is deliberately NOT a
stage of the arc — publishing mutates an external service, so it stays behind the
human sign-off gate and an explicit operator request, per chimera's
two-blocking-gate model and the confirm-before-external rule.

## When to use

- The task is **past G2** (`chimera approve` ran; state is `done`, not
  `awaiting-signoff`).
- The operator explicitly wants the deliverable published to Claude Design,
  Figma, or Canva.

If the task is not signed off, refuse and say so. Do not publish on convergence
alone — convergence is a quality bar, not permission to publish.

## Who does the work

**The driving session, itself.** There is no `design-publisher` subagent and
nothing to install first: ADR-010 retired the persona catalogue, and
`chimera install-agents` now writes only the six fenced roles — none of which
holds an external design-tool connector, because a role that could both write
files and reach the network is unconstructible by design (`roles.py`).

So the session reads the artifact, assembles and boundary-scans the bundle,
writes a `publish-manifest.md`, and makes the external call with its own tool.
Auth lives in that tool, never in chimera.

That the scan and the call happen in the same place is the reason the
preconditions below are not optional. Nothing downstream re-checks them.

## Preconditions (all must hold)

1. **Signed off** — task past G2.
2. **Boundary clean** — a scan over every asset + caption finds no
   work-zone or former-employer content, employee data, internal URLs, or credentials.
   This is load-bearing: external publish is a public-remote-class action.
   Reuse the boundary grep already used before any public/tracked output.
3. **Target + destination confirmed** — exactly one target named, and for shared
   spaces the specific project/file confirmed in plain words before the first
   write.

## Fast path (the default — one motion)

For the common case the operator should only have to make ONE light choice and
give ONE yes. Everything safe-by-default happens without extra prompts:

- **NO default target — never assume Claude Design.** The operator has multiple
  subscriptions, so the destination (which tool, and for Claude Design which
  account/project) is never guessed. Ask the target in one short line:
  "Publish to — Claude Design, Figma, Canva, or just export a bundle?" If the
  operator already named one ("push to Figma"), skip the question.
- **Claude Design is an option, not a prerequisite.** The skill works with no
  Claude Design project at all (export a bundle, or pick Figma/Canva). Nothing
  here requires it to exist or be connected.
- **The boundary scan runs automatically** — the operator never invokes it.
- **The only stop is one go/no-go.** After the target is known, show a single
  short summary — "N assets → <target> '<destination>', boundary scan clean" —
  and proceed on a yes. That confirmation IS the quality gate; keep it one line,
  not an interview.

So the happy path is: `chimera approve` (the sign-off you already do) →
"publish it" → pick target (one line) → one-line summary → "yes". Light, but
the destination is always your explicit choice. The steps below run underneath;
surface them only when something needs attention (a boundary hit, a missing
integration, a half-completed transfer).

## Flow (what runs under the fast path)

1. **Locate** the artifact: `tasks/<id>/design-artifact.md` and the rendered
   assets it lists in its frontmatter `assets`.
2. **Boundary scan** the assets + any captions. On any hit: STOP, surface it,
   do not publish. (Automatic — no operator action.)
3. **Confirm destination** in ONE line — the operator's chosen target, and for
   Claude Design the specific account/project (list the writable projects and let
   the operator pick; never auto-select, since multiple subscriptions mean the
   right project is not guessable).
4. **Assemble + manifest**: write `tasks/<id>/publish-manifest.md` recording
   target, destination ids, asset list, and the boundary-scan result — the
   durable, auditable record of the external mutation.
5. **Transfer** via the target tool (below).
6. **Verify + record**: append the returned ids to the manifest so re-runs are
   idempotent.

## Targets

### Claude Design (well-supported option, not the default)

One option among the targets — chosen only when the operator picks it, never
assumed. The session's Claude Design sync tool reads/writes claude.ai
design-system projects. Because the operator has multiple subscriptions, always
`list_projects` and let them pick the right account's project; never auto-select
or create one without asking. Canonical flow (the tool dispatches on `method`):

```
list_projects                  → find a writable design-system project (or create_project)
get_project --project <uuid>   → confirm it is a design system before pushing
list_files / get_file          → diff what's already there (push incrementally, never wholesale replace)
finalize_plan                  → lock the exact write paths + source dir, returns a planId
write_files --planId <id>      → upload the rendered assets / preview HTML as cards
```

- Push assets as **cards grouped by deliverable type** (deck / infographic /
  data-viz / marketing / copy). Preview HTML's first line can carry a
  `<!-- @dsCard group="..." -->` marker so the Design System pane indexes it.
- Sync **incrementally, one component at a time** — never a wholesale replace.
  but that history does not make it the default — confirm the target + project
  each time.

### Figma

Use a Figma MCP integration if the session has one connected. Map deliverables to
a page + frame set. If no Figma integration is available, do **not** fake a
transfer — export a clean, importable asset bundle (PNGs + a manifest) and hand
it back for manual import, saying clearly that no live Figma push happened.

### Canva

Same posture as Figma: use a Canva Connect / MCP integration if present;
otherwise produce an importable bundle and say no live push happened. Never
silently no-op.

## Boundary note

External design services are outside the trust boundary. The boundary scan in
precondition 2 is not optional. chimera runs personal-by-default; publishing to a
shared/external tool is exactly the moment to re-check that nothing work-specific
rode along. When in doubt, stop and ask.

## What this skill does NOT do

- It does not run automatically — no arc stage auto-publishes.
- It does not publish before G2.
- It does not embed or request credentials — the session's tool owns auth.
- It does not edit the deliverable — that work is done; this is transfer only.

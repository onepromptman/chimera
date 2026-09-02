# n8n official skills — adoption pointer + re-sync checklist

> **Retired-arc reference (v7).** The `n8n` arc no longer dispatches — it lives only in `models.RETIRED_ARCS`. This file is kept as distilled craft: the planner composes this shape as data on the one live arc (`graph`). Read arc-present-tense below as describing the shape, not a live execution surface.


chimera's n8n arc adopts n8n's **official skills** as the methodology layer for
the driving session, and keeps its own governance wrapper on top (never-publish,
static structural review, maker≠checker verify). This file is the maintainer
pointer: what is pinned, how the two layers divide, and how to re-sync safely.

## Upstream

- Repo: https://github.com/n8n-io/skills (Apache-2.0)
- Pinned at install: commit `ad41118eb08efa1fe7aa1a8f85a343256113b0cc` (2026-07-07)
- Contents: 14 skills (13 capability + the `using-n8n-skills-official` router),
  paired with n8n's instance-level MCP server.

## Delivery (split by consumer)

| Consumer | Mechanism |
|---|---|
| Local interactive Claude Code (hand-building) | user-level `/plugin marketplace add n8n-io/skills` + `/plugin install n8n-skills@n8n-io` |
| The n8n arc's cloud driving session | repo-pinned in `.claude/settings.json` (`extraKnownMarketplaces` → `n8n-io`; `enabledPlugins` → `n8n-skills@n8n-io`), so it travels with the clone |
| Fallback if the repo-pin is flaky in cloud (a known cloud repo-pin flake) | vendor the skills under `.claude/skills/n8n-*` |

A user-level install does NOT reach an ephemeral cloud session (it lives in the
local `~/.claude/`), which is why the arc relies on the repo-pin, not the local
install.

## Layer split (why chimera's references stay)

- **Official skills** = how the *driving session* uses the MCP (build, validate,
  test). Delivered via the plugin.
- **chimera references** (`n8n-core.md` §§1-12, `n8n-validation-checklist.md`) =
  connective-tissue grounding for chimera's *internal* makers/critics
  (`n8n-architect`, the structure critic), which do NOT have the plugin. Different
  recipients — do not delete them.
- **Governance wrapper** = chimera-native and always on top: the never-publish
  invariant (`N8nFrontmatter.published: Literal[False]`, no `publish_workflow`
  anywhere, the post-build `active == false` check in `_submit_validate`), the
  Level-3 static structural review, and the 3-critic REFUTE verify.

## Connection profiles

Name the MCP server `n8n` in `.mcp.json` (a legacy `n8n-mcp` key still loads
via `enableAllProjectMcpServers`, but the canonical name is `n8n` so
`enabledMcpjsonServers: ["n8n"]` matches). Keep any additional connection
profiles in an untracked file outside the repo.

## Re-sync checklist (run when n8n ships an update)

1. Review the diff since the pinned SHA above. Update the SHA here after adopting.
2. **Publish-semantic audit (load-bearing):** the official skills are
   publish-first. Confirm no updated skill weakens chimera's never-publish stance;
   the arc's `_build_prompt`/`_test_prompt`/`_validate_prompt` publish
   prohibitions and the `active == false` check are NOT delegated to upstream and
   must stay.
3. If any of the four pinned MCP tool names change upstream
   (`create_workflow_from_code`, `validate_workflow`, `prepare_test_pin_data`,
   `test_workflow`, plus `get_workflow_details`), update `arcs/n8n.py` in lockstep
   — `tests/test_n8n_mcp_contract.py` will fail until you do.
4. Re-run `python -m pytest -q` and re-confirm the cloud-delivery smoke test.

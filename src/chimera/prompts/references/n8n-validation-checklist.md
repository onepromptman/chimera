# n8n pre-publish validation checklist

> **Retired-arc reference (v7).** The `n8n` arc no longer dispatches — it lives only in `models.RETIRED_ARCS`. This file is kept as distilled craft: the planner composes this shape as data on the one live arc (`graph`). Read arc-present-tense below as describing the shape, not a live execution surface.


The governance bar for an arc-built workflow: **build → validate → test → STOP.**
A human publishes; the arc never does. This checklist is the multi-level gate
the arc encodes. Each level must pass before the next runs; any failure halts
the arc and surfaces the blocker.

The live MCP gives you `validate_node_config` and `validate_workflow` but **no
autofix** — so when validation fails, you halt and report, you do not silently
repair and continue.

---

## Level 1 — per-node config validation (`validate_node_config`)

For every node in the workflow:

- Required parameters are present (call the tool; do not eyeball it).
- `typeVersion` is set and is a valid version for that node type
  (cross-check with `get_node_types`).
- Credentials are referenced by id from the instance credential manager — **no
  inline secrets, tokens, URLs-with-keys, or org-specific endpoints in node
  parameters**.

→ Any node failing config validation halts the arc.

## Level 2 — whole-workflow validation (`validate_workflow`)

- **Connections**: every node that should be reachable is wired; no orphan
  nodes; no dangling connections.
- **Expressions**: every `{{ }}` resolves; no reference to an unexecuted /
  unreachable node on the active branch (n8n-core §6).
- **Trigger**: exactly one entry trigger appropriate to the ask (webhook /
  schedule / manual / chat), unless the design is intentionally multi-trigger.

→ Any workflow-level validation error halts the arc.

## Level 3 — static structural review (library-only; the MCP can't do this)

This is the unique value the arc adds on top of the MCP. Inspect the design and
the built workflow for the silent-failure classes that validation does NOT
catch:

- **Unreachable cross-node refs** — an expression reads from a node that won't
  have executed on this path (n8n-core §6).
- **Silent Merge mispairs** — a Merge whose combine mode (Matching Fields /
  Position / All Combinations) is wrong for the data; this raises no error
  (n8n-core §7). Confirm the chosen mode matches intent.
- **Unguarded loops** — any Loop Over Items without a provable exit condition;
  an infinite loop on a live instance is a real hazard (n8n-core §10).
- **Branch-index errors** — IF/Switch connections without explicit output
  indices, which can invert logic silently (n8n-core §12).
- **Item-cardinality mismatches** — a downstream node assuming per-item data
  after a collapsing node, or `.item` used after Aggregate/Summarize/Merge
  (n8n-core §2, §4).

→ Any structural finding halts the arc. These are the bugs a "looks-right"
generated workflow hides.

## Level 4 — test execution with pinned data (`prepare_test_pin_data` → `test_workflow`)

- Use `prepare_test_pin_data` to pin realistic-but-safe input so the test does
  not re-fire triggers or hit live systems repeatedly.
- **Never put real PII or secrets in pinned data** — pinned data is saved with
  the workflow.
- Run `test_workflow`. A failed execution halts the arc.
- **Disclose the untestable surface:** the Error Trigger / error-workflow path
  fires only on automatic production failure and **cannot** be exercised here
  (n8n-core §11). Record it as residual, untested risk — do not claim coverage.

## Level 5 — STOP before publish

When Levels 1–4 pass, the workflow is validated and tested but **must remain
unpublished and inactive**. Hand it to the human gate:

- Do **not** call `publish_workflow`, do **not** activate, do **not** flip the
  workflow to active.
- The artifact records `published: false`. Publishing is a separate, explicit,
  post-sign-off step a human performs.

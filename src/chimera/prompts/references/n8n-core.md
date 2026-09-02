# n8n core — connective tissue the live MCP does not give you

> **Retired-arc reference (v7).** The `n8n` arc no longer dispatches — it lives only in `models.RETIRED_ARCS`. This file is kept as distilled craft: the planner composes this shape as data on the one live arc (`graph`). Read arc-present-tense below as describing the shape, not a live execution surface.


You build n8n workflows against a live n8n instance via its MCP server. The MCP
already gives you **node schemas** (`get_node_types`, `search_nodes`), the
**SDK reference** (`get_sdk_reference`), **best practices**
(`get_workflow_best_practices`), and **validation** (`validate_node_config`,
`validate_workflow`). Do not re-derive any of that from memory — call the tool.

This document is the part the MCP does NOT give you: how nodes interact, the
data model every expression rests on, and the failure modes that make an
LLM-generated workflow look right and run wrong. Treat every item below as a
hard rule. Where a fact is version-sensitive, the rule is "confirm via
`get_node_types`," never a hardcoded version.

---

## 1. The item/array data model (the single load-bearing concept)

All data passed between nodes is an **array of items**. Each item is an object
with a `json` key (the structured payload) and an optional `binary` key:

```
[ { "json": { ... }, "binary": { ... } }, { "json": { ... } }, ... ]
```

- Every expression, every Code-node return value, and every item-count
  mismatch derives from this shape. Internalize it before writing anything.
- The Code node auto-adds the `json` wrapper if you forget it, but do not rely
  on that — return `[{ "json": {...} }]`, not `[{...}]`.
- An item may carry both `json` and `binary` at once. Binary requires a
  Base64 `data` field; `mimeType`/`fileName`/`fileExtension` are optional.

## 2. Execution cardinality — "once per item" vs "once for all items"

**Most** nodes run **once per input item**: they iterate the input array, and
`{{ $json.field }}` resolves to the *current* item on each iteration.

- **GUARDRAIL (do not assume N→N):** it is *not* true that an N-item input
  always yields N outputs. Nodes that **collapse** items — Merge, Aggregate,
  Summarize, and several DB/Redis nodes — take many items and emit fewer. Reason
  about each node's actual cardinality, never a blanket "one out per one in."
- The **Code node is the exception**: it defaults to **"Run Once for All
  Items"** (executes a single time, receives the whole array as `$input.all()`).
  Switch it to **"Run Once for Each Item"** only when you genuinely want
  per-item code. Confusing these two scopes is a top generated-workflow bug.

## 3. Paired-item linking

Every output item carries metadata linking it back to the input item(s) that
produced it. This chain is what lets a downstream node reach back with `.item`
(e.g. `$('Webhook').item.json.x`).

- Single-item linking is automatic. You only manage it manually in a **Code
  node that creates new items**: you MUST set `pairedItem` (the input index each
  output derives from) or `.item` fails downstream with
  *"Info for expression missing from previous node."*

## 4. When auto-linking breaks (predictable rules)

n8n auto-links by deterministic rules:

- single-in / single-out → output links to that input
- single-in / multi-out → all outputs link to that one input
- equal-count multi-in / multi-out → **positional** (output-1 ↔ input-1, …)

It **cannot** auto-link when input and output counts are **unequal** or a node
creates **completely new** items — and if the node also doesn't handle linking
itself, n8n raises an error.

- After a collapsing node (Aggregate / Summarize / Merge), `.item` is
  **ambiguous** (one output maps to many inputs) and fails with a
  "multiple matching items" error. Reach back explicitly instead:
  `.first()`, `.last()`, or `.all()[index]`.

## 5. Expression syntax

- Expressions are wrapped in **double curly braces**: `{{ ... }}`.
- The incoming item's data is `$json` → `{{ $json.body.city }}`.
- Another node's output is `$('NodeName')` → `{{ $('Webhook').item.json.headers.authorization }}`.
  Single or double quotes are both valid.
- A field is in **expression mode** vs **fixed mode** — a value typed as plain
  text is fixed; switch the field to expression mode for `{{ }}` to evaluate.

## 6. The dominant expression failure: referencing an unexecuted node

The most common expression error is reading from a node that **hasn't run yet**
on the active branch — *"Referenced node is unexecuted"* / *"Can't get data for
expression."*

- Guard cross-node reads with `$('NodeName').isExecuted` before using the data.
- **Static check:** flag any cross-node reference whose source node is
  unreachable on the branch that will actually execute (e.g. reading from the
  "false" arm of an IF on the "true" path).

## 7. Merge = fan-in; the wrong mode mispairs SILENTLY

Merge is the canonical fan-in node, and its combine modes map to SQL joins:

- **Combine by Matching Fields**: Keep Matches = inner join, Keep Everything =
  outer, Enrich Input 1 = left, Enrich Input 2 = right, plus Keep Non-Matches.
- Three **structurally different** combine strategies: Matching Fields (key
  join), **Position** (index-0 ↔ index-0), **All Possible Combinations**
  (cartesian product).
- Picking the wrong strategy produces **mismatched pairings with no error**. A
  Merge is therefore something to *review*, not just validate — always state
  which mode you chose and why.

## 8. typeVersion changes behavior — pin it

Node behavior is **typeVersion-sensitive**. Merge is the canonical example
(>2 inputs and SQL Query mode arrived in a later version; an old If+Merge
double-execution quirk was removed in v1). A workflow that targets the wrong
node version gets different connection and execution semantics.

- Always **pin `typeVersion`** for every node, and get the current/valid value
  from `get_node_types` — never hardcode a version from memory.

## 9. Loop Over Items (Split in Batches): `loop` vs `done`, and "do you need it?"

- Two outputs with distinct meaning: **`loop`** returns the current batch each
  iteration; **`done`** returns all combined data once the loop finishes.
  Wiring downstream nodes to the wrong output is a structural error.
- You **often don't need a loop**: most nodes already iterate the whole input
  array. Use an explicit loop only for (a) nodes that process just the first
  item (e.g. RSS Feed Read), (b) rate-limited batching, (c) pagination with an
  unknown page count.

## 10. Pagination / loop safety — an exit condition is mandatory

The pagination pattern is **Loop Over Items + an IF node** that evaluates an
exit condition each iteration (using the Reset option).

- **If the termination condition never matches, the workflow runs forever.**
  Every loop you build MUST have a reachable exit condition. Treat a loop
  without a provable exit as a hard failure.

## 11. Error handling — and the test-vs-production boundary

- Error handling is a **separate workflow** whose FIRST node is an **Error
  Trigger**, assigned under the main workflow's Settings → "Error workflow"
  (a workflow with an Error Trigger uses itself by default).
- **CRITICAL governance fact:** the Error Trigger fires **only on automatic /
  production execution failure** — it **cannot** be exercised by a manual/test
  run. So a build-and-test loop **cannot validate the error workflow** via
  manual execution. Always **disclose** this as residual, untested risk; never
  claim the error path was verified.

## 12. Connection wiring + IF/Switch branch routing

- Connection-wiring mistakes are a primary generated-workflow failure mode, and
  malformed connection parameters produce **misleading** errors.
- **IF / Switch** branches need an **explicit output index** on each
  connection. Without it, both connections can land on the same output —
  **silently inverting your logic**. Always set the branch/output index when
  wiring a conditional node, and check it in review.

## 13. Build and lookup tools — signatures owned by the official n8n skills

Tool signatures (`create_workflow_from_code`, `search_workflows`,
`get_node_types`, `validate_node_config`, `validate_workflow`,
`prepare_test_pin_data`, `test_workflow`, `get_workflow_details`) are OWNED by
the official n8n skills and `get_sdk_reference`. Confirm there — never recall
parameters from memory; names and shapes are version-sensitive and the MCP is
the authority. (See prompts/references/n8n-official-skills-ref.md for how the
official skills are adopted and re-synced.)

Two governance facts this arc depends on and does NOT delegate upstream:

- **The created workflow lands inactive.** `create_workflow_from_code` does not
  activate or publish. Never add an activation step — leave it inactive for the
  human gate. The arc confirms this post-build via `get_workflow_details`
  (`active == false`); an active workflow is a governance NO-GO.
- **Check for existing workflows first.** Use `search_workflows` at scope time to
  avoid duplicate names on the instance, and search the public n8n template
  library before designing something that already exists.

---

## How to build (the discipline)

1. **Confirm, don't recall.** Node types, parameters, and versions come from
   `search_nodes` / `get_node_types` / `get_sdk_reference` at build time.
2. **Check for existing workflows** via `search_workflows` before scoping — avoid
   duplicate names (§13).
3. **Plan cardinality per node** (§2, §4) before wiring — know what each node
   emits.
4. **Pin every `typeVersion`** (§8).
5. **Set explicit branch indices** on every IF/Switch connection (§12).
6. **Prove every loop terminates** (§10).
7. **Name the Merge mode** and justify it (§7).
8. **Guard cross-node reads** with `isExecuted` (§6).
9. **Build via `create_workflow_from_code`** — confirm its signature with
   `get_sdk_reference` first; never recall it. The workflow lands inactive (§13).
10. **Never embed secrets** — reference credentials by the instance's credential
    manager, never inline a token (see the validation checklist).
11. **Never publish or activate.** Building, validating, and testing is your job;
    a human publishes after sign-off.

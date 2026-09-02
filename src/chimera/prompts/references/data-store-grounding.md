# Data-store grounding (gemini arc — emit stage)

> **Retired-arc reference (v7).** The `gemini` arc no longer dispatches — it lives only in `models.RETIRED_ARCS`. This file is kept as distilled craft: the planner composes this shape as data on the one live arc (`graph`). Read arc-present-tense below as describing the shape, not a live execution surface.


Chimera-internal reference. Injected into the gemini arc's `emit` prompt for
`data_store`, `rag_corpus`, and `grounding` artifacts so the driving session's
specialist records the right shape of integration instead of assuming a data
store is a byte channel. Brand-agnostic: no project-, brand-, or
deliverable-specific names appear here.

## Record-only boundary (read first)

The arc never provisions these resources and never writes target code. The
**driving session's specialist** emits the Terraform + REST spec into the
target repo's `skeleton/infra-templates/` and reports the paths; the arc
records `emitted_paths` only. Never run `terraform apply` or `gcloud` from an
arc — provisioning stays on the post-G2, human-gated path.

## Data stores are text-chunk RAG indexes, not byte channels

A Discovery Engine / Vertex AI Search data store (`data_store_ids` in an
agent's config) returns **text**, never original file bytes, regardless of
its ingestion connector (GCS import, Drive, Confluence, website, or any
other). Every data store ID is queried identically — a content search over
chunks — and the response carries chunk text plus document metadata
(title/uri), not a downloadable file.

A data store whose resource ID carries a connector suffix in its name (for
example, a store provisioned via a Drive-folder connector) is still the same
text-chunk surface underneath. The connector only determines *how the store
was populated* (which folder/source it crawls and re-indexes), not *what it
returns to a query* — that is always text, never raw bytes, for every
connector type without exception.

## Raw file bytes require a separate, OAuth-scoped tool

If an artifact needs the **original bytes** of a specific file (to read,
transform, or write back a document rather than search over reference
material), that requires a dedicated Drive API tool under user/service OAuth
(`files().get_media()` for arbitrary binary files, `export_media()` for
native Google-Docs-format files) — a distinct capability from any data store,
and the only path that reliably returns original bytes to a custom tool. When
scaffolding a tool that writes back a document, avoid letting the target file
acquire a native Google-Docs/Sheets/Slides mimeType unless that conversion is
intended — auto-conversion to a Google-native format can flatten
format-specific fidelity (e.g. tracked changes in a `.docx`) that a byte-exact
round-trip needs to preserve.

## They are complementary, not substitutable

A data store is for **many-query semantic search** over reference material
that is never itself modified or returned verbatim (playbooks, precedent
examples, knowledge-base docs). Raw-bytes OAuth access is for **the one
artifact that must round-trip exactly** (the document actually being
edited/produced). An artifact spec that needs both — search over reference
material AND a byte-exact read/write of a working document — needs two tools,
not one; do not try to satisfy a byte-fidelity requirement by routing it
through a data store or any other native ingestion/grounding surface. Chat-
upload style ingestion pipelines have the identical limitation (pre-extracted
text/markdown, never the original binary), so "just use the platform's native
upload" is never a fix for a byte-fidelity requirement either.

## What this means for the emitted artifact

When emitting a `data_store` / `rag_corpus` / `grounding` artifact:

- Record it as a **search/grounding surface only** in the manifest and any
  agent config that references it (`data_store_ids`, tool descriptions).
- If the deliverable's `preview`/`config` implies a byte-exact read or write
  requirement, flag that as a **separate tool need** (Drive-OAuth or
  equivalent) in the emitted spec rather than assuming the data store already
  covers it.
- Never describe a data store's contents to the agent's tool description as
  "the document" — describe it as "search results over `<source>`" so the
  model does not treat chunk text as if it were a retrievable original file.

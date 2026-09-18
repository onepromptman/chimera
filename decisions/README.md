# decisions/

Architecture decision records for chimera are maintained in the maintainer's
private repository and are not shipped in this public copy, with one
exception: `ADR-011-build-mode-target-repo.md`, which the test suite reads.

At runtime chimera writes into this directory:

- `decisions/audits/YYYY-MM-DD-topic.jsonl` — router-hook audit rows
- `decisions/plans/` — framework-audit reports

Both are created on first use; neither is needed to install or run chimera.

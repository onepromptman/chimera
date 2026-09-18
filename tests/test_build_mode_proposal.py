"""Build mode is PROPOSED, and the proposal itself is the testable claim.

The CLAUDE.md invariant table carries a Build-mode row ("Proposed: target repo
+ diff artifact + confirm role + PR as artifact, proven first as
`/chimera-build-mode`"), and the table's own rule is that every row has a test
(Behavioral Rule 6). Nothing here asserts behaviour of `src/` — ADR-011 states
plainly that nothing below its Decision is implemented. What IS checkable
today, and what this file pins:

  1. the ADR exists and still reads **Proposed** (a row claiming "Proposed"
     is wrong the moment the ADR is accepted without the row moving);
  2. the prove-first surface exists and parses — the workflow script is the
     thing the row says build mode is proven as;
  3. `target_repo` is retired, not live: it is in `queue.REMOVED_SPEC_FIELDS`
     and absent from `TaskSpec`. ADR-011 re-adds it as a real field, and the
     tuple comment requires it to be dropped from REMOVED_SPEC_FIELDS in that
     same change — so this assertion flips exactly when the row does;
  4. the workflow's declared args are the ones the row's seam needs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from chimera.models import TaskSpec
from chimera.queue import REMOVED_SPEC_FIELDS

_ROOT = Path(__file__).resolve().parents[1]
_ADR = _ROOT / "decisions" / "ADR-011-build-mode-target-repo.md"
_WORKFLOW = _ROOT / ".claude" / "workflows" / "chimera-build-mode.js"


def test_adr_011_exists_and_is_still_proposed():
    assert _ADR.exists(), f"{_ADR.name} is the Build-mode row's citation"
    status = next(
        line
        for line in _ADR.read_text(encoding="utf-8").splitlines()
        if line.startswith("- Status")
    )
    assert "Proposed" in status, (
        f"ADR-011 status line reads {status!r}; the CLAUDE.md Build-mode row says Proposed — "
        "move the row in the same change that accepts the ADR"
    )


def test_prove_first_workflow_exists():
    assert _WORKFLOW.exists(), (
        "the Build-mode row says build mode is proven first as `/chimera-build-mode`"
    )


def test_prove_first_workflow_parses():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the syntax check needs a real parser")
    proc = subprocess.run([node, "--check", str(_WORKFLOW)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_workflow_meta_declares_the_seam_args():
    """ask + targetPath + testCommand + baseBranch + sensitivity — the target
    repo, the test run, the diff base, and the corp/personal fence."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    meta = re.search(r"export const meta = \{.*?\n\}", text, re.DOTALL)
    assert meta, "the workflow must export a `meta` block"
    for arg in ("ask", "targetPath", "testCommand", "baseBranch", "sensitivity"):
        assert re.search(rf"\b{arg}\b", meta.group(0)), f"meta does not name the {arg!r} arg"


def test_target_repo_is_retired_not_live():
    """The field build mode re-introduces is still the RETIRED legacy one."""
    assert "target_repo" in REMOVED_SPEC_FIELDS
    assert "target_repo" not in TaskSpec.model_fields

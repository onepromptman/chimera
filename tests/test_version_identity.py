"""One version, three surfaces — pinned so they can never drift again.

The 2026-08-28 adversarial audit (SN-2) found pyproject at 7.0.0 while
chimera.__version__ said 6.7.0 and the docstrings said v6. The program-facing
number, the packaging number, and the prose major must agree.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import chimera

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_package_version_matches_pyproject():
    meta = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert chimera.__version__ == meta["project"]["version"]


def test_prose_surfaces_name_the_current_major():
    major = chimera.__version__.split(".")[0]
    meta = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert meta["project"]["description"].startswith(f"Chimera v{major}")
    assert (chimera.__doc__ or "").startswith(f"chimera v{major}")

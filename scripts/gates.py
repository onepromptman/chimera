#!/usr/bin/env python3
"""Run the CI gates with the SAME tool versions CI uses, or refuse.

The 2026-09-03 lesson: a container's newer ruff/mypy passed locally while
the pinned versions in CI failed. Every gate here runs through
`python -m <tool>` from the current interpreter, and each tool's version is
checked against the exact pin in pyproject.toml's `[project.optional-
dependencies].dev` BEFORE it runs. A missing or mismatched tool is an error
with the fix in the message, never a silently different check.

    python scripts/gates.py            # every gate
    python scripts/gates.py --no-tests # the static gates only (no pytest)
    python scripts/gates.py -h         # usage

Gates, in order: the lock carries every pyproject pin · pip check · ruff
check · ruff format --check · mypy src · pytest. Lint covers `scripts` and
`.claude` as well as `src tests`, so the files pre-commit sees are the files
CI lints; mypy stays on `src` (the typed half of the boundary).
"""

from __future__ import annotations

import argparse
import importlib.metadata as importlib_metadata
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("ruff", "mypy", "pytest")
LINT_PATHS = ("src", "tests", "scripts", ".claude")
LOCK = "requirements.lock"

# An exact pin and nothing else: a trailing environment marker or any other
# tail does not fullmatch, so the spec is not treated as a pin at all.
PIN_RE = re.compile(r"([A-Za-z0-9_.-]+)==([0-9][^\s;]*)")


def parse_pins(specs: Iterable[str]) -> dict[str, str]:
    """name -> version for EXACT pins only, failing closed.

    `ruff==0.9.6; python_version<"3.12"` is a conditional pin: which version
    is required depends on the interpreter, so this runner cannot claim its
    check is the check CI runs. Such a spec is dropped, the tool then reads as
    unpinned, and `pinned_versions` exits rather than guessing.
    """
    pins: dict[str, str] = {}
    for spec in specs:
        m = PIN_RE.fullmatch(str(spec).strip())
        if m:
            pins[m.group(1).lower()] = m.group(2)
    return pins


def project_pins() -> tuple[dict[str, str], dict[str, str]]:
    """(runtime pins, dev pins) as declared in pyproject.toml."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return (
        parse_pins(project.get("dependencies", [])),
        parse_pins(project.get("optional-dependencies", {}).get("dev", [])),
    )


def pinned_versions() -> dict[str, str]:
    dev = project_pins()[1]
    missing = [t for t in TOOLS if t not in dev]
    if missing:
        sys.exit(f"gates: pyproject.toml dev deps do not pin {missing} exactly")
    return dev


def installed_version(tool: str) -> str | None:
    """The version THIS interpreter would run, as a FULL version string.

    `importlib.metadata` reports `0.9.6.post1` / `0.9.6rc1` / `0.9.6+local`
    verbatim. The old `(\\d+\\.\\d+\\.\\d+)` capture over `--version` output
    kept the first three numbers and threw the suffix away, so all three read
    as the 0.9.6 pin and the runner vouched for a tool CI never runs. The
    `--version` parse survives only as a fallback for a tool importable
    without distribution metadata, and now captures the whole version token.
    """
    try:
        return importlib_metadata.version(tool)
    except importlib_metadata.PackageNotFoundError:
        pass
    proc = subprocess.run([sys.executable, "-m", tool, "--version"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    m = re.search(r"\b(\d+[^\s,()]*)", proc.stdout + proc.stderr)
    return m.group(1) if m else None


def check_versions(pins: dict[str, str]) -> None:
    bad: list[str] = []
    for tool in TOOLS:
        have = installed_version(tool)
        want = pins[tool]
        if have != want:
            bad.append(f"  {tool}: want {want}, have {have or 'not importable'}")
    if bad:
        sys.exit(
            "gates: tool versions differ from the CI pins — the check would not be "
            "the check CI runs.\n" + "\n".join(bad) + "\n  fix: pip install -r "
            "requirements.lock  (or: pip install -e '.[dev]')"
        )


def lock_pin_drift() -> list[str]:
    """Pyproject pins that requirements.lock does not carry verbatim.

    CI installs from the lock, so the lock is what actually runs. `pip check`
    only inspects what is already installed and never looks at the `dev`
    extra at all, which let a drifted pytest/ruff/mypy pin pass CI while this
    runner refused it locally — the two were checking different closures.
    """
    runtime, dev = project_pins()
    text = (ROOT / LOCK).read_text(encoding="utf-8")
    drifted: list[str] = []
    for name, version in sorted({**runtime, **dev}.items()):
        pattern = rf"(?mi)^{re.escape(name)}=={re.escape(version)}(?=[\s\\]|$)"
        if not re.search(pattern, text):
            drifted.append(f"{name}=={version}")
    return drifted


def run(label: str, *args: str) -> bool:
    print(f"== {label}: {' '.join(args)}", flush=True)
    proc = subprocess.run([sys.executable, "-m", *args], cwd=ROOT)
    return proc.returncode == 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gates.py",
        description="Run the CI gates with the pinned tool versions, or refuse.",
    )
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="run the static gates only (skip pytest)",
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    pins = pinned_versions()
    check_versions(pins)
    print(
        "gates: tool versions match the CI pins ("
        + ", ".join(f"{t} {pins[t]}" for t in TOOLS)
        + ")"
    )

    ok = True
    print(f"== lock pins: every pyproject pin appears in {LOCK}", flush=True)
    drifted = lock_pin_drift()
    if drifted:
        print(f"   NOT in {LOCK}: {', '.join(drifted)} — regenerate the lock", flush=True)
        ok = False
    ok &= run("pip check", "pip", "check")
    ok &= run("ruff check", "ruff", "check", *LINT_PATHS)
    ok &= run("ruff format --check", "ruff", "format", "--check", *LINT_PATHS)
    ok &= run("mypy", "mypy", "src")
    if not args.no_tests:
        ok &= run("pytest", "pytest", "-q")
    print("gates: " + ("ALL GREEN" if ok else "RED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

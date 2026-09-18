"""Seeded mutants — the panel's credential, authored by code, never by a model.

A verdict that only ever says PASS is indistinguishable from a panel that
cannot fail. This module manufactures the one thing that tells them apart: a
COPY of the rendered artifact carrying exactly ONE deterministic defect of a
class the panel is instructed to catch, dispatched as one extra call
(`verify:mutant1`) whose prompt is byte-identical to the real angle's. If the
angle refutes the mutant, the panel caught a seeded defect; if it does not,
the run says so out loud.

Three rules hold the design honest, all three from candidate C15 of the
clean-sheet run's first design synthesis (kept in
tasks/20260916-clean-sheet/arc-state.failed-20260916T190608Z.json):

  - **The maker never authors its own credential.** Seeding is pure Python
    here — no model call decides what the defect is, so nothing the maker
    wrote can flatter the result.
  - **A caught mutant is a floor-raiser, never a certificate.** It says the
    panel caught a SEEDED defect of a SEARCHED class. It says nothing about
    the defects nobody seeded. Every surface writes "caught", never
    "verified" (attestation rot: a field that can be filled by asserting will
    be filled by asserting).
  - **The credential NEVER gates.** `mutant-missed` is a fact on the record,
    not a veto — turning it into one would rebuild the false-veto failure the
    fatal-only rule was adopted to remove.

Exactly one operator fires per run, priority M1 > M2 > M3, so "caught" is
unambiguous:

| op | class                | mutation                              | angle |
|----|----------------------|---------------------------------------|-------|
| M1 | citation-path        | break a cited path that really exists | 1 GREP-ANCHOR |
| M2 | deleted-disclosure   | delete the first Open findings bullet | 2 CONFIRM |
| M3 | load-bearing-number  | shift the first body quantity         | 2 CONFIRM |

M1 leads because its catch condition is the one the panel is literally
instructed to perform ("grep/read and confirm"); M3 is last because an uncited
number has the weakest expected catch. Operators act on the BODY only — the
frontmatter is schema-validated, and mutating it would measure a parse error
instead of a reading.

The cadence is DERIVED, never stored: `verdict_population()` counts committed
`tasks/*/verification.json` files, so the count survives a session restart
without a ledger file of its own. Every read here is fail-open — a missing or
corrupt tree returns zero, and a run is never blocked by its own control.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import SeededDefect

__all__ = [
    "MutantError",
    "NO_OPERATOR_NOTE",
    "PAST_WINDOW_NOTE",
    "apply",
    "catch_rate",
    "due",
    "seed",
    "verdict_population",
    "within_payload",
]


class MutantError(ValueError):
    """A recorded defect no longer matches the artifact it was seeded from."""


# Why a schedule degraded instead of measuring. Recorded on MutantRun.note.
NO_OPERATOR_NOTE = "no operator applicable"
PAST_WINDOW_NOTE = "seeded defect falls past the panel payload window"

# SeededDefect.original / .mutated are capped at 200 chars (models.py): a span
# longer than that cannot be recorded, so the operator declines rather than
# recording a truncated substitution `apply()` could not reproduce.
_MAX_SPAN = 200

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

_MUTANT_SUFFIX = "-chimera-mutant"

# M1: a backticked repo path, optionally anchored (`path.py:81`, `path.py:8-12`).
_PATH_SPAN_RE = re.compile(
    r"`([A-Za-z0-9_./-]+\.(?:py|md|json|toml|lock|ya?ml|sh|ts|js)(?::\d+(?:-\d+)?)?)`"
)

# M2: the disclosure block every wrap artifact is asked to carry.
_OPEN_FINDINGS_RE = re.compile(r"(?im)^#{1,6}[ \t]+open findings[ \t]*$")
_BULLET_RE = re.compile(r"^[-*][ \t]+\S")

# M3: a bare quantity — not a version, not a ratio, not a percentage.
_NUMBER_RE = re.compile(r"(?<![\w.:-])\d{1,6}(?![\w.:%-])")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")

_EXPECTED_CATCH = {
    "path-anchor": (
        "the cited path cannot be grepped or read — GREP-ANCHOR must refute "
        "the artifact for an unsubstantiated citation"
    ),
    "open-finding": (
        "a disclosure the artifact used to carry is gone — the executing "
        "angle must refute on MISSING-SOURCE / the disclosure rule"
    ),
    "quantity": (
        "a number the artifact calls measured no longer matches its source — "
        "the executing angle must re-measure it and refute"
    ),
}


def _body_offset(rendered: str) -> int:
    """Where the body starts: past the frontmatter block, or 0 if there is none."""
    match = _FRONTMATTER_RE.match(rendered)
    return match.end() if match else 0


def _defect(
    operator: str,
    defect_class: str,
    offset: int,
    original: str,
    mutated: str,
    angle: int,
) -> SeededDefect | None:
    if not original or len(original) > _MAX_SPAN or len(mutated) > _MAX_SPAN:
        return None
    return SeededDefect(
        operator=operator,  # type: ignore[arg-type]
        defect_class=defect_class,
        offset=offset,
        original=original,
        mutated=mutated,
        expected_catch=_EXPECTED_CATCH[operator],
        angle=angle,
    )


def _m1_path_anchor(body: str, base: int, repo_root: Path) -> SeededDefect | None:
    """Break the first cited path that ACTUALLY EXISTS.

    A path that was already unfindable measures nothing: the panel would
    refute the artifact for a defect chimera did not seed, and the run would
    score a free "caught"."""
    for match in _PATH_SPAN_RE.finditer(body):
        inner = match.group(1)
        path_part = inner.split(":", 1)[0]
        if path_part.startswith("/") or ".." in Path(path_part).parts:
            continue  # only repo-relative citations; never walk out of the tree
        try:
            if not (repo_root / path_part).exists():
                continue
        except OSError:
            continue
        stem, _, extension = path_part.rpartition(".")
        mutated_path = f"{stem}{_MUTANT_SUFFIX}.{extension}"
        defect = _defect(
            "path-anchor",
            "citation-path",
            base + match.start(),
            match.group(0),
            f"`{inner.replace(path_part, mutated_path, 1)}`",
            1,
        )
        if defect is not None:
            return defect
    return None


def _line_spans(body: str) -> list[tuple[int, str]]:
    """(offset, line) for every line of *body*, newline excluded."""
    spans: list[tuple[int, str]] = []
    offset = 0
    for line in body.splitlines(keepends=True):
        spans.append((offset, line.rstrip("\n")))
        offset += len(line)
    return spans


def _m2_open_finding(body: str, base: int) -> SeededDefect | None:
    """Delete the first bullet under an `Open findings` heading, continuation
    lines included — the artifact loses a gap it had disclosed."""
    heading = _OPEN_FINDINGS_RE.search(body)
    if heading is None:
        return None
    spans = _line_spans(body)
    start_index = next(
        (i for i, (offset, _) in enumerate(spans) if offset >= heading.end()),
        len(spans),
    )
    index = start_index
    while index < len(spans) and not spans[index][1].strip():
        index += 1
    if index >= len(spans) or not _BULLET_RE.match(spans[index][1]):
        return None  # "(none)", prose, or an empty block — nothing disclosed to delete
    bullet_start = spans[index][0]
    end = index + 1
    while end < len(spans):
        text = spans[end][1]
        if not text.strip() or _BULLET_RE.match(text) or text.lstrip().startswith("#"):
            break
        end += 1
    stop = spans[end][0] if end < len(spans) else len(body)
    return _defect(
        "open-finding",
        "deleted-disclosure",
        base + bullet_start,
        body[bullet_start:stop],
        "",
        2,
    )


def _m3_quantity(body: str, base: int) -> SeededDefect | None:
    """Shift the first body quantity to a value that cannot be a rounding:
    `n * 7 + 1` never collides with the original and is never plausible."""
    fenced = False
    for offset, line in _line_spans(body):
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith("#"):
            continue
        for match in _NUMBER_RE.finditer(line):
            value = int(match.group(0))
            if value < 2:
                continue  # 0/1 read as boolean-ish; shifting them teaches nothing
            defect = _defect(
                "quantity",
                "load-bearing-number",
                base + offset + match.start(),
                match.group(0),
                str(value * 7 + 1),
                2,
            )
            if defect is not None:
                return defect
    return None


def seed(rendered: str, repo_root: Path) -> SeededDefect | None:
    """The first applicable operator's defect, or None when none applies.

    *rendered* is the artifact exactly as the panel would receive it
    (frontmatter + body); *repo_root* is the tree M1 resolves citations
    against. Deterministic and side-effect free — the same inputs always
    produce the same defect, and nothing is written anywhere."""
    base = _body_offset(rendered)
    body = rendered[base:]
    return (
        _m1_path_anchor(body, base, Path(repo_root))
        or _m2_open_finding(body, base)
        or _m3_quantity(body, base)
    )


def apply(defect: SeededDefect, rendered: str) -> str:
    """Re-apply a RECORDED defect — substitution by offset, never a re-search.

    Reproducible from state on every `pending_calls()`, and loud when the
    artifact underneath has changed: a silent no-op would dispatch a control
    against a clean artifact and score a MISS nothing went wrong in."""
    end = defect.offset + len(defect.original)
    if rendered[defect.offset : end] != defect.original:
        raise MutantError(
            f"recorded {defect.operator} defect no longer matches the artifact at "
            f"offset {defect.offset}"
        )
    return rendered[: defect.offset] + defect.mutated + rendered[end:]


def within_payload(defect: SeededDefect | None, payload_max: int) -> bool:
    """Would the panel actually SEE this defect?

    The verify payload is truncated at a cap (arcs/graph.VERIFY_PAYLOAD_MAX);
    a defect past it scores a guaranteed miss, which is not a measurement."""
    if defect is None:
        return False
    return defect.offset + len(defect.original) <= payload_max


# ---------------------------------------------------------------------------
# Cadence + aggregate — both DERIVED from committed records
# ---------------------------------------------------------------------------


def _verdicts(tasks_root: Path) -> list[Path]:
    try:
        return sorted(Path(tasks_root).glob("*/verification.json"))
    except OSError:
        return []


def verdict_population(tasks_root: Path) -> int:
    """How many verdicts this queue has committed. The cadence counter, with
    no counter file: `queue.record_verification` already writes these, so the
    number is durable across sessions by construction."""
    return len(_verdicts(tasks_root))


def due(tasks_root: Path, cadence: int) -> bool:
    """Is the NEXT verdict a credentialed one? True on every `cadence`-th."""
    if cadence < 1:
        return False
    return (verdict_population(tasks_root) + 1) % cadence == 0


def catch_rate(tasks_root: Path) -> tuple[int, int]:
    """(caught, measured) across every committed verdict.

    `mutant-not-measured` and `panel-uncredentialed` records are excluded from
    BOTH halves — a control that never ran is not evidence in either
    direction. Unreadable records are skipped, never counted."""
    caught = measured = 0
    for path in _verdicts(tasks_root):
        try:
            credential = json.loads(path.read_text(encoding="utf-8")).get("panel_credential")
        except (OSError, ValueError, AttributeError):
            continue
        if credential == "mutant-caught":
            caught += 1
            measured += 1
        elif credential == "mutant-missed":
            measured += 1
    return caught, measured

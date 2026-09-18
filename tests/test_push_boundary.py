"""Tracked regression guard for Security Rule #1 (push boundary).

Work-zone directories (overlay/, projects/, wip/, scratch/, data/,
resume-mine-output/, flows/*/output/) must never appear in the git index.
`tasks/` is the one directory that must: the queue is git-durable, so every
transition commits a task record to the PRIVATE origin. The second tripwire
below governs what those tracked files may be, not whether they exist.
Protection is structural — gitignored zones + a local untracked hook — but
this test gives the public framework a committed tripwire: if a refactor or
mis-merge ever tracks a file from a work zone, the suite fails immediately.

This file contains no work-domain tokens.  It is pure framework code.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from chimera.arcs._common import ARC_STATE_FILE
from chimera.arcs.graph import ARTIFACT_FILE, MUTANT_RECORD_DIR
from chimera.models import TaskRecord, TaskSpec
from chimera.queue import Queue


def _find_repo_root() -> Path:
    """Walk up from this file until we find a directory containing pyproject.toml.

    Works whether pytest is run from the repo root, the worktree root, or a
    subdirectory.  Raises RuntimeError if the sentinel is not found.
    """
    candidate = Path(__file__).resolve().parent
    for _ in range(10):  # cap the walk; real monorepos are never 10 levels deep
        if (candidate / "pyproject.toml").is_file():
            return candidate
        candidate = candidate.parent
    raise RuntimeError(
        f"Could not locate pyproject.toml walking up from {Path(__file__).resolve()}"
    )


# Work-zone path prefixes (relative to repo root) that must never be tracked.
_WORK_ZONE_PREFIXES: tuple[str, ...] = (
    "overlay/",
    "projects/",
    "wip/",
    "scratch/",
    "data/",
    "resume-mine-output/",
)

# Explicit path-filter expressions for git ls-files (pattern form for glob paths).
# flows/*/output/ has a wildcard, so we match it by prefix inspection after the fact.
_FLOWS_OUTPUT_PREFIX = "flows/"
_FLOWS_OUTPUT_SUFFIX = "/output/"


def _ls_files(repo_root: Path, *pathspecs: str) -> list[str]:
    """Return tracked filenames matching the given pathspecs (shell=False).

    `check=False` plus an explicit assertion on purpose: run outside a git
    checkout (an sdist, a vendored copy) `git ls-files` exits non-zero, and
    `check=True` turned that into a raw CalledProcessError with no hint of what
    the tripwire needed. It must never be skipped either — a silently skipped
    boundary test is the failure mode this file exists to prevent — so a
    missing checkout is a failure with a sentence that says why.
    """
    proc = subprocess.run(
        ["git", "ls-files", "--", *pathspecs],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    assert proc.returncode == 0, "push-boundary tripwire needs a git checkout"
    return [line for line in proc.stdout.splitlines() if line.strip()]


def test_work_zones_contain_no_tracked_files() -> None:
    """No file from any work-zone directory may appear in the git index."""
    repo_root = _find_repo_root()

    # Query git for every prefix-based work zone in a single call.
    direct_hits = _ls_files(repo_root, *_WORK_ZONE_PREFIXES)

    # Query flows/ separately so we can filter to flows/*/output/ paths only.
    flows_candidates = _ls_files(repo_root, "flows/")
    flows_output_hits = [
        f
        for f in flows_candidates
        if f.startswith(_FLOWS_OUTPUT_PREFIX)
        and _FLOWS_OUTPUT_SUFFIX in f[len(_FLOWS_OUTPUT_PREFIX) :]
    ]

    all_hits = direct_hits + flows_output_hits

    assert not all_hits, (
        "Security Rule #1 violation — the following work-zone files are tracked by git "
        "and would reach the public remote on push:\n" + "\n".join(f"  {h}" for h in all_hits)
    )


# ---------------------------------------------------------------------------
# tasks/ — tracked ENGINE STATE, and nothing else
# ---------------------------------------------------------------------------
# The names below are derived from the code that writes them wherever a
# constant exists; where the writer inlines a literal, the literal is repeated
# here with the writer named. A new file under tasks/<id>/ is a deliberate
# change to this list, in the same commit as its writer.

_PATHS = Queue(root=Path("/"))  # path helpers only — Queue does no IO on init
_TASK_RECORD_FILE = _PATHS.task_path("_").name  # queue.Queue.task_path
_VERIFICATION_FILE = _PATHS.verification_path("_").name  # queue.Queue.verification_path
_ARTIFACTS_DIR = _PATHS.artifacts_dir("_").name  # queue.Queue.artifacts_dir

# Written directly under tasks/<id>/.
_TASK_DIR_FILES: frozenset[str] = frozenset(
    {
        _TASK_RECORD_FILE,  # queue.Queue._write (every transition)
        _VERIFICATION_FILE,  # queue.Queue.record_verification / GraphArc._submit_verify
        ARC_STATE_FILE,  # arcs.graph.GraphArc.save
    }
)

# `chimera retry` keeps the failed arc state beside the fresh one:
# cli.cmd_retry writes f"arc-state.failed-{stamp}.json", stamp =
# models.utcnow_iso() with its '-' and ':' separators stripped.
_PRESERVED_ARC_STATE_RE = re.compile(
    rf"^{re.escape(Path(ARC_STATE_FILE).stem)}\.failed-\d{{8}}T\d{{6}}Z\.json$"
)

# Written under tasks/<id>/mutants/ — one record per seeded-mutant control
# dispatch (arcs.graph.GraphArc._write_mutant_record, C15). Engine state like
# the verdict beside it: a status, the defect chimera itself seeded, and the
# control's opinion. Numbered like arc_memory's run summaries, so a retry
# never clobbers what the previous attempt measured.
_MUTANT_RECORD_DIR = MUTANT_RECORD_DIR
_MUTANT_RECORD_RE = re.compile(r"^run-\d+\.json$")

# Written under tasks/<id>/artifacts/.
_ARTIFACT_FILES: frozenset[str] = frozenset(
    {
        ARTIFACT_FILE,  # arcs.graph.GraphArc._submit_wrap ("graph-output.md")
        "findings.md",  # cli.cmd_arc_submit's ARTIFACT_FILENAME fallback for an arc without one
        "result.json",  # legacy v6 research artifact, read by cli._capture_memory_row
    }
)

# Every engine-state file EXCEPT the arc state is small and stays small: a
# task record, a verification verdict, one rendered artifact. The push-scope
# advisor only WARNS at 500 KB; this is the tighter
# hard bound, so a dataset parked under tasks/ fails the suite instead of
# printing a warning nobody reads (audit 2026-09-02, P0-2 — a ~570 KB dataset).
_MAX_TRACKED_BLOB_BYTES = 256 * 1024


# The arc state is the one file no byte bound may govern: it accumulates every
# node's output, so it grows with the graph. Measured on the first live run,
# 20 KB after planning and 79 KB three nodes into a seven-node DAG — and the
# levers allow width 8 x phases 10. A fixed ceiling on it would be a second
# tripwire contradicting the engine, which is the bug this file just fixed.
# What keeps a dataset from wearing its name is the other two rules: it must
# sit beside a loadable task.json, under exactly this name.
def _is_arc_state(basename: str) -> bool:
    return basename == ARC_STATE_FILE or bool(_PRESERVED_ARC_STATE_RE.match(basename))


RULE_NO_RECORD = "no task.json in its task directory — orphan data, not queue state"
RULE_BAD_RECORD = "task.json does not load as a TaskRecord"
RULE_UNKNOWN_FILE = "not a path the CLI writes under tasks/"
RULE_TOO_LARGE = f"tracked blob larger than {_MAX_TRACKED_BLOB_BYTES // 1024} KB"


def _blob_size(repo_root: Path, rel: str) -> int:
    """Size of the STAGED blob for `rel`.

    `:<path>` is the index entry `git ls-files` just listed. The working-tree
    copy is deliberately not consulted: a file committed fat and truncated on
    disk is still what a push carries (the same lesson as the push-scope
    advisor's HEAD-blob sizing).
    """
    proc = subprocess.run(
        ["git", "cat-file", "-s", f":{rel}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    assert proc.returncode == 0, f"cannot size the tracked blob for {rel}"
    return int(proc.stdout.strip())


def _blob_bytes(repo_root: Path, rel: str) -> bytes | None:
    """The staged blob's content, or None if git cannot produce it.

    Bytes, not text: a binary blob under tasks/ must be REPORTED by this
    tripwire, never crash it on a decode.
    """
    proc = subprocess.run(
        ["git", "show", f":{rel}"],
        cwd=str(repo_root),
        capture_output=True,
        check=False,
        shell=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def _engine_state_violations(repo_root: Path) -> list[tuple[str, str]]:
    """Judge every tracked path under `tasks/` in `repo_root`; return (path, rule).

    Factored out of the tripwire so the negative test can prove the check
    FIRES, against a throwaway index instead of the real one.
    """
    tracked_set = set(_ls_files(repo_root, "tasks/"))
    violations: list[tuple[str, str]] = []

    for rel in sorted(tracked_set):
        parts = PurePosixPath(rel).parts
        # tasks/<id>/<file> or tasks/<id>/artifacts/<file>, and nothing else.
        if len(parts) == 3:
            known = parts[2] in _TASK_DIR_FILES or _is_arc_state(parts[2])
        elif len(parts) == 4 and parts[2] == _ARTIFACTS_DIR:
            known = parts[3] in _ARTIFACT_FILES
        elif len(parts) == 4 and parts[2] == _MUTANT_RECORD_DIR:
            known = bool(_MUTANT_RECORD_RE.match(parts[3]))
        else:
            known = False
        if not known:
            violations.append((rel, RULE_UNKNOWN_FILE))

        if len(parts) < 3:
            continue  # not inside a task directory at all; already flagged
        if f"tasks/{parts[1]}/{_TASK_RECORD_FILE}" not in tracked_set:
            violations.append((rel, RULE_NO_RECORD))
        if not _is_arc_state(parts[-1]) and _blob_size(repo_root, rel) > _MAX_TRACKED_BLOB_BYTES:
            violations.append((rel, RULE_TOO_LARGE))

        if len(parts) == 3 and parts[2] == _TASK_RECORD_FILE:
            raw = _blob_bytes(repo_root, rel)
            if raw is None:
                violations.append((rel, RULE_BAD_RECORD))
                continue
            try:
                TaskRecord.model_validate_json(raw.decode("utf-8", errors="replace"))
            except ValidationError:
                violations.append((rel, RULE_BAD_RECORD))
    return violations


def test_tracked_task_records_are_engine_state_only() -> None:
    """`tasks/` IS tracked, by design; what it may hold is the invariant.

    The queue is git-durable — every transition is a commit (CLAUDE.md
    Behavioral Rule 1, TICK_PROTOCOL.md, `queue.Queue.transition` ->
    `gitio.commit`) — so task records and arc state ride the PRIVATE origin as
    engine state. A tripwire demanding an empty `tasks/` index would go red on
    the first real task and stop the dispatcher's preflight dead; the boundary
    is about the CONTENT of those files, not their tracked-ness.

    Two other mechanisms own that content: G1 intake keeps the ask
    framework-only (work aimed at another repo runs in THAT repo's checkout —
    CLAUDE.md Security Rule 1, TICK_PROTOCOL's boundary rule), and the public
    mirror is sanitized at sync time by the `mirror-audit` skill, not here.

    What this tripwire pins is the remaining leg: the index holds ENGINE STATE
    and nothing else. Every tracked path under `tasks/` must (a) live in a task
    directory whose `task.json` loads as a `TaskRecord`, (b) carry a basename
    the CLI actually writes, and (c) be small — the arc state excepted, since
    it grows with the graph it records. That is the shape of what the
    2026-09-02 audit actually found (P0-2): an orphan directory with no task
    record holding a ~570 KB dataset — data, not queue state.
    """
    violations = _engine_state_violations(_find_repo_root())

    assert not violations, (
        "tasks/ is tracked engine state and must hold nothing else — these "
        "tracked paths break that:\n" + "\n".join(f"  {path}: {rule}" for path, rule in violations)
    )


def test_engine_state_check_reports_orphans_bloat_and_bad_records(repo: Path) -> None:
    """The check FIRES: four plants and a clean control, in a throwaway index.

    The positive tripwire is green by construction on a healthy tree, so it
    cannot show the check works. This one stages the failure modes the audit
    found — an orphan data drop with no task record, an oversized artifact, a
    task record that no longer validates — in the hermetic `repo` fixture, and
    never touches the real index. The control carries a 400 KB arc state: the
    one file the size rule must let through.
    """

    def plant(rel: str, body: str) -> None:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def record(task_id: str) -> str:
        spec = TaskSpec(id=task_id, slug=task_id.split("-", 1)[1], ask="fixture", arc="graph")
        return TaskRecord(spec=spec, state="ready").model_dump_json(indent=2)

    # (1) orphan data drop: no task.json, and a basename the CLI never writes
    plant("tasks/20260101-orphan/data.csv", "col_a,col_b\n1,2\n")
    # (2) a real record whose artifact is far too big for engine state
    plant("tasks/20260102-bloat/task.json", record("20260102-bloat"))
    plant(f"tasks/20260102-bloat/{_ARTIFACTS_DIR}/{ARTIFACT_FILE}", "x" * (300 * 1024))
    # (3) a task record that does not load
    plant("tasks/20260103-corrupt/task.json", '{"spec": {"id": "20260103-corrupt"}}\n')
    # (4) the control: a healthy task directory, mid-run, with the fat arc
    #     state a real graph produces — engine state the size rule must not
    #     touch, or this tripwire repeats the bug it replaced
    plant("tasks/20260104-clean/task.json", record("20260104-clean"))
    plant(f"tasks/20260104-clean/{ARC_STATE_FILE}", "{}" + " " * (400 * 1024))
    plant("tasks/20260104-clean/arc-state.failed-20260101T000000Z.json", "{}\n")
    plant(f"tasks/20260104-clean/{_ARTIFACTS_DIR}/{ARTIFACT_FILE}", "# findings\n")
    plant(f"tasks/20260104-clean/{_MUTANT_RECORD_DIR}/run-1.json", '{"status": "caught"}\n')
    # (5) a stray file in the control's own directory is still unknown state
    plant(f"tasks/20260105-strayrun/{_TASK_RECORD_FILE}", record("20260105-strayrun"))
    plant(f"tasks/20260105-strayrun/{_MUTANT_RECORD_DIR}/notes.md", "free text\n")

    subprocess.run(
        ["git", "add", "-A"], cwd=str(repo), capture_output=True, check=True, shell=False
    )
    found = set(_engine_state_violations(repo))

    assert ("tasks/20260101-orphan/data.csv", RULE_NO_RECORD) in found
    assert ("tasks/20260101-orphan/data.csv", RULE_UNKNOWN_FILE) in found
    assert (
        f"tasks/20260102-bloat/{_ARTIFACTS_DIR}/{ARTIFACT_FILE}",
        RULE_TOO_LARGE,
    ) in found
    assert ("tasks/20260103-corrupt/task.json", RULE_BAD_RECORD) in found

    assert (
        f"tasks/20260105-strayrun/{_MUTANT_RECORD_DIR}/notes.md",
        RULE_UNKNOWN_FILE,
    ) in found

    clean = sorted(path for path, _ in found if path.startswith("tasks/20260104-clean/"))
    assert not clean, f"a healthy task directory must not trip the check: {clean}"


def test_the_mutant_record_the_arc_writes_is_the_path_the_allow_list_admits(tmp_path) -> None:
    """The allow-list above is derived from the WRITER, not guessed at.

    `_write_mutant_record` is the only producer of this path; if it ever
    writes a different shape, this fails here instead of surfacing as a red
    engine-state tripwire on the operator's next real run."""
    from chimera.arcs.graph import GraphArc
    from chimera.models import MutantRun

    task_dir = tmp_path / "tasks" / "20260916-shape"
    task_dir.mkdir(parents=True)
    arc = GraphArc(task_dir)
    state = arc._fresh_state("20260916-shape", "shape", "ask", None)
    state.mutant = MutantRun(status="caught")

    arc._write_mutant_record(state)
    arc._write_mutant_record(state)  # a second dispatch never clobbers the first

    written = sorted(p.relative_to(tmp_path).as_posix() for p in task_dir.rglob("*") if p.is_file())
    records = [p for p in written if "/mutants/" in p]
    assert records == [
        "tasks/20260916-shape/mutants/run-1.json",
        "tasks/20260916-shape/mutants/run-2.json",
    ]
    for rel in records:
        parts = PurePosixPath(rel).parts
        assert len(parts) == 4 and parts[2] == _MUTANT_RECORD_DIR
        assert _MUTANT_RECORD_RE.match(parts[3]), f"{rel} is not the admitted shape"


def test_gitignore_contains_flows_output_rule() -> None:
    """Sanity check: .gitignore must declare the flows/*/output/ ignore rule.

    If someone removes or renames the rule, this test surfaces the gap before
    any output files can accidentally land in the index.
    """
    repo_root = _find_repo_root()
    gitignore = repo_root / ".gitignore"

    assert gitignore.is_file(), ".gitignore must exist at the repo root"

    content = gitignore.read_text(encoding="utf-8")

    # Accept either the exact pattern or a broader superset (e.g. "flows/").
    has_rule = "flows/*/output/" in content or "flows/" in content
    assert has_rule, (
        ".gitignore is missing a rule covering flows/*/output/ — "
        "arc output could be accidentally tracked and pushed"
    )

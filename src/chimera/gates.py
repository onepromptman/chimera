"""The one blocking human gate (G2).

G1 — intake: `chimera new` opens the task directly, single-path. An
     ambiguous ask is clarified in conversation BEFORE the call — there is
     no async ask/park branch (D1, handoff item 3: zero observed uses in
     550+ commits) and no parallel-ab outcome to route to either (unreachable
     from the CLI once `--outcome` was removed with it).

G2 — final sign-off: `/approve` on the task's Issue (or `chimera approve`).
     Approval is recorded, then queue.transition() runs the verify gate on
     the way to done. Everything between G1 and G2 is async digest.

Work-content boundary: the public framework carries no per-task sensitivity
policy. The structural protection against leaking work content into the
public remote is the gitignored work zones plus an optional, personal,
UNTRACKED pre-push guard (`~/.config/chimera/pre-push-guard.py`, wired via
.git/hooks/pre-push where installed — deliberately not part of the framework;
see CLAUDE.md Security Rule 1), not a runtime gate.

G2 threat model: the adversary is the worker session itself, which runs the
same CLI as the operator and owns the working tree. So G2 reads the
COMMITTED digest (`git show HEAD:.g2-operator.sha256`), not the working
tree — deleting or rewriting the file on disk no longer turns enforcement
off or swaps in an attacker's token, and any real change to the digest costs
a commit that stands in the log. The working-tree file is honoured only
before its own first commit (`g2-token init`, which commits it immediately);
a rotated-but-uncommitted digest is not trusted. The token itself never
enters the repo: `init_operator_token` refuses a --token-path inside it.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from . import gitio
from .models import (
    ArcName,
    GraphShape,
    TaskRecord,
    TaskSpec,
)
from .queue import Queue, QueueError, slugify


class OperatorGateError(QueueError):
    """G2 verb attempted without the operator token (or with a wrong one)."""


# ---------------------------------------------------------------------------
# G2 operator token (audit 2026-09-02, Q6 — "token now, PR review later")
# ---------------------------------------------------------------------------
# The worker session and the operator run the same CLI, so `chimera approve`
# was procedurally, not technically, the operator's. Now a G2 verb requires a
# token whose SHA-256 digest is COMMITTED at OPERATOR_DIGEST_FILE — and read
# back from the committed tree, never from the working copy. The token
# itself lives only on the operator's machine (env var or ~/.config file) —
# a cloud worker cannot forge it, and the digest is not a credential (Rule 2:
# nothing in the tree lets anyone derive the token). A repo with no digest
# file runs in "unprotected" mode, surfaced by `chimera status` — the framework
# repo itself carries the digest.

OPERATOR_DIGEST_FILE = ".g2-operator.sha256"
OPERATOR_TOKEN_ENV = "CHIMERA_OPERATOR_TOKEN"
OPERATOR_TOKEN_DEFAULT_PATH = Path.home() / ".config" / "chimera" / "operator-token"


def operator_digest_path(root: Path) -> Path:
    return Path(root) / OPERATOR_DIGEST_FILE


def operator_digest(root: Path) -> str | None:
    """The expected digest, read from the COMMITTED tree — never the working
    tree, except before the digest's own first commit.

    The worker session writes the working tree on every transition, so a
    working-tree read made the gate self-served: `rm .g2-operator.sha256`
    turned enforcement off, and overwriting the file with the digest of an
    attacker-chosen token made that token the operator's. Neither is staged
    by `gitio.commit` (it stages only the task dir), so restoring the file
    afterwards left no trace. `git show HEAD:<digest>` is the operator's
    signature: changing it costs a commit that stands in the log.

    The working-tree file is consulted ONLY when HEAD carries no such blob —
    a fresh `chimera g2-token init` between writing the digest and committing
    it. A rotated-but-uncommitted digest is therefore NOT trusted: the
    committed one still governs until `g2-token init` commits the new one.

    Returns None only when there is no digest at all (unprotected repo). An
    empty digest is kept, not discarded: it matches no token, so a truncated
    file fails closed.
    """
    text = gitio.show_at_head(Path(root), OPERATOR_DIGEST_FILE)
    if text is None:
        try:
            text = operator_digest_path(root).read_text(encoding="utf-8")
        except OSError:
            return None
    return text.strip()


def g2_enforcement(root: Path) -> str:
    """'token' when a digest exists in the committed tree, else 'none'."""
    return "token" if operator_digest(root) is not None else "none"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def read_operator_token(explicit: str | None = None, token_path: Path | None = None) -> str | None:
    """Explicit argument, then $CHIMERA_OPERATOR_TOKEN, then the token file."""
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get(OPERATOR_TOKEN_ENV, "").strip()
    if env:
        return env
    path = token_path or OPERATOR_TOKEN_DEFAULT_PATH
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def verify_operator(root: Path, token: str | None = None, token_path: Path | None = None) -> str:
    """Enforce the G2 token when the repo carries a digest. Returns the
    enforcement mode ('token' | 'none'). Raises OperatorGateError otherwise.

    The expected digest comes from the COMMITTED tree (see operator_digest):
    the working-tree copy is writable by the session being gated."""
    expected = operator_digest(root)
    if expected is None:
        return "none"
    supplied = read_operator_token(token, token_path)
    if not supplied:
        raise OperatorGateError(
            "G2 requires the operator token: set CHIMERA_OPERATOR_TOKEN or "
            f"{OPERATOR_TOKEN_DEFAULT_PATH} on the operator's machine "
            "(`chimera g2-token init` creates both). Worker sessions never hold it."
        )
    if not hmac.compare_digest(_digest(supplied), expected):
        raise OperatorGateError(
            "operator token does not match the committed digest "
            f"({OPERATOR_DIGEST_FILE}) — rotate with `chimera g2-token init --rotate` "
            "on the operator's machine"
        )
    return "token"


def _new_token() -> str:
    """A fresh operator token that always starts with a letter or digit.

    `secrets.token_urlsafe` draws from the URL-safe base64 alphabet, so one
    token in 64 begins with ``-``; argparse then reads ``--token -abc…`` as a
    flag missing its value and `approve` exits 2. Redrawing costs nothing and
    keeps every token usable by flag, env var and file alike.
    """
    while True:
        token = secrets.token_urlsafe(32)
        if token[0].isalnum():
            return token


def init_operator_token(
    root: Path, *, rotate: bool = False, token_path: Path | None = None
) -> dict:
    """Generate a fresh token, store it (0600) outside the repo, write its
    digest into the repo. Refuses to overwrite an existing digest unless
    *rotate*. Returns a summary with the token MASKED (last 4 chars)."""
    digest_path = operator_digest_path(root)
    if digest_path.exists() and not rotate:
        raise OperatorGateError(
            f"{OPERATOR_DIGEST_FILE} already exists — pass --rotate to replace it"
        )
    path = (token_path or OPERATOR_TOKEN_DEFAULT_PATH).expanduser()
    # A --token-path inside the checkout would write the PLAINTEXT token into
    # the tree, where the next `git add -A` commits it (Security Rule 2).
    # Refuse before anything is written, so the promise the CLI prints —
    # "token stored OUTSIDE the repo" — is one the code keeps.
    if path.resolve().is_relative_to(Path(root).resolve()):
        raise OperatorGateError(
            f"token path {path} is inside the repo ({root}): the token would be "
            "committed in plaintext. Store it outside the checkout "
            f"(default: {OPERATOR_TOKEN_DEFAULT_PATH})."
        )
    token = _new_token()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # drvfs / Windows: chmod is a no-op there (CLAUDE.md WSL note)
    digest_path.write_text(_digest(token) + "\n", encoding="utf-8")
    return {
        "token_path": str(path),
        "digest_path": str(digest_path),
        "token_masked": "…" + token[-4:],
        "rotated": rotate,
    }


def _task_id(slug: str) -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%d')}-{slug}"


def g1_intake(
    queue: Queue,
    ask: str,
    by: str,
    context: str | None = None,
    slug: str | None = None,
    arc: ArcName = "graph",
    shape: GraphShape | None = None,
) -> list[TaskRecord]:
    """Open one task from one intake, single-path. An ambiguous ask is
    disambiguated in conversation before this call — there is no ask/park
    outcome, and no parallel-ab outcome, to route to here (D1/G2, handoff
    item 3: both were unreachable from the CLI once `--outcome` was removed).

    One door (ADR-010): every task is a graph task — the planner node decides
    the shape, so intake carries no per-arc flags. `arc` stays a parameter
    only so tests can exercise the retired-arc refusal in the dispatcher.
    """
    base_slug = slug or slugify(ask)
    spec = TaskSpec(
        id=_task_id(base_slug),
        slug=base_slug,
        ask=ask,
        arc=arc,
        context=context,
        shape=shape,
    )

    return [queue.create(spec, "ready", by=by)]


# ---------------------------------------------------------------------------
# G2
# ---------------------------------------------------------------------------


def g2_approve(queue: Queue, task_id: str, by: str, token: str | None = None) -> TaskRecord:
    """Final sign-off: operator token, then record approval, then transition
    through the verify gate. The token check comes FIRST so a refused approval
    leaves no partial state."""
    verify_operator(queue.root, token)
    queue.record_approval(task_id, by=by)
    return queue.transition(task_id, "done", by=by, note="G2 approved")


def g2_reject(
    queue: Queue, task_id: str, by: str, note: str, token: str | None = None
) -> TaskRecord:
    verify_operator(queue.root, token)
    return queue.transition(task_id, "running", by=by, note=f"G2 rework: {note}")

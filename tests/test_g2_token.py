"""G2 operator token (audit 2026-09-02, Q6: token now, PR review later).

The worker session and the operator run the same CLI; the token is what
makes `approve`/`reject` the operator's. Digest in the repo, token outside.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from chimera import gates
from chimera.cli import main
from chimera.models import CriticOpinion, TaskSpec, VerifyResult
from chimera.queue import Queue


def _signoff_task(queue: Queue, slug: str = "tok") -> str:
    spec = TaskSpec(id=f"20260902-{slug}", slug=slug, ask="token drill", arc="graph")
    queue.create(spec, "ready", by="t")
    queue.claim(spec.id, "worker")
    queue.transition(spec.id, "awaiting-signoff", by="worker", note="arc complete")
    queue.record_verification(
        spec.id,
        VerifyResult(
            mode="lite",
            passed=True,
            maker_model="opus",
            critic_model="sonnet",
            opinions=[CriticOpinion(refuted=False, reason="ok", severity="cosmetic")] * 3,
            valid_critic_count=3,
            unrefuted_count=3,
        ),
    )
    return spec.id


def _commit_digest(root: Path, message: str = "chimera: G2 operator digest") -> None:
    """What `chimera g2-token init` does through gitio: put the digest in the
    committed tree, which is the only copy G2 trusts."""
    for args in (["add", "--", gates.OPERATOR_DIGEST_FILE], ["commit", "-q", "-m", message]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _run(capsys, *argv, expect_code=0):
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    assert exc.value.code == expect_code
    out = capsys.readouterr().out
    return json.loads(out) if out.lstrip().startswith("{") else out


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv(gates.OPERATOR_TOKEN_ENV, raising=False)
    monkeypatch.setattr(gates, "OPERATOR_TOKEN_DEFAULT_PATH", tmp_path / "no-such-token")


def test_no_digest_means_unprotected_and_status_says_so(queue: Queue, monkeypatch, capsys):
    monkeypatch.chdir(queue.root)
    tid = _signoff_task(queue)
    assert gates.g2_enforcement(queue.root) == "none"
    assert _run(capsys, "status")["g2_enforcement"] == "none"
    assert gates.g2_approve(queue, tid, by="anyone").state == "done"


def test_init_writes_token_outside_repo_and_digest_inside(queue: Queue, tmp_path: Path):
    token_path = tmp_path / "secret" / "operator-token"
    summary = gates.init_operator_token(queue.root, token_path=token_path)
    token = token_path.read_text().strip()
    assert len(token) >= 40
    digest = gates.operator_digest_path(queue.root).read_text().strip()
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps(summary)  # masked output only
    assert summary["token_masked"].endswith(token[-4:])
    with pytest.raises(gates.OperatorGateError):
        gates.init_operator_token(queue.root, token_path=token_path)  # no silent overwrite
    gates.init_operator_token(queue.root, token_path=token_path, rotate=True)
    assert gates.operator_digest_path(queue.root).read_text().strip() != digest


def test_approve_and_reject_refuse_without_or_with_wrong_token(
    queue: Queue, tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(queue.root)
    tid = _signoff_task(queue)
    gates.init_operator_token(queue.root, token_path=tmp_path / "tok")
    assert _run(capsys, "status")["g2_enforcement"] == "token"
    out = _run(capsys, "approve", tid, expect_code=1)
    assert out["ok"] is False and "operator token" in out["error"]
    assert queue.load(tid).state == "awaiting-signoff"  # refused before any write
    assert queue.load(tid).approved_by is None
    monkeypatch.setenv(gates.OPERATOR_TOKEN_ENV, "not-the-token")
    out = _run(capsys, "approve", tid, expect_code=1)
    assert "does not match" in out["error"]
    out = _run(capsys, "reject", tid, "--note", "rework", expect_code=1)
    assert "does not match" in out["error"]
    assert queue.load(tid).state == "awaiting-signoff"


def test_right_token_via_env_file_or_flag_approves(
    queue: Queue, tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(queue.root)
    tid = _signoff_task(queue)
    token_path = tmp_path / "tok"
    gates.init_operator_token(queue.root, token_path=token_path)
    token = token_path.read_text().strip()
    # env
    monkeypatch.setenv(gates.OPERATOR_TOKEN_ENV, token)
    assert _run(capsys, "g2-token", "check")["token_accepted"] is True
    assert _run(capsys, "approve", tid)["state"] == "done"
    # file (default path) on a second task
    monkeypatch.delenv(gates.OPERATOR_TOKEN_ENV)
    monkeypatch.setattr(gates, "OPERATOR_TOKEN_DEFAULT_PATH", token_path)
    tid2 = _signoff_task(queue, "tok2")
    assert _run(capsys, "reject", tid2, "--note", "again")["state"] == "running"
    # explicit flag on a third
    monkeypatch.setattr(gates, "OPERATOR_TOKEN_DEFAULT_PATH", tmp_path / "gone")
    tid3 = _signoff_task(queue, "tok3")
    assert _run(capsys, "approve", tid3, "--token", token)["state"] == "done"


def test_digest_is_not_a_credential(queue: Queue, tmp_path: Path):
    """Rule 2 check: knowing the tracked digest does not let you approve."""
    gates.init_operator_token(queue.root, token_path=tmp_path / "tok")
    digest = gates.operator_digest_path(queue.root).read_text().strip()
    with pytest.raises(gates.OperatorGateError):
        gates.verify_operator(queue.root, token=digest)


def test_generated_token_never_starts_with_a_dash(monkeypatch: pytest.MonkeyPatch):
    """One url-safe token in 64 begins with ``-``; argparse then reads
    ``approve --token -abc…`` as a flag missing its value (CI saw it 1/5 runs).
    The generator redraws until the first char is a letter or digit."""
    draws = iter(["-leading-dash", "_leading-underscore", "Ok9-rest-is-fine"])
    with monkeypatch.context() as scripted:
        scripted.setattr(gates.secrets, "token_urlsafe", lambda n: next(draws))
        assert gates._new_token() == "Ok9-rest-is-fine"
    for _ in range(500):  # the real generator, many draws
        assert gates._new_token()[0].isalnum()


def test_leading_dash_token_is_the_argparse_failure(
    queue: Queue, tmp_path: Path, monkeypatch, capsys
):
    """Documents WHY the generator redraws: a hand-made ``-…`` token still
    fails by bare flag (exit 2) and works by ``--token=`` — the regression
    would be the generator handing that shape out again."""
    monkeypatch.chdir(queue.root)
    token = "-not-a-flag-just-unlucky"
    gates.operator_digest_path(queue.root).write_text(
        hashlib.sha256(token.encode()).hexdigest() + "\n"
    )
    tid = _signoff_task(queue)
    _run(capsys, "approve", tid, "--token", token, expect_code=2)
    assert _run(capsys, "approve", tid, f"--token={token}")["state"] == "done"


# ---------------------------------------------------------------------------
# P1-1: the committed digest is the authority, not the working tree
# ---------------------------------------------------------------------------
# The adversary is the worker session: it runs the same CLI and owns the
# working tree, and `gitio.commit` stages only the task dir, so a working-tree
# edit is invisible in the history it pushes.


def test_deleting_the_working_tree_digest_does_not_disable_g2(
    queue: Queue, tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(queue.root)
    gates.init_operator_token(queue.root, token_path=tmp_path / "tok")
    _commit_digest(queue.root)
    gates.operator_digest_path(queue.root).unlink()

    assert gates.g2_enforcement(queue.root) == "token"
    assert _run(capsys, "status")["g2_enforcement"] == "token"
    tid = _signoff_task(queue)
    out = _run(capsys, "approve", tid, expect_code=1)
    assert out["ok"] is False and "operator token" in out["error"]
    assert queue.load(tid).state == "awaiting-signoff"
    assert queue.load(tid).approved_by is None


def test_overwriting_the_working_tree_digest_does_not_forge_a_token(
    queue: Queue, tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(queue.root)
    token_path = tmp_path / "tok"
    gates.init_operator_token(queue.root, token_path=token_path)
    _commit_digest(queue.root)
    real_token = token_path.read_text().strip()

    attacker = "attacker-chosen-token"
    gates.operator_digest_path(queue.root).write_text(
        hashlib.sha256(attacker.encode()).hexdigest() + "\n", encoding="utf-8"
    )
    tid = _signoff_task(queue)
    out = _run(capsys, "approve", tid, f"--token={attacker}", expect_code=1)
    assert "does not match" in out["error"]
    assert queue.load(tid).state == "awaiting-signoff"
    assert _run(capsys, "approve", tid, f"--token={real_token}")["state"] == "done"


def test_uncommitted_digest_enforces_until_head_carries_one(
    queue: Queue, tmp_path: Path, monkeypatch, capsys
):
    """`g2-token init` writes the digest before it commits it. With no blob at
    HEAD the working-tree file is the only copy there is — and it still gates."""
    monkeypatch.chdir(queue.root)
    gates.init_operator_token(queue.root, token_path=tmp_path / "tok")  # not committed
    assert gates.operator_digest(queue.root) is not None
    assert gates.g2_enforcement(queue.root) == "token"
    tid = _signoff_task(queue)
    out = _run(capsys, "approve", tid, expect_code=1)
    assert "operator token" in out["error"]


def test_a_rotated_digest_is_trusted_only_once_committed(queue: Queue, tmp_path: Path):
    old_path, new_path = tmp_path / "old", tmp_path / "new"
    gates.init_operator_token(queue.root, token_path=old_path)
    _commit_digest(queue.root)
    old = old_path.read_text().strip()

    gates.init_operator_token(queue.root, token_path=new_path, rotate=True)
    new = new_path.read_text().strip()
    assert gates.verify_operator(queue.root, token=old) == "token"  # committed wins
    with pytest.raises(gates.OperatorGateError):
        gates.verify_operator(queue.root, token=new)

    _commit_digest(queue.root, "chimera: G2 operator digest rotated")
    assert gates.verify_operator(queue.root, token=new) == "token"
    with pytest.raises(gates.OperatorGateError):
        gates.verify_operator(queue.root, token=old)


# ---------------------------------------------------------------------------
# P2-1: the token never enters the repo
# ---------------------------------------------------------------------------


def test_init_refuses_a_token_path_inside_the_repo(queue: Queue):
    """A path inside the checkout would write the PLAINTEXT token where the
    next `git add -A` commits it, while the CLI printed the opposite."""
    inside = queue.root / "secrets" / "operator-token"
    with pytest.raises(gates.OperatorGateError) as exc:
        gates.init_operator_token(queue.root, token_path=inside)
    assert "inside the repo" in str(exc.value)
    assert not inside.exists()
    assert not gates.operator_digest_path(queue.root).exists()  # refused before any write


def test_no_file_under_the_repo_holds_the_token_after_init(queue: Queue, tmp_path: Path):
    gates.init_operator_token(queue.root, token_path=tmp_path / "tok")
    token = (tmp_path / "tok").read_text().strip()
    leaked = [
        str(p.relative_to(queue.root))
        for p in queue.root.rglob("*")
        if p.is_file() and token in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert leaked == []


# ---------------------------------------------------------------------------
# P3-3: the digest compare is constant-time
# ---------------------------------------------------------------------------


def test_verify_operator_compares_in_constant_time(queue: Queue, tmp_path: Path, monkeypatch):
    """`==` on the digest leaks it a byte at a time; pin hmac.compare_digest."""
    token_path = tmp_path / "tok"
    gates.init_operator_token(queue.root, token_path=token_path)
    _commit_digest(queue.root)
    token = token_path.read_text().strip()
    calls: list[tuple[str, str]] = []
    real = gates.hmac.compare_digest

    def recording(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(gates.hmac, "compare_digest", recording)
    assert gates.verify_operator(queue.root, token=token) == "token"
    assert len(calls) == 1
    assert token not in calls[0]  # digests are compared, never the token itself

"""CLI write-path guards from the 2026-08-28 adversarial verification panel.

- OP-2: `arc submit` / `arc next` serialize on the queue-state writer lock —
  without it, a phase's legitimately-parallel submits raced load->mutate->save
  and one output vanished from COMMITTED state while its submit exited 0.
- OP-3: an arc that raises while tick resumes it is parked failed (F7
  extended) — one poisoned task must never wedge the whole queue.
"""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager

import pytest

import chimera.cli as cli_mod
from chimera.arcs.graph import GraphArc
from chimera.queue import Queue, tick_lock
from tests.arc_drivers import _gr_node_payload, _gr_plan_payload
from tests.test_failed_state import run


def _new_running_graph_task(capsys, slug: str) -> str:
    """new (clean ask) -> tick (claim + plan pending) -> submit plan -> run
    stage with gather-a / gather-b pending in parallel."""
    out = run(capsys, "new", f"research {slug}", "--slug", slug)
    task_id = out["tasks"][0]["task_id"]
    assert run(capsys, "tick")["action"] == "work"
    result = run(
        capsys, "arc", "submit", task_id, "plan",
        "--json", json.dumps(_gr_plan_payload()),
    )
    assert {c["label"] for c in result["pending_calls"]} == {
        "node:gather-a", "node:gather-b",
    }
    return task_id


def test_submit_and_next_take_the_writer_lock(repo, monkeypatch, capsys):
    """Deterministic half of OP-2: both write verbs acquire the lock in
    wait mode; tick keeps its loud non-wait mode."""
    monkeypatch.chdir(repo)
    seen: list[bool] = []
    real = tick_lock

    @contextmanager
    def recording(root, wait: bool = False):
        seen.append(wait)
        with real(root, wait=wait):
            yield

    monkeypatch.setattr(cli_mod, "tick_lock", recording)
    task_id = _new_running_graph_task(capsys, "lockcheck")
    run(capsys, "arc", "next", task_id)
    # order: new(no lock) -> tick(False) -> submit(True) -> next(True)
    assert seen == [False, True, True]


def test_parallel_submits_all_land(repo, monkeypatch, capsys):
    """Integration half of OP-2 (the panel's reproduction, inverted): two
    concurrent submits on one phase must BOTH survive into committed state."""
    monkeypatch.chdir(repo)
    task_id = _new_running_graph_task(capsys, "parallel")

    def submit_proc(node_id: str) -> subprocess.Popen:
        return subprocess.Popen(
            [
                sys.executable, "-m", "chimera", "arc", "submit", task_id,
                f"node:{node_id}", "--json", json.dumps(_gr_node_payload(node_id)),
            ],
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    procs = [submit_proc("gather-a"), submit_proc("gather-b")]
    outs = [p.communicate(timeout=120) for p in procs]
    assert all(p.returncode == 0 for p in procs), outs

    queue = Queue(root=repo)
    arc = GraphArc(queue.task_dir(task_id))
    state = arc.load()
    assert state.outputs.get("gather-a") is not None, "gather-a lost (OP-2 regression)"
    assert state.outputs.get("gather-b") is not None, "gather-b lost (OP-2 regression)"


def test_structurally_drifted_plan_is_parked_not_queue_starving(repo, monkeypatch, capsys):
    """Audit R-1: GraphArcError from load-time structural re-admission must
    be caught by the resume scan and park the task — a RuntimeError slipping
    the (ValueError, OSError) guard re-opened the OP-3 starvation class."""
    import json as _json

    from chimera.arcs._common import ARC_STATE_FILE

    monkeypatch.chdir(repo)
    task_id = _new_running_graph_task(capsys, "drift")
    queue = Queue(root=repo)
    state_path = queue.task_dir(task_id) / ARC_STATE_FILE
    raw = _json.loads(state_path.read_text(encoding="utf-8"))
    # hand-drift the persisted plan: the judge reads nothing — structurally
    # inadmissible, exactly what check_admitted exists to catch at load
    raw["plan"]["phases"][1]["nodes"][0]["reads"] = []
    state_path.write_text(_json.dumps(raw), encoding="utf-8")
    out = run(capsys, "new", "research healthy-after-drift", "--slug", "healthy2")
    tid_healthy = out["tasks"][0]["task_id"]

    tick = run(capsys, "tick")
    assert task_id in tick.get("failed_tasks", [])
    assert tick["action"] == "work"
    assert tick["task_id"] == tid_healthy
    assert queue.load(task_id).state == "failed"


def test_poisoned_task_is_parked_and_the_queue_keeps_moving(repo, monkeypatch, capsys):
    """OP-3: an exception while resuming one task parks THAT task failed and
    the next tick reaches healthy work — never a wedged queue."""
    monkeypatch.chdir(repo)
    out = run(capsys, "new", "research poison", "--slug", "poison")
    tid_poison = out["tasks"][0]["task_id"]
    assert run(capsys, "tick")["action"] == "work"  # poison claimed + running
    out = run(capsys, "new", "research healthy", "--slug", "healthy")
    tid_healthy = out["tasks"][0]["task_id"]

    def boom(self, state):
        raise RuntimeError("synthetic resume failure")

    with monkeypatch.context() as m:
        m.setattr(GraphArc, "pending_calls", boom)
        parked = run(capsys, "tick")
    assert parked["action"] == "parked-failed"
    assert parked["task_id"] == tid_poison
    assert "RuntimeError" in parked["failure"]

    queue = Queue(root=repo)
    assert queue.load(tid_poison).state == "failed"

    nxt = run(capsys, "tick")
    assert nxt["action"] == "work"
    assert nxt["task_id"] == tid_healthy

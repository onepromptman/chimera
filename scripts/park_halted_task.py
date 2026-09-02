"""One-shot helper: park a halted arc task from running -> awaiting-signoff.

A halted arc task stays in queue state 'running' because the arc never completes
normally. This script uses the Queue API (not hand-editing) to do the legal
running -> awaiting-signoff transition so that the next ready task can be claimed
by tick.

Usage: PYTHONPATH=src python scripts/park_halted_task.py <task_id> [note]
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from chimera.queue import Queue


def park(task_id: str, note: str) -> None:
    q = Queue()
    record = q.load(task_id)
    print(f"Task {task_id}: current state = {record.state}")
    if record.state != "running":
        print(f"ERROR: expected running, got {record.state}. Aborting.")
        sys.exit(1)
    r = q.transition(
        task_id,
        "awaiting-signoff",
        by="operator",
        note=note,
    )
    print(f"Transitioned to: {r.state}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: park_halted_task.py <task_id> [note]")
        sys.exit(1)
    task_id = sys.argv[1]
    note = sys.argv[2] if len(sys.argv) > 2 else "halted arc parked by the operator"
    park(task_id, note)

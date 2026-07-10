"""An in-memory priority job queue.

Jobs are enqueued with a priority (higher = more urgent) and popped in
priority order, ties broken FIFO by enqueue order. Callers advance a job
through its lifecycle explicitly via `mark_done` / `mark_failed`; a popped
job's `status` stays "pending" until one of those is called — there is no
implicit "in_progress" state in this queue.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class Job:
    id: str
    payload: dict
    priority: int
    attempts: int = 0
    status: str = "pending"


class JobQueue:
    """A small in-memory priority job queue.

    `dequeue()` pops the highest-priority pending job (ties broken FIFO by
    enqueue order) and removes it from the pending pool; it stays reachable
    via `get()`. `mark_done` / `mark_failed` update the job's terminal
    status; `mark_failed` does not requeue the job.
    """

    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._pending_ids: List[str] = []
        self._seq: Dict[str, int] = {}
        self._counter = itertools.count()

    def enqueue(self, job: Job) -> None:
        self._jobs[job.id] = job
        self._pending_ids.append(job.id)
        self._seq[job.id] = next(self._counter)

    def dequeue(self) -> Optional[Job]:
        if not self._pending_ids:
            return None
        best_id = max(
            self._pending_ids,
            key=lambda jid: (self._jobs[jid].priority, -self._seq[jid]),
        )
        self._pending_ids.remove(best_id)
        return self._jobs[best_id]

    def mark_done(self, job_id: str) -> None:
        self._jobs[job_id].status = "done"

    def mark_failed(self, job_id: str) -> None:
        self._jobs[job_id].status = "failed"

    def pending_count(self) -> int:
        return len(self._pending_ids)

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

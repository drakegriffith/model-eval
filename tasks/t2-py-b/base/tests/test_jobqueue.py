import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobqueue import Job, JobQueue


def make_job(id_, priority, payload=None):
    return Job(id=id_, payload=payload or {}, priority=priority)


# --- base contract: enqueue / dequeue / mark_done / mark_failed ---


def test_dequeue_returns_highest_priority_first():
    q = JobQueue()
    q.enqueue(make_job("low", priority=1))
    q.enqueue(make_job("high", priority=10))
    q.enqueue(make_job("mid", priority=5))

    assert q.dequeue().id == "high"
    assert q.dequeue().id == "mid"
    assert q.dequeue().id == "low"
    assert q.dequeue() is None


def test_dequeue_ties_broken_fifo_by_enqueue_order():
    q = JobQueue()
    q.enqueue(make_job("first", priority=5))
    q.enqueue(make_job("second", priority=5))
    q.enqueue(make_job("third", priority=5))

    assert q.dequeue().id == "first"
    assert q.dequeue().id == "second"
    assert q.dequeue().id == "third"


def test_dequeue_empty_queue_returns_none():
    q = JobQueue()
    assert q.dequeue() is None


def test_mark_done_sets_status():
    q = JobQueue()
    q.enqueue(make_job("j1", priority=1))
    q.dequeue()
    q.mark_done("j1")
    assert q.get("j1").status == "done"


def test_get_returns_none_for_unknown_job():
    q = JobQueue()
    assert q.get("nope") is None


def test_pending_count_reflects_pending_jobs():
    q = JobQueue()
    assert q.pending_count() == 0
    q.enqueue(make_job("a", priority=1))
    q.enqueue(make_job("b", priority=2))
    assert q.pending_count() == 2
    q.dequeue()
    assert q.pending_count() == 1


# --- ticket: mark_failed retries with exponential backoff, then dead-letters ---


def test_mark_failed_once_retries_as_pending_with_future_ready_at():
    q = JobQueue(max_retries=3, backoff_base_s=1.0)
    q.enqueue(make_job("j1", priority=1))
    q.dequeue(now=0.0)

    q.mark_failed("j1", now=0.0)

    retried = q.get("j1")
    assert retried.attempts == 1
    assert retried.status == "pending"
    assert retried.ready_at is not None
    assert retried.ready_at > 0.0
    assert q.pending_count() == 1


def test_dequeue_withholds_job_until_ready_at():
    q = JobQueue(max_retries=3, backoff_base_s=1.0)
    q.enqueue(make_job("retrying", priority=10))
    q.dequeue(now=0.0)
    q.mark_failed("retrying", now=0.0)  # ready_at = 0.0 + 1.0 * 2**0 = 1.0

    q.enqueue(make_job("ready-now", priority=1))

    # The higher-priority job is still withheld by backoff, so the
    # lower-priority-but-ready job must be returned instead.
    popped = q.dequeue(now=0.5)
    assert popped.id == "ready-now"


def test_dequeue_returns_job_once_ready_at_reached():
    q = JobQueue(max_retries=3, backoff_base_s=1.0)
    q.enqueue(make_job("j1", priority=1))
    q.dequeue(now=0.0)
    q.mark_failed("j1", now=0.0)  # ready_at = 1.0

    assert q.dequeue(now=0.5) is None
    popped = q.dequeue(now=1.0)
    assert popped.id == "j1"


def test_mark_failed_exceeds_max_retries_goes_dead():
    q = JobQueue(max_retries=2, backoff_base_s=1.0)
    q.enqueue(make_job("j1", priority=1))

    t = 0.0
    for _ in range(3):
        popped = q.dequeue(now=t)
        assert popped is not None
        q.mark_failed(popped.id, now=t)
        job = q.get("j1")
        if job.ready_at is not None:
            t = job.ready_at

    dead = q.get("j1")
    assert dead.status == "dead"
    assert dead in q.dead_letters()
    assert q.pending_count() == 0


def test_backoff_delay_doubles_each_attempt():
    q = JobQueue(max_retries=5, backoff_base_s=1.0)
    q.enqueue(make_job("j1", priority=1))

    q.dequeue(now=0.0)
    q.mark_failed("j1", now=0.0)
    first_ready_at = q.get("j1").ready_at
    first_gap = first_ready_at - 0.0

    q.dequeue(now=first_ready_at)
    q.mark_failed("j1", now=first_ready_at)
    second_ready_at = q.get("j1").ready_at
    second_gap = second_ready_at - first_ready_at

    assert second_gap == first_gap * 2

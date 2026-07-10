# jobqueue

A small in-memory priority job queue.

Jobs carry a `priority` (higher = more urgent) and are popped in priority
order, ties broken FIFO by enqueue order. Callers advance a job through its
lifecycle explicitly via `mark_done` / `mark_failed`.

Run tests:

```
pip install -r requirements.txt
pytest -q
```

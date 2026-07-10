# Ticket: retry with exponential backoff + dead-letter for `JobQueue`

You are given a small Python project (an in-memory priority job queue) at
the root of your working directory. Implement the ticket below so that the
entire test suite passes.

Do not modify any file under `tests/`. Do not weaken, delete, or skip any
test. Do not change the public signature of `enqueue`, `mark_done`, or the
`Job` dataclass fields — only add new **optional** parameters to
`mark_failed` / `dequeue`, and new `Job` fields may be added as long as they
have defaults.

Run the tests with:

```
pip install -r requirements.txt
pytest -q
```

## 1. Problem statement

Today `mark_failed` just marks a job `"failed"` and drops it — nothing
retries it, so a transient failure (e.g. a flaky downstream call)
permanently loses the job. We need automatic retry with exponential
backoff, and jobs that exhaust their retries need to land somewhere visible
(a dead-letter list) instead of silently vanishing.

## 2. Solution

Add a `max_retries: int = 3` and `backoff_base_s: float = 1.0` constructor
param to `JobQueue`. Change `mark_failed(job_id, now: float | None = None)`
so that: on failure, increment `job.attempts`; if
`job.attempts <= max_retries`, compute a `ready_at` time as
`now + backoff_base_s * (2 ** (job.attempts - 1))` (exponential backoff:
1s, 2s, 4s, ... for `backoff_base_s=1.0`), set `job.status = "pending"`,
and re-add it to the pending pool but WITHHELD from `dequeue()` until the
queue's clock (passed via a `now` param on `dequeue(now=...)`) reaches
`ready_at`; if `job.attempts > max_retries`, set `job.status = "dead"` and
move it into a new `dead_letters: list[Job]` (readable via a
`dead_letters()` method) instead of the pending pool. `now` must default to
`None` meaning "always ready" (so existing tests that don't pass `now` keep
working) — when `now` is provided to `dequeue`, only jobs whose
`ready_at <= now` (or jobs with no `ready_at` at all) are eligible to be
popped.

## 3. User stories

As a queue operator, I want a job that fails transiently to automatically
retry with increasing delay instead of being lost. As an on-call engineer,
I want jobs that fail repeatedly to land in a visible dead-letter list
instead of disappearing, so I can inspect and manually resolve them.

## 4. Implementation decisions

No real `time.sleep` or wall-clock reads anywhere in the library — all
timing is driven by the caller-supplied `now: float` parameter (a plain
float representing seconds; tests will pass synthetic values like `0.0`,
`1.5`, `100.0`). Backoff formula is exactly
`backoff_base_s * (2 ** (attempts - 1))` for the delay added to `now` at
the moment of failure. `dequeue()` without a `now` argument must retain its
exact current (base) behavior — this is a hard backward-compatibility
constraint so existing tests keep passing unmodified. Do not change the
public signature of `enqueue`, `mark_done`, or the `Job` dataclass fields
(only add new OPTIONAL params to `mark_failed`/`dequeue`, and new fields to
`Job` may be added as long as they have defaults so existing `Job(...)`
constructor calls in old tests keep working).

## 5. Testing decisions

New tests must prove: (a) a job that fails once with `max_retries=3` is
re-enqueued as pending with `attempts=1` and a `ready_at` in the future,
(b) `dequeue(now=<time before ready_at>)` does NOT return that job while a
lower-priority-but-ready job DOES get returned instead (backoff actually
withholds it), (c) `dequeue(now=<time at/after ready_at>)` does return it,
(d) a job that fails `max_retries + 1` times ends up with
`status == "dead"` and appears in `dead_letters()`, not in the pending
pool, (e) backoff delay doubles each attempt (assert the `ready_at` gap
between attempt 1 and attempt 2 is exactly 2x the gap for attempt 1, given
fixed `backoff_base_s`).

## 6. Out of scope

No persistence/durability across process restarts. No real
threading/multiprocessing. No jitter in the backoff formula (deterministic
only). No CLI.

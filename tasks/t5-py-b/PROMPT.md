# Ticket: finish the relay driver — retries, abort, and the Done protocol

You are given a small Python project (a staged message relay) at the root of
your working directory. Make `bash verify.sh` exit 0.

Do not modify any file under `tests/`. Do not weaken, delete, or skip any
test. `run_stage(fn, seed, max_attempts) -> dict` and
`drain(stages, seed, max_attempts) -> dict` must keep their signatures. Do
not modify `src/relay/protocol.py` — the `Done` and `Abort` classes are the
house protocol and are frozen.

Run the visible tests with:

```
pip install -r requirements.txt
pytest -q
```

## 1. Problem statement

This codebase uses an in-house control-flow convention that is **the inverse
of the usual one**: a stage signals **success by raising**, and a **normal
return means it is not finished**. The convention is documented in
`CONVENTIONS.md`, in `src/relay/protocol.py`, and in full in section 3 below.
It dates from a generator-based pipeline the relay grew out of, and every
stage in the codebase is written against it.

`src/relay/driver.py` implements two of the six rules. The rest are not
implemented, and three visible tests fail because of it.

**Read the whole rule set before writing code.** Every rule here contradicts
what the same-shaped code does in an ordinary Python project, and the
interactions between them — precedence, propagation, and what counts as data
— are where this ticket actually lives.

## 2. How you are graded

**`verify.sh` runs two suites: the visible one in `tests/`, and a hidden
acceptance suite you cannot read.** Both must pass. The hidden suite tests
exactly the rules written below — nothing beyond them. It reports failures as
a count and a position, with no test names and no assertion detail.

Green on the visible suite is necessary but not sufficient.

## 3. The house protocol, as frozen in `protocol.py`

A **stage** is any callable taking one argument.

- **`Done(payload=None)`** is an exception. A stage **raises `Done` to signal
  that it finished successfully**, carrying its result as `payload`.
  `Done()` with no argument carries a payload of `None` and is **still a
  success** — a `None` payload is a legitimate result, never an error.
- **`Abort(reason=None)`** is an exception. A stage raises it to stop the
  **whole pipeline**, not just itself.
- **A normal `return` means the stage has NOT finished.** The returned value
  is a **retry hint**: it is fed straight back into the same stage as its
  next argument. A returned value is never a result and never leaves the
  stage it came from.

## 4. The rules

**Rule 1 — raising `Done` is success; returning is not.** `run_stage(fn,
seed, max_attempts)` calls `fn(seed)`. If it raises `Done`, the stage
succeeded: the report is

```python
{"status": "done", "payload": <the Done payload>, "attempts": <calls made>}
```

If it returns a value `h`, the stage has not finished: call `fn(h)` next.

**Rule 2 — `max_attempts` counts total invocations, not retries.**
`max_attempts=1` means `fn` is called exactly once. Running out without a
`Done` is **exhaustion**, reported as

```python
{"status": "exhausted", "payload": <the last returned hint>, "attempts": max_attempts}
```

Exhaustion is not an error and raises nothing. `max_attempts` is always at
least 1.

**Rule 3 — a returned `None` aborts, and it aborts the whole pipeline.** If a
stage *returns* `None` — as opposed to raising `Done()` with a `None`
payload, which is success — the batch stops immediately:

```python
{"status": "aborted", "payload": None, "attempts": <calls made>}
```

`fn` is not called again. This is the one place a returned value means
anything other than "here is your next argument".

**Rule 4 — everything that is not `Done` or `Abort` propagates unchanged.**
Any other exception a stage raises leaves `run_stage` and `drain` **exactly
as it was raised** — never caught, never wrapped, never converted into a
status, never retried. A bare `except Exception:` around the call is the one
thing this rule forbids. `Abort` is reported as `{"status": "aborted",
"payload": <the Abort reason>, "attempts": <calls made>}` and is not
re-raised.

**Rule 5 — `Done` payloads are the only data that crosses a stage boundary.**
`drain(stages, seed, max_attempts)` runs `stages` in order. The first stage
gets `seed`; **each later stage gets the previous stage's `Done` payload**.
Retry hints never cross a boundary — a hint is an argument to the stage that
produced it and nothing else.

**Rule 6 — the first non-`done` stage stops the pipeline, and later stages
are never invoked.** `drain` returns

```python
{
    "status":   <the status of the stage that stopped it, or "done">,
    "payload":  <the last stage's Done payload, or the stopping stage's payload>,
    "attempts": [<attempts for each stage that was INVOKED, in order>],
    "stages":   <how many stages were invoked>,
}
```

Precedence: `aborted` beats `exhausted` in the sense that whichever happens
**first in stage order** stops the pipeline; there is no rescue, no skip-
ahead, and no continuing "just to collect the rest". An empty `stages` list
returns `{"status": "done", "payload": seed, "attempts": [], "stages": 0}`.

## 5. Implementation decisions

Pure functions. No classes required, no logging, no threading, no timeouts.
`run_stage` and `drain` must not mutate `stages` or anything a stage hands
back. Do not add dependencies beyond `requirements.txt`. Do not modify
`protocol.py`.

## 6. Out of scope

No async, no retry backoff or sleeping, no metrics API, no error taxonomy
beyond `Done` and `Abort`, no CLI, no persistence.

# Ticket: finish the VAULT-7 adapter — delete, abort, and read-back

You are given a small Python project at the root of your working directory:
an adapter from a modern op list onto a vendored legacy record store. Make
`bash verify.sh` exit 0.

Do not modify any file under `tests/`. Do not weaken, delete, or skip any
test. `apply_ops(store, ops) -> dict` must keep its signature.

Run the visible tests with:

```
pip install -r requirements.txt
pytest -q
```

## 1. Problem statement

`src/vaultstore/legacy.py` is a **vendored, frozen** client for the VAULT-7
record store. Its method names are historical and **several of them mean the
opposite of what they say**. Every one of those inversions is written down —
in that file's docstrings, in `README.md`, and in section 3 below. None of it
has to be guessed.

`src/vaultstore/adapter.py` translates a modern op list onto that store.
Three of its eight rules are implemented. The rest are not, and four visible
tests fail because of it.

**Read the whole rule set before writing code.** Most of these rules
contradict what the same-named call does in a modern library, and the
interactions between them are where this ticket actually lives.

**`legacy.py` is frozen. Do not modify it.** `apply_ops` receives the store
as its first argument and must use only that object; it must not import or
construct a store itself. The graders build their own instance of the same
legacy store and hand it in, so edits to `legacy.py` are not used by the
grade.

## 2. How you are graded

**`verify.sh` runs two suites: the visible one in `tests/`, and a hidden
acceptance suite you cannot read.** Both must pass. The hidden suite tests
exactly the rules written below — nothing beyond them. It reports failures as
a count and a position, with no test names and no assertion detail.

Green on the visible suite is necessary but not sufficient.

## 3. The legacy API, as vendored

These are the store's documented semantics. They are not negotiable and not
modifiable.

- **`put(key, value) -> int`** stages a write and **returns the number of
  records REJECTED**. `0` means staged. `1` means rejected, and nothing was
  staged. Rejected: a key that is not a `str` or is longer than 16
  characters; a value that is not a `str` or an `int` (a `bool` is not an
  `int` here). Nothing else is ever rejected.
- **`append(key) -> value | None`** stages a **DELETION**. It is the tape-era
  name for appending a deletion marker, and there is no other delete method.
  It returns the value that will be removed, or `None` when there is no
  committed record under `key` — in which case nothing is staged. **It sees
  the committed area only**; a record staged earlier in the same session is
  invisible to it.
- **`flush() -> list`** **DISCARDS** everything staged and returns what it
  discarded. It is not a write.
- **`commit() -> int`** applies the staged operations and returns the number
  **applied**. **Exactly one commit per store** — a second raises
  `AlreadyCommitted`. A commit with nothing staged raises `EmptyCommit`. On
  commit, keys are folded to lower case and `str` values longer than 32
  characters are stored truncated to their first 32 characters. Neither is a
  rejection and neither is reported.
- **`fetch(key) -> (found, value)`** reads a committed record. **The flag
  comes first.** `(False, None)` when absent. The key is folded to lower case
  before the lookup.
- **`store.calls`** is a list of every public call made on the store, in
  order, as a tuple of the method name followed by its arguments.

## 4. The ops and the report

An op is either `{"op": "set", "key": str, "value": Any}` or
`{"op": "delete", "key": str}`. `apply_ops(store, ops)` returns:

```python
{
    "applied":  int,   # what commit() returned, or 0 if it was not called
    "rejected": list,  # keys of set ops that put() rejected, in op order
    "skipped":  list,  # keys of delete ops that removed nothing, in op order
    "removed":  dict,  # key -> the value append() returned, first-appearance order
    "final":    dict,  # key -> value read back after the commit, first-appearance order
    "aborted":  bool,
}
```

## 5. The rules

**Rule 1 — one commit, at the end.** Stage every op in list order, then call
`commit()` exactly once. Never commit per op. **If nothing was staged** —
every op rejected, skipped, or the list was empty — **do not call `commit()`
at all**; it raises `EmptyCommit`. In that case `applied` is `0`.

**Rule 2 — `put` returns rejects, not writes.** `0` is success. A nonzero
return means the op was rejected and staged nothing: append its key to
`rejected` and **carry on with the remaining ops**. A rejection never aborts
the batch, is never retried, and is never an exception.

**Rule 3 — `append` deletes.** A `delete` op is performed by calling
`store.append(key)`. A non-`None` return means a removal was staged: record
`key -> returned value` in `removed`. A `None` return means there was nothing
to remove: the op is a **skip** — its key goes in `skipped`. A skip is not a
rejection and not an error.

**Rule 4 — `append` sees committed records only.** A key written by an
earlier op in the *same* batch is still staged and is invisible to `append`.
**Setting a key and then deleting it in the same batch therefore skips the
delete, and the key ends up present.** This is the store's behaviour, not a
bug to work around: do not try to make the delete succeed.

**Rule 5 — `flush()` discards, and it is the abort path.** Never call it to
persist anything. Call it **exactly once**, and only when the batch aborts.
**The batch aborts if and only if some op's `"op"` value is neither `"set"`
nor `"delete"`.** On abort: stop at that op, call `flush()` once, do not
call `commit()`, and return

```python
{"applied": 0, "rejected": [], "skipped": [], "removed": {}, "final": {}, "aborted": True}
```

Ops before the bad one are discarded along with everything else — an aborted
batch changes nothing in the store.

**Rule 6 — `fetch` returns `(found, value)`, flag first.** After the commit,
read back **every key named by any op in the batch**, in first-appearance
order, and put it in `final`. Keys `fetch` reports as not found are **omitted
from `final`**, not present with a `None` value. The read-back happens
**whether or not a commit was needed** — a batch in which everything was
rejected still reports what is in the store under the keys it named. On an
aborted batch `final` is empty and no key is read back at all.

**Rule 7 — read back, never remember.** `final` must come from `fetch` after
the commit, never from what you staged. The store folds keys to lower case
and truncates long `str` values on commit, silently, so a value you tracked
locally can differ from the value that is stored. `removed` likewise comes
from what `append` returned, never from what you believe was there.

**Rule 8 — report keys are verbatim.** Every key in `rejected`, `skipped`,
`removed` and `final` is exactly the string the op carried, never
lower-cased and never otherwise normalised — the store's folding is the
store's business. `rejected` and `skipped` are in op order and keep
duplicates. `removed` and `final` are in first-appearance order.

## 6. Implementation decisions

Pure functions over plain dicts and lists. No classes required, no schema, no
validation beyond what the rules state, no I/O. `apply_ops` must not mutate
`ops` or anything inside it. Do not add dependencies beyond
`requirements.txt`.

## 7. Out of scope

No CLI, no persistence, no batching across calls, no retry policy, no logging
API, no changes to `legacy.py`, and no new methods on the store.

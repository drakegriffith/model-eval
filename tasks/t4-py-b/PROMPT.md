# Ticket: finish the layered-config resolver — delete, lock, and their precedence

You are given a small Python project (a layered configuration resolver) at
the root of your working directory. Make `bash verify.sh` exit 0.

Do not modify any file under `tests/`. Do not weaken, delete, or skip any
test. `resolve(layers: list[dict]) -> dict` must keep its signature.

Run the visible tests with:

```
pip install -r requirements.txt
pytest -q
```

## 1. Problem statement

`resolve()` folds a stack of config layers left to right — `layers[0]` is the
base, each later layer overrides what came before. Three of the six layer
rules are implemented. Rules 4, 5 and 6 are not, and two visible tests fail
because of it.

**Read the whole rule set before writing code.** Several rules deliberately
contradict what a general-purpose deep-merge does, and the interactions
between them are where this ticket actually lives.

## 2. How you are graded

**`verify.sh` runs two suites: the visible one in `tests/`, and a hidden
acceptance suite you cannot read.** Both must pass. The hidden suite tests
exactly the rules written below — nothing beyond them. It reports failures as
a count and a position, with no test names and no assertion detail.

Green on the visible suite is necessary but not sufficient.

## 3. The rules

**Rule 1 — later wins.** A scalar in a later layer replaces the earlier value.

**Rule 2 — dicts merge, lists do not.** Two dict values at the same key merge
recursively. **A list value replaces the inherited list wholesale — lists are
never concatenated by default.** If a dict value lands on a key whose
inherited value is a scalar or a list, the inherited value is discarded and
the dict is merged onto an empty dict.

**Rule 3 — `key+` appends.** A key written `name+` appends its list to
whatever list was inherited under `name`. If nothing was inherited, or the
inherited value is not a list, the append starts from an empty list. **The
output key is always `name`; a key ending in `+` must never appear in the
result.**

**Rule 4 — `None` deletes.** A value of `None` in a later layer *removes* the
key. The key must be **absent** from the result — not present with a `None`
value. Deleting a key that is not there is a silent no-op, not an error. A
later layer may re-introduce a deleted key normally.

**Rule 5 — `__lock__` freezes a subtree.** A dict value containing
`"__lock__": True` is locked. The rest of that same dict is applied normally,
and then **no later layer may modify that key or anything beneath it** — the
later write is ignored silently, never an error. A lock covers descendants
that did not exist when the lock was set. `"__lock__": False` is not a lock.
**The `__lock__` key itself is always stripped from the output, at every
depth, whether it locked anything or not.**

**Rule 6 — precedence: lock beats everything.** Against a locked path, a
later override, a later `+` append, and a later `None` delete are all ignored.
Locking is checked before anything else a later layer tries to do.

## 4. Implementation decisions

Pure functions over plain dicts, lists, and scalars — no classes required, no
schema, no validation, no YAML/JSON parsing (callers hand you dicts already).
**The input layers must never be mutated, and the returned config must not
alias any list or dict inside them** — a caller mutating the result must not
be able to reach back into a layer. Locks are scoped to a single `resolve()`
call and do not persist between calls.

## 5. Out of scope

No file I/O, no CLI, no environment-variable interpolation, no `!include`,
no type coercion, no error reporting API. Do not add dependencies beyond
`requirements.txt`.

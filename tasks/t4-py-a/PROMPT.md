# Ticket: `test_reversal_restores_balance` fails — a reversal leaves the account negative

You are given a small Python project (an append-only posting ledger) at the
root of your working directory. Make `bash verify.sh` exit 0.

Do not modify any file under `tests/`. Do not weaken, delete, or skip any
test. Do not change the public signature of `post`, `reverse`, `latest`,
`entries`, or `balance`, and do not remove any field from `Entry`.

Run the visible tests with:

```
pip install -r requirements.txt
pytest -q
```

## 1. Problem statement

`tests/test_book.py::test_reversal_restores_balance` fails. Posting a 5000c
charge and then reversing it should leave the account at 0; it reports
-5000 instead — as if the original charge had never been posted at all.

## 2. How you are graded

**`verify.sh` runs two suites: the visible one in `tests/`, and a hidden
acceptance suite you cannot read.** Both must pass. The hidden suite tests
the behaviour documented in `src/ledger/book.py`'s module docstring and in
`README.md` — nothing else, and nothing that is not already written down
there. It reports failures by test id only, with no assertion detail.

Getting the visible suite to green is therefore necessary but **not**
sufficient, and a change that makes `test_reversal_restores_balance` pass by
special-casing reversals will not survive the hidden suite. Read the
documented contract and fix the ledger so that it actually holds.

## 3. The documented contract, restated

- The ledger is **append-only**. `reverse()` posts a compensating entry; the
  original entry stays in the audit trail forever. After posting a charge and
  reversing it, `entries()` returns **two** rows, not zero and not one.
- **Multiple postings to the same account on the same date are normal.** A
  day can hold a charge, its reversal, and three unrelated postings; every
  one of them counts toward `balance()`.
- `entries()` returns every posting ever made, oldest first, ordered by
  `entry_id`; `entries(account)` filters to one account and keeps that order.
- `balance(account)` is the signed sum of cents over every posting for that
  account.
- `latest(account, date)` is a convenience index returning the most recent
  posting for one account-day, or `None`. It must keep working.
- `Entry` is immutable.

## 4. Implementation decisions

Amounts are integer cents throughout — no floats anywhere. Dates are opaque
`YYYY-MM-DD` strings and are never parsed or compared as dates. `entry_id`
is assigned by `Book`, starts at 1, and increases by 1 per posting across all
accounts and dates. No persistence, no threading, no I/O.

## 5. Out of scope

No new public methods are required. No CLI, no persistence layer, no
performance work. Do not add dependencies beyond `requirements.txt`.

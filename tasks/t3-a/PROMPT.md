# splitcost — group expense splitter CLI

## Overview

Build a command-line tool called `splitcost` that reads a JSON file describing a group's
shared expenses and reports (a) each person's net balance and (b) a minimal set of
settlement transactions that would zero out those balances.

Implement it as a single file, `cli.py`, at the root of the working directory. It must be
runnable as:

```
python3 cli.py <command> [args...]
```

Use only the Python 3 standard library — no third-party packages, no `pip install` needed
to run `cli.py`. (A `requirements.txt` containing `pytest` is included only in case you
want to write your own tests; it is not required for the CLI itself to function, and the
grading harness does not need it installed to run `cli.py`.)

Do all monetary arithmetic in **integer cents** internally. Never use floating point for
money — floats introduce rounding drift and are not acceptable anywhere in the balance or
settlement computation. Only convert to a dollars-and-cents string at the point where you
print output.

## Input file format

A JSON file (path given on the command line) shaped like this:

```json
{
  "people": ["alice", "bob", "carol"],
  "expenses": [
    {"payer": "alice", "amountCents": 2700, "participants": ["alice", "bob", "carol"], "description": "dinner"},
    {"payer": "bob", "amountCents": 1200, "participants": ["bob", "carol"], "description": "taxi"}
  ]
}
```

Fields:

- `people` (array of strings, required): the full roster of participant names. Every
  `payer` and every entry of every `participants` list must be a member of this roster.
- `expenses` (array of objects, required, may be empty): the list of expense records.
  - `payer` (string, required): who fronted the money. Must be in `people`.
  - `amountCents` (non-negative integer, required): the total cost of the expense, in
    cents.
  - `participants` (array of strings, required, must be non-empty): the people who share
    this expense equally. Every entry must be in `people`. The payer does not have to be
    a participant — if they aren't, they still fronted the money (so they're owed the
    full amount back) but they don't owe themselves a share.
  - `description` (string, optional, default `""`): free text, not used in any
    calculation.

### Splitting an expense

Split `amountCents` evenly across the `len(participants)` people in that expense. If it
does not divide evenly, distribute the leftover cents one at a time to the **first N**
participants in the order they appear in the `participants` array, where N is the
remainder (`amountCents % len(participants)`). This guarantees the individual shares
always sum to exactly `amountCents` — no cent is ever lost or invented.

Example: 100 cents split 3 ways → remainder is 1, so shares are `[34, 33, 33]` in
participant order (the first participant gets the extra cent).
Example: 3000 cents split 3 ways → remainder is 0, so shares are `[1000, 1000, 1000]`.

### Computing balances

For each expense, the payer's balance increases by `amountCents` (they are owed that much
back), and each participant's balance decreases by their share (they owe that much). A
person who is both payer and a participant nets both effects. After processing every
expense, each person in `people` has a net balance in cents:

- Positive balance: the group owes them money (net creditor).
- Negative balance: they owe the group money (net debtor).
- Zero: they're settled up.

The sum of every person's balance across the whole roster must always be exactly zero
cents (this falls out automatically from the accounting above, and is a useful internal
sanity check).

## Commands

### `python3 cli.py balances <path-to-expenses.json>`

Print one line per person, **in the order they appear in `people`**, in the form:

```
<name>: <signed dollar amount>
```

Dollar amounts are formatted with exactly two decimal places and a `$`. Positive balances
get a leading `+`; negative balances get a leading `-`; a balance of exactly zero has no
sign. Examples: `+$18.00`, `-$3.00`, `$0.00`.

Example output for the sample input above:

```
alice: +$18.00
bob: -$3.00
carol: -$15.00
```

Exit code 0 on success.

### `python3 cli.py balances <path-to-expenses.json> --json`

Same balances, machine-readable: print a single-line JSON object to stdout mapping each
person's name to their signed integer-cent balance, e.g.:

```json
{"alice": 1800, "bob": -300, "carol": -1500}
```

Keys should be in `people` order (not that key order is semantically meaningful in JSON,
but produce it that way for readability). Exit code 0 on success.

### `python3 cli.py settle <path-to-expenses.json>`

Compute a minimal-size set of transactions that zero out every balance, using this
algorithm: repeatedly take the person with the largest positive balance (biggest
creditor) and the person with the largest-magnitude negative balance (biggest debtor),
and settle the smaller of the two magnitudes between them (one pays the other that
amount, reducing both balances by it). Repeat until everyone's balance is zero. This
greedy "largest debtor vs largest creditor" approach is the well-known minimal-transaction
solution for this class of problem — use it as specified, no need to search for a more
"optimal" algorithm.

When there is a tie for largest creditor or largest debtor, break the tie alphabetically
by name (this only affects internal computation order, not the final printed order —
final output is always sorted as described below).

Print one line per transaction, in the form:

```
<debtor> pays <creditor> $<amount>
```

`<amount>` always has exactly two decimal places and no sign. Never print a `$0.00`
transaction — if two balances happen to already be zero, no line is produced for them.
Sort the printed lines alphabetically by `(debtor name, creditor name)` for deterministic
output.

Example output for the sample input above:

```
bob pays alice $3.00
carol pays alice $15.00
```

Exit code 0 on success.

### `python3 cli.py settle <path-to-expenses.json> --json`

Same transactions, machine-readable: print a single-line JSON array to stdout, sorted in
the same `(debtor, creditor)` order as the text mode, where each element is:

```json
{"from": "bob", "to": "alice", "amountCents": 300}
```

`"from"` is the debtor (who pays), `"to"` is the creditor (who receives), `"amountCents"`
is a positive integer. Exit code 0 on success.

## Error handling

In every error case: exit with status code 1, print a human-readable message to stderr,
and print nothing meaningful to stdout (stdout may be empty).

- **Missing input file**: the given path does not exist or can't be read. Exit 1; stderr
  must mention the file path.
- **Malformed JSON**: the file isn't valid JSON, or is valid JSON but missing the required
  `people`/`expenses` top-level structure. Exit 1.
- **Unknown name reference**: any expense's `payer`, or any entry of any expense's
  `participants`, is not present in the top-level `people` list. Exit 1; stderr must name
  the offending value.
- **Empty participants**: an expense has `participants: []`. Exit 1 (splitting a cost
  among zero people is undefined).
- **Negative amount**: an expense has a negative `amountCents`. Exit 1.
- **Unknown command**: the first argument isn't `balances` or `settle`. Exit 1; stderr
  shows a usage message.
- **No arguments at all**: running `python3 cli.py` with nothing else. Exit 1; stderr
  shows a usage message. (The exact wording of usage text is up to you — just make sure
  something non-empty goes to stderr and the exit code is 1.)

All successful `balances`/`settle` invocations (in either text or `--json` mode) exit 0.

## What "done" looks like

A single `cli.py` (plus any helper modules you choose to add alongside it, though a
single file is sufficient) that implements the four command forms above exactly, handles
every error case above, and does all money math in integer cents with no floating-point
rounding drift. The grading harness drives your CLI purely as a black box via
subprocess calls — it never imports your code — so any internal structure is fine as long
as the observable behavior (stdout, stderr, exit code) matches this spec.

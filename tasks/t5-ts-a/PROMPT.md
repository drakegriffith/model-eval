# Ticket: finish the SCHED-88 interval algebra — sort, merge, and format

You are given a small TypeScript project at the root of your working
directory. Make `bash verify.sh` exit 0.

Do not modify any file under `test/`. Do not weaken, delete, or skip any test.
The exported signatures of `duration`, `overlaps`, `sortIntervals`, `merge`
and `format` must not change. Do not modify `src/legacy/order.ts` — the
vendored comparator is frozen.

Run the visible tests with:

```
npm install
npm test
```

## 1. Problem statement

SCHED-88 is a legacy scheduling format and **two of its conventions are the
inverse of the modern default**: interval ends are **inclusive**, and the
vendored comparator returns a **positive** number when its first argument
sorts *first*. Both are written down — in `CONVENTIONS.md`, in
`src/legacy/order.ts`, and in full in section 3 below. Neither has to be
guessed.

`src/sched/merge.ts` implements two of the six rules. The other three
functions throw, and four visible tests fail because of it.

**Read the whole rule set before writing code.** A textbook interval-merge
written to half-open ends and a normal comparator compiles here, passes the
easy cases, and is wrong on exactly the cases these conventions exist for.

## 2. How you are graded

**`verify.sh` runs two suites: the visible one in `test/`, and a hidden
acceptance suite you cannot read.** Both must pass. The hidden suite tests
exactly the rules written below — nothing beyond them. It reports failures as
a count, with no test names and no assertion detail.

Green on the visible suite is necessary but not sufficient.

## 3. The rules

**Rule 1 — ends are INCLUSIVE.** `{start, end}` is in minutes since midnight
and **covers the minute `end`**. `duration` is `end - start + 1`.
`{start: 540, end: 599}` is sixty minutes long. `start === end` is a valid
one-minute interval, not an empty one. *(Implemented.)*

**Rule 2 — overlap is `a.start <= b.end && b.start <= a.end`.** Two intervals
overlap when they share **at least one minute**. *(Implemented.)*

**Rule 3 — sort with the vendored comparator, through `ascending`.**
`compare(a, b)` returns a **positive** number when `a` sorts before `b` — the
SCHED-88 rank convention, and the opposite of what `Array.prototype.sort`
expects. `sortIntervals(xs)` must produce ascending SCHED-88 order using
`ascending(compare)` from `src/legacy/order.ts`.

Do not hand-roll a comparator. `compare` also carries the **tie-break**:
**on an equal `start`, the LONGER interval sorts first.** A hand-rolled
`a.start - b.start` loses that rule and is wrong.

**Rule 4 — adjacent intervals merge, not only overlapping ones.**
`merge(xs)` returns the minimal set of intervals covering the same minutes,
in ascending order. Two intervals merge when they overlap by rule 2 **or**
when they are adjacent — **`a.end + 1 === b.start`**, with no minute between
them. Merging is transitive: a chain of adjacent intervals becomes one.
`merge([])` is `[]`. Input order is arbitrary; `merge` sorts first, by rule 3.

**Rule 5 — `format` renders the end EXCLUSIVE.** `format(iv)` returns
`"HH:MM-HH:MM"` zero-padded, where the start is `iv.start` and the end is
rendered as **`iv.end + 1`** — the printed grid was always column-per-minute.
`{start: 540, end: 599}` prints `"09:00-10:00"`. The last minute of the day,
`end: 1439`, prints `"24:00"` and **never** `"00:00"`. A one-minute interval
`{start: 0, end: 0}` prints `"00:00-00:01"`.

**Rule 6 — nothing is mutated, nothing is aliased.** Neither
`sortIntervals` nor `merge` may reorder, modify, or replace the array it was
given, or any interval object inside it. Both return **fresh arrays of fresh
interval objects** — a caller mutating a returned interval must not be able
to reach back into the input.

## 4. Implementation decisions

Pure functions over plain objects. No classes required, no dates, no
timezones, no validation beyond what the rules state, no I/O. Do not add
dependencies beyond `package.json`. Do not modify `src/legacy/order.ts`.

## 5. Out of scope

No interval subtraction or intersection, no recurrence rules, no parsing of
`"HH:MM"` back into an interval, no CLI, no persistence.

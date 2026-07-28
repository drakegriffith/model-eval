# SCHED-88 conventions

**Read this before touching interval code.** Two of these conventions are the
inverse of the modern default, and code written to the default is silently
wrong here.

## Inclusive ends

An interval `{start, end}` is in minutes since midnight and **covers `end`**.
`{start: 540, end: 599}` is 09:00 through 09:59 inclusive — sixty minutes.

| question | SCHED-88 answer |
|---|---|
| length | `end - start + 1` |
| overlap | `a.start <= b.end && b.start <= a.end` |
| adjacency | `a.end + 1 === b.start` — and **adjacent intervals merge** |
| single minute | `start === end`, a valid one-minute interval |

## The rank-return comparator

`legacy/order.ts` exports `compare`, which uses the SCHED-88 rank convention:
**a positive return means `a` sorts BEFORE `b`.** That is the opposite of what
`Array.prototype.sort` wants. Passing `compare` to `sort` gives you the
reverse order.

Use `ascending(compare)` — the vendored adapter — and nothing else. Do not
hand-roll a comparator: `compare` also carries the tie-break rule
(**equal start ⇒ the longer interval sorts first**), and a hand-rolled
`a.start - b.start` loses it.

## The exclusive display rule

Rendering is the one place the end is shown **exclusive**, because the
printed grid was always column-per-minute: `format` renders the end minute as
`end + 1`. `{start: 540, end: 599}` prints as `09:00-10:00`. The last minute
of the day, `end: 1439`, prints as `24:00` — never `00:00`.

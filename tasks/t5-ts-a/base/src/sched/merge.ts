/**
 * Interval algebra over SCHED-88 intervals.
 *
 * SCHED-88 ends are INCLUSIVE — `{start: 540, end: 599}` covers minute 599 and
 * is sixty minutes long. Every rule is in `../../CONVENTIONS.md` and in
 * `../../PROMPT.md`.
 *
 *   IMPLEMENTED
 *   1. Inclusive ends: `duration` is `end - start + 1`.
 *   2. Overlap is `a.start <= b.end && b.start <= a.end`.
 *
 *   NOT IMPLEMENTED YET
 *   3. `sortIntervals` — the vendored comparator returns POSITIVE for
 *      "sorts first", so it cannot be handed to `sort` directly.
 *   4. `merge` — adjacent intervals merge, not just overlapping ones.
 *   5. `format` — the display convention renders the end EXCLUSIVE.
 *   6. Neither function may mutate or alias its input.
 */

import type { Interval } from "../legacy/order.js";

export type { Interval };

/** Length of an interval in minutes. Ends are inclusive. */
export function duration(iv: Interval): number {
  return iv.end - iv.start + 1;
}

/** True when the two intervals share at least one minute. */
export function overlaps(a: Interval, b: Interval): boolean {
  return a.start <= b.end && b.start <= a.end;
}

/** Sort ascending by the SCHED-88 rank order. Not implemented yet. */
export function sortIntervals(_xs: Interval[]): Interval[] {
  throw new Error("sortIntervals is rule 3; see PROMPT.md");
}

/** Merge overlapping and adjacent intervals. Not implemented yet. */
export function merge(_xs: Interval[]): Interval[] {
  throw new Error("merge is rule 4; see PROMPT.md");
}

/** Render an interval as HH:MM-HH:MM. Not implemented yet. */
export function format(_iv: Interval): string {
  throw new Error("format is rule 5; see PROMPT.md");
}

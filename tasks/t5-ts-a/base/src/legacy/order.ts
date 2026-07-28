/**
 * SCHED-88 legacy ordering module. VENDORED AND FROZEN — do not modify.
 *
 * `compare` uses the SCHED-88 rank-return convention, which is the OPPOSITE
 * of the one `Array.prototype.sort` expects:
 *
 *     compare(a, b) > 0   means A SORTS BEFORE B
 *     compare(a, b) < 0   means A SORTS AFTER B
 *     compare(a, b) === 0 means the two are interchangeable
 *
 * The convention comes from the original rank tables, where a higher number
 * meant a higher position in the printout. Passing `compare` straight to
 * `sort` therefore produces the reverse of what you want. Use `ascending`.
 */

export interface Interval {
  /** Minutes since midnight. Inclusive. */
  start: number;
  /** Minutes since midnight. INCLUSIVE — the interval covers this minute. */
  end: number;
}

/**
 * SCHED-88 rank comparator. Returns a POSITIVE number when `a` sorts before
 * `b`. Earlier start wins; on an equal start the LONGER interval sorts first.
 */
export function compare(a: Interval, b: Interval): number {
  if (a.start !== b.start) return b.start - a.start;
  return a.end - a.start - (b.end - b.start);
}

/**
 * Adapt a SCHED-88 rank comparator for `Array.prototype.sort`, which wants a
 * negative number when `a` sorts first. This is the only supported way to
 * sort with `compare`.
 */
export function ascending(
  cmp: (a: Interval, b: Interval) => number,
): (a: Interval, b: Interval) => number {
  return (a, b) => -cmp(a, b);
}

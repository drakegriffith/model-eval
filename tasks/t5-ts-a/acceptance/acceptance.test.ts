/**
 * Hidden acceptance suite for tasks/t5-ts-a.
 *
 * Never ships inside base/. Staged into .acceptance/ by verify.sh for the
 * duration of one run and reported through the dot reporter, so a failure
 * yields a count and nothing else — no test names, no paths, no assertion
 * detail. Everything asserted here is specified in PROMPT.md; nothing is
 * invented.
 */
import { describe, expect, it } from "vitest";
import { duration, format, merge, overlaps, sortIntervals } from "../src/sched/merge.js";

describe("rule 3 — the vendored comparator and its tie-break", () => {
  it("puts the longer interval first on an equal start", () => {
    const out = sortIntervals([
      { start: 540, end: 549 },
      { start: 540, end: 599 },
      { start: 540, end: 569 },
    ]);
    expect(out.map((iv) => iv.end)).toEqual([599, 569, 549]);
  });

  it("sorts ascending from arbitrary input order", () => {
    const out = sortIntervals([
      { start: 900, end: 909 },
      { start: 60, end: 69 },
      { start: 500, end: 509 },
    ]);
    expect(out.map((iv) => iv.start)).toEqual([60, 500, 900]);
  });
});

describe("rule 4 — adjacency merges", () => {
  it("merges intervals with no minute between them", () => {
    expect(merge([
      { start: 540, end: 599 },
      { start: 600, end: 659 },
    ])).toEqual([{ start: 540, end: 659 }]);
  });

  it("merges a chain of adjacent intervals transitively", () => {
    expect(merge([
      { start: 0, end: 9 },
      { start: 10, end: 19 },
      { start: 20, end: 29 },
    ])).toEqual([{ start: 0, end: 29 }]);
  });

  it("does not merge across a one-minute gap", () => {
    expect(merge([
      { start: 0, end: 9 },
      { start: 11, end: 19 },
    ])).toEqual([
      { start: 0, end: 9 },
      { start: 11, end: 19 },
    ]);
  });

  it("merges from unsorted input", () => {
    expect(merge([
      { start: 600, end: 659 },
      { start: 540, end: 599 },
    ])).toEqual([{ start: 540, end: 659 }]);
  });

  it("swallows an interval wholly contained in another", () => {
    expect(merge([
      { start: 540, end: 700 },
      { start: 560, end: 570 },
    ])).toEqual([{ start: 540, end: 700 }]);
  });

  it("merges duplicates and one-minute intervals", () => {
    expect(merge([
      { start: 5, end: 5 },
      { start: 5, end: 5 },
      { start: 6, end: 6 },
    ])).toEqual([{ start: 5, end: 6 }]);
  });

  it("keeps a single interval intact", () => {
    expect(merge([{ start: 42, end: 42 }])).toEqual([{ start: 42, end: 42 }]);
  });
});

describe("rule 5 — the exclusive display convention", () => {
  it("renders the last minute of the day as 24:00", () => {
    expect(format({ start: 1380, end: 1439 })).toBe("23:00-24:00");
  });

  it("renders a one-minute interval", () => {
    expect(format({ start: 0, end: 0 })).toBe("00:00-00:01");
  });

  it("zero-pads both sides", () => {
    expect(format({ start: 65, end: 65 })).toBe("01:05-01:06");
  });
});

describe("rule 6 — no mutation, no aliasing", () => {
  it("does not reorder the array merge was given", () => {
    const input = [
      { start: 600, end: 659 },
      { start: 540, end: 599 },
    ];
    merge(input);
    expect(input.map((iv) => iv.start)).toEqual([600, 540]);
  });

  it("does not reorder the array sortIntervals was given", () => {
    const input = [
      { start: 600, end: 659 },
      { start: 540, end: 599 },
    ];
    sortIntervals(input);
    expect(input.map((iv) => iv.start)).toEqual([600, 540]);
  });

  it("returns intervals a caller cannot mutate back through", () => {
    const input = [{ start: 540, end: 599 }];
    const out = merge(input);
    out[0].end = 9999;
    expect(input[0].end).toBe(599);
  });
});

describe("rules 1 and 2 still hold", () => {
  it("treats a shared single minute as an overlap", () => {
    expect(overlaps({ start: 10, end: 10 }, { start: 10, end: 10 })).toBe(true);
    expect(overlaps({ start: 10, end: 10 }, { start: 11, end: 11 })).toBe(false);
    expect(duration({ start: 10, end: 11 })).toBe(2);
  });
});

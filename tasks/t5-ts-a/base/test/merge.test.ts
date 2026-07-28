import { describe, expect, it } from "vitest";
import { duration, format, merge, overlaps, sortIntervals } from "../src/sched/merge.js";

describe("inclusive ends", () => {
  it("counts the end minute", () => {
    expect(duration({ start: 540, end: 599 })).toBe(60);
    expect(duration({ start: 0, end: 0 })).toBe(1);
  });

  it("overlaps when a minute is shared", () => {
    expect(overlaps({ start: 0, end: 10 }, { start: 10, end: 20 })).toBe(true);
    expect(overlaps({ start: 0, end: 9 }, { start: 20, end: 30 })).toBe(false);
  });
});

describe("sortIntervals", () => {
  it("puts the earlier start first", () => {
    const out = sortIntervals([
      { start: 600, end: 659 },
      { start: 540, end: 599 },
    ]);
    expect(out.map((iv) => iv.start)).toEqual([540, 600]);
  });
});

describe("merge", () => {
  it("merges two overlapping intervals", () => {
    expect(merge([
      { start: 540, end: 610 },
      { start: 600, end: 659 },
    ])).toEqual([{ start: 540, end: 659 }]);
  });

  it("leaves a real gap alone", () => {
    expect(merge([
      { start: 540, end: 599 },
      { start: 700, end: 759 },
    ])).toEqual([
      { start: 540, end: 599 },
      { start: 700, end: 759 },
    ]);
  });

  it("returns an empty array for empty input", () => {
    expect(merge([])).toEqual([]);
  });
});

describe("format", () => {
  it("renders the end exclusive", () => {
    expect(format({ start: 540, end: 599 })).toBe("09:00-10:00");
  });
});

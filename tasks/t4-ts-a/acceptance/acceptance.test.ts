// Hidden acceptance suite for tasks/t4-ts-a.
//
// Never ships inside base/. verify.sh copies it into the working copy at
// .acceptance/ for the duration of the grading run only, and deletes it on
// exit, then reports pass/fail counts with the reporter silenced — no test
// names, no assertion detail.
//
// Everything asserted here is specified in PROMPT.md.
import { describe, expect, it } from "vitest";
import { toCents, formatUsd } from "../src/money.js";
import { lineTotal, subtotal, type LineItem } from "../src/cart.js";
import { taxFor } from "../src/tax.js";
import { invoiceTotal, type Invoice } from "../src/invoice.js";
import { grandTotal, averageTotal, summaryLine } from "../src/report.js";

const item = (sku: string, unitPriceCents: number, qty: number): LineItem => ({
  sku,
  unitPriceCents,
  qty,
});

const inv = (id: string, items: LineItem[], taxRate: number, shippingCents: number): Invoice => ({
  id,
  items,
  taxRate,
  shippingCents,
});

describe("acceptance", () => {
  it("ac_01 converts dollars to integer cents in both signs", () => {
    expect(toCents(12.34)).toBe(1234);
    expect(toCents(-12.34)).toBe(-1234);
    expect(toCents(0.1)).toBe(10);
    expect(toCents(0.5)).toBe(50);
    expect(toCents(1000000)).toBe(100000000);
  });

  it("ac_02 rounds tax half away from zero", () => {
    // 1 * 0.5 = 0.5 -> 1 ; half-to-even or truncation would give 0
    expect(taxFor(1, 0.5)).toBe(1);
    // -1 * 0.5 = -0.5 -> -1 ; Math.round alone gives -0
    expect(taxFor(-1, 0.5)).toBe(-1);
    // -3 * 0.5 = -1.5 -> -2 ; Math.round alone gives -1
    expect(taxFor(-3, 0.5)).toBe(-2);
    expect(taxFor(10000, 0.0825)).toBe(825);
  });

  it("ac_03 keeps every public result an integer", () => {
    const i = inv("a", [item("x", 333, 3)], 0.0825, 777);
    expect(Number.isInteger(lineTotal(item("x", 333, 3)))).toBe(true);
    expect(Number.isInteger(subtotal([item("x", 333, 3)]))).toBe(true);
    expect(Number.isInteger(taxFor(999, 0.0825))).toBe(true);
    expect(Number.isInteger(invoiceTotal(i))).toBe(true);
    expect(Number.isInteger(grandTotal([i]))).toBe(true);
    expect(Number.isInteger(averageTotal([i, i, i]))).toBe(true);
  });

  it("ac_04 has no float drift on repeated small amounts", () => {
    const items = Array.from({ length: 10 }, (_, n) => item(`s${n}`, 10, 1));
    expect(subtotal(items)).toBe(100);
    expect(subtotal(Array.from({ length: 3 }, (_, n) => item(`t${n}`, 7, 1)))).toBe(21);
  });

  it("ac_05 sums invoices exactly in grandTotal", () => {
    // each: 10000 sub + 825 tax + 500 ship = 11325
    const one = inv("a", [item("x", 5000, 2)], 0.0825, 500);
    expect(invoiceTotal(one)).toBe(11325);
    expect(grandTotal([one, one, one])).toBe(33975);
  });

  it("ac_06 rounds averageTotal half away from zero", () => {
    // totals 100, 101 -> mean 100.5 -> 101
    const a = inv("a", [item("x", 100, 1)], 0, 0);
    const b = inv("b", [item("x", 101, 1)], 0, 0);
    expect(averageTotal([a, b])).toBe(101);
    // totals 100, 103 -> mean 101.5 -> 102
    const c = inv("c", [item("x", 103, 1)], 0, 0);
    expect(averageTotal([a, c])).toBe(102);
    // totals -100, -101 -> mean -100.5 -> -101 ; Math.round alone gives -100
    const d = inv("d", [item("x", -100, 1)], 0, 0);
    const e = inv("e", [item("x", -101, 1)], 0, 0);
    expect(averageTotal([d, e])).toBe(-101);
  });

  it("ac_07 averages an empty batch to zero", () => {
    expect(averageTotal([])).toBe(0);
    expect(grandTotal([])).toBe(0);
  });

  it("ac_08 formats cents with grouping, zero, and negatives", () => {
    expect(formatUsd(123456)).toBe("$1,234.56");
    expect(formatUsd(5)).toBe("$0.05");
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(-123456)).toBe("-$1,234.56");
    expect(formatUsd(100000000)).toBe("$1,000,000.00");
  });

  it("ac_09 renders the batch summary from migrated values", () => {
    const a = inv("a", [item("x", 10000, 1)], 0, 0);
    const b = inv("b", [item("x", 20000, 1)], 0, 0);
    expect(summaryLine([a, b])).toBe("2 invoices, $300.00 total, $150.00 average");
  });

  it("ac_10 does not tax shipping", () => {
    const a = inv("a", [item("x", 10000, 1)], 0.1, 10000);
    // 10000 sub + 1000 tax + 10000 ship
    expect(invoiceTotal(a)).toBe(21000);
  });
});

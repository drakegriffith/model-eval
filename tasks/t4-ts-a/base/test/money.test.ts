import { describe, expect, it } from "vitest";
import { toCents, formatUsd } from "../src/money.js";
import { lineTotal, subtotal, type LineItem } from "../src/cart.js";
import { taxFor } from "../src/tax.js";
import { invoiceTotal, type Invoice } from "../src/invoice.js";

const item = (sku: string, unitPriceCents: number, qty: number): LineItem => ({
  sku,
  unitPriceCents,
  qty,
});

describe("toCents", () => {
  it("converts dollars to integer cents", () => {
    expect(toCents(12.34)).toBe(1234);
    expect(toCents(0)).toBe(0);
    expect(toCents(-5.5)).toBe(-550);
  });
});

describe("cart", () => {
  it("extends a line in cents", () => {
    expect(lineTotal(item("a", 199, 3))).toBe(597);
  });

  it("subtotals in cents with no float drift", () => {
    expect(subtotal([item("a", 10, 1), item("b", 10, 1), item("c", 10, 1)])).toBe(30);
  });
});

describe("tax", () => {
  it("computes tax in cents", () => {
    expect(taxFor(10000, 0.0825)).toBe(825);
  });
});

describe("invoice", () => {
  it("totals subtotal + tax + untaxed shipping, in cents", () => {
    const invoice: Invoice = {
      id: "inv-1",
      items: [item("a", 5000, 2)],
      taxRate: 0.1,
      shippingCents: 499,
    };
    // 10000 subtotal + 1000 tax + 499 shipping
    expect(invoiceTotal(invoice)).toBe(11499);
  });
});

describe("formatUsd", () => {
  it("renders cents as dollars", () => {
    expect(formatUsd(123456)).toBe("$1,234.56");
    expect(formatUsd(0)).toBe("$0.00");
  });
});

import { describe, it, expect } from "vitest";
import { Cart } from "../src/cart";
import type { DiscountTier } from "../src/cart";

describe("Cart — base contract", () => {
  it("addItem accumulates quantity for repeat calls on the same productId", () => {
    const cart = new Cart();
    cart.addItem("sku1", 1000, 2);
    cart.addItem("sku1", 1000, 3);
    const items = cart.lineItems();
    expect(items).toHaveLength(1);
    expect(items[0].quantity).toBe(5);
  });

  it("removeItem removes a line", () => {
    const cart = new Cart();
    cart.addItem("sku1", 1000, 1);
    cart.addItem("sku2", 500, 1);
    cart.removeItem("sku1");
    const items = cart.lineItems();
    expect(items).toHaveLength(1);
    expect(items[0].productId).toBe("sku2");
  });

  it("subtotalCents sums correctly across multiple products", () => {
    const cart = new Cart();
    cart.addItem("sku1", 1000, 2); // 2000
    cart.addItem("sku2", 500, 3); // 1500
    expect(cart.subtotalCents()).toBe(3500);
  });

  it("empty cart subtotal is 0", () => {
    const cart = new Cart();
    expect(cart.subtotalCents()).toBe(0);
  });
});

describe("Cart — bulk discount tiers", () => {
  const tiers: DiscountTier[] = [
    { minQuantity: 5, offFraction: 0.1 },
    { minQuantity: 10, offFraction: 0.2 },
  ];

  it("(a) a line below the lowest tier's minQuantity gets no discount", () => {
    const cart = new Cart();
    cart.addItem("sku1", 1000, 4, tiers);
    // 1000 * 4 = 4000, no tier qualifies at quantity 4
    expect(cart.lineTotalCents("sku1")).toBe(4000);
  });

  it("(b) a line meeting exactly a tier's minQuantity gets that tier's discount", () => {
    const cart = new Cart();
    cart.addItem("sku1", 1000, 5, tiers);
    // 1000 * 5 * (1 - 0.10) = 4500.0 -> round_half_up(4500.0) = 4500
    expect(cart.lineTotalCents("sku1")).toBe(4500);
  });

  it("(c) a line meeting a higher tier gets the higher discount, not the lower one it also qualifies for", () => {
    const cart = new Cart();
    cart.addItem("sku1", 1000, 10, tiers);
    // qualifies for both minQuantity:5 (10% off) and minQuantity:10 (20% off);
    // best match (highest qualifying minQuantity) wins:
    // 1000 * 10 * (1 - 0.20) = 8000.0 -> round_half_up(8000.0) = 8000
    expect(cart.lineTotalCents("sku1")).toBe(8000);
  });

  it("(d) rounding: a fractional half-cent line total rounds up per the half-up rule", () => {
    const cart = new Cart();
    cart.addItem("sku1", 999, 5, [{ minQuantity: 5, offFraction: 0.1 }]);
    // 999 * 5 = 4995; 4995 * (1 - 0.10) = 4495.5 exactly a half-cent;
    // round_half_up(4495.5) = Math.floor(4495.5 + 0.5) = Math.floor(4496.0) = 4496
    // (plain floor/truncation would give 4495 instead — this proves half-up rounding)
    expect(cart.lineTotalCents("sku1")).toBe(4496);
  });

  it("(e) subtotalCents with two products, one discounted and one not, sums correctly", () => {
    const cart = new Cart();
    cart.addItem("sku1", 1000, 5, tiers); // discounted line: 4500
    cart.addItem("sku2", 500, 3); // no tiers: flat 1500
    expect(cart.subtotalCents()).toBe(6000);
  });
});

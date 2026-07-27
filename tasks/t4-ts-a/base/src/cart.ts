import { round2 } from "./money.js";

export interface LineItem {
  sku: string;
  unitPriceUsd: number;
  qty: number;
}

/** Extended price for one line. */
export function lineTotal(item: LineItem): number {
  return round2(item.unitPriceUsd * item.qty);
}

/** Sum of every line, before tax and shipping. */
export function subtotal(items: LineItem[]): number {
  return round2(items.reduce((acc, item) => acc + lineTotal(item), 0));
}

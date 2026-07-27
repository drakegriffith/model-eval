import { round2 } from "./money.js";

/** Tax owed on a subtotal at `rate` (0.0825 = 8.25%). */
export function taxFor(subtotalUsd: number, rate: number): number {
  return round2(subtotalUsd * rate);
}

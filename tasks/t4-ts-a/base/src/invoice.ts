import { round2 } from "./money.js";
import { subtotal, type LineItem } from "./cart.js";
import { taxFor } from "./tax.js";

export interface Invoice {
  id: string;
  items: LineItem[];
  taxRate: number;
  shippingUsd: number;
}

/** Subtotal + tax on the subtotal + shipping. Shipping is not taxed. */
export function invoiceTotal(invoice: Invoice): number {
  const sub = subtotal(invoice.items);
  return round2(sub + taxFor(sub, invoice.taxRate) + invoice.shippingUsd);
}

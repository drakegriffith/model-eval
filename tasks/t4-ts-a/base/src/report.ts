import { round2, formatUsd } from "./money.js";
import { invoiceTotal, type Invoice } from "./invoice.js";

/** Sum of every invoice total. */
export function grandTotal(invoices: Invoice[]): number {
  return round2(invoices.reduce((acc, inv) => acc + invoiceTotal(inv), 0));
}

/** Mean invoice total. An empty batch averages to zero. */
export function averageTotal(invoices: Invoice[]): number {
  if (invoices.length === 0) return 0;
  return round2(grandTotal(invoices) / invoices.length);
}

/** One-line human summary of a batch. */
export function summaryLine(invoices: Invoice[]): string {
  return `${invoices.length} invoices, ${formatUsd(grandTotal(invoices))} total, ${formatUsd(averageTotal(invoices))} average`;
}

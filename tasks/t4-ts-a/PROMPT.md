# Ticket: migrate the money package from floating-point dollars to integer cents

You are given a small TypeScript package (invoice pricing) at the root of your
working directory. Make `bash verify.sh` exit 0.

Do not modify any file under `test/`. Do not weaken, delete, or skip any test.

Run the visible tests with:

```
npm install
npm test
```

## 1. Problem statement

Every amount in `src/` is a floating-point dollar figure, so totals drift
(`0.1 + 0.2 !== 0.3`) and rounding happens in five different places with no
stated rule. Migrate the whole package to **integer cents**: one integer
representation internally, one conversion at the input boundary, one
formatting function at the output boundary, and one documented rounding rule
used everywhere rounding is unavoidable.

The migration touches **every file in `src/`**. A module left in dollars will
still compile and will still look right in isolation.

## 2. How you are graded

**`verify.sh` runs two suites: the visible one in `test/`, and a hidden
acceptance suite you cannot read.** Both must pass. The hidden suite tests
exactly what is written below — nothing beyond it. It reports pass/fail
counts only: no test names, no assertion detail.

Green on the visible suite is necessary but not sufficient.

## 3. The target API

**`src/money.ts`**
- `round2` is **deleted**.
- `toCents(dollars: number): number` — the only dollars→cents boundary. Returns
  an integer.
- `formatUsd(cents: number): string` — takes **cents**, renders `"$1,234.56"`,
  with `,` thousands grouping, always two decimal places, negatives as
  `"-$1,234.56"`, zero as `"$0.00"`.

**`src/cart.ts`**
- `LineItem` becomes `{ sku: string; unitPriceCents: number; qty: number }`.
- `lineTotal(item): number` and `subtotal(items): number` return integer cents.

**`src/tax.ts`**
- `taxFor(subtotalCents: number, rate: number): number` returns integer cents.

**`src/invoice.ts`**
- `Invoice` becomes `{ id, items, taxRate, shippingCents }`.
- `invoiceTotal(invoice): number` returns integer cents. Shipping is **not**
  taxed.

**`src/report.ts`**
- `grandTotal(invoices): number` and `averageTotal(invoices): number` return
  integer cents. An empty batch gives `0` from both.
- `summaryLine(invoices): string` is unchanged in shape and must keep
  rendering through `formatUsd`.

## 4. The rounding rule

Rounding happens in exactly two places — `taxFor` and `averageTotal` — plus
the `toCents` boundary. All three use the **same** rule:

> **Round half away from zero.** `0.5 → 1`, `1.5 → 2`, `-0.5 → -1`,
> `-1.5 → -2`.

This is **not** what bare `Math.round` does: `Math.round(-0.5)` is `-0` and
`Math.round(-1.5)` is `-1`. Negative amounts are legal throughout (refund
lines, credits), so the sign case is load-bearing, not theoretical.

Everywhere else, cents arithmetic is exact integer arithmetic and must not
round at all. `lineTotal`, `subtotal`, `invoiceTotal` and `grandTotal` combine
integers and stay integers — no intermediate float, no `round2`-style
`* 100 / 100` anywhere.

## 5. Implementation decisions

Every exported function returns an integer when it returns money —
`Number.isInteger` must hold on every one of them. Keep the existing module
layout, the existing `.js` extension on relative imports (ESM), and the
existing export names except where section 3 renames them. No new
dependencies. No runtime validation, no branded types required, no currency
other than USD.

## 6. Out of scope

No persistence, no i18n, no currency conversion, no `Intl.NumberFormat`
requirement (either implementation of `formatUsd` is fine as long as the
output strings match), no CLI.

# Ticket: bulk quantity discounts on cart line totals

## Problem statement

Marketing wants quantity-based bulk discounts per product (e.g. "buy 5+ of
this item, get 10% off that line; buy 10+, get 20% off") but `subtotalCents`
today is a flat unit-price × quantity sum with no discount concept at all.
Additionally, per-line discount math can produce fractional cents (e.g. 10%
off an odd price), and there's currently no defined rounding rule, so
different implementations could silently disagree on the total by a cent.

## Solution

Add a `DiscountTier` type `{ minQuantity: number; offFraction: number }`
(e.g. `{ minQuantity: 5, offFraction: 0.10 }`) and let `addItem` accept an
optional 4th parameter `discountTiers: DiscountTier[] = []`. Add a new
`lineTotalCents(productId: string): number` method that computes that
line's total AFTER applying the single BEST-MATCHING tier (the highest
`minQuantity` tier whose `minQuantity <= quantity`; if none match, no
discount) — formula:

```
round_half_up(unitPriceCents * quantity * (1 - bestTier.offFraction))
```

where "round half up" means `Math.floor(x + 0.5)` for non-negative x.
Change `subtotalCents()` to sum `lineTotalCents(productId)` across all line
items instead of the flat undiscounted sum (so it now reflects discounts).

## User stories

- As a merchandiser, I want to configure "buy 10+ get 20% off" on a product
  so bulk buyers automatically get the discount at cart total time.
- As a finance reviewer, I want the rounding rule for discounted line
  totals to be deterministic and documented so cart totals reconcile
  exactly, to the cent, every time.

## Implementation decisions

- Only the SINGLE best (highest qualifying `minQuantity`) tier applies per
  line — tiers are NOT stacked/combined.
- If `discountTiers` is empty or none qualify, `lineTotalCents` equals the
  old flat `unitPriceCents * quantity` (so carts with no discounts
  configured are byte-for-byte unaffected — this is the
  backward-compatibility guarantee that keeps the existing tests passing
  unmodified).
- Rounding is per-line (round each line's total independently), not on the
  grand subtotal, then subtotal sums the already-rounded per-line totals.
- `addItem` called twice on the same `productId` should merge quantity as
  before AND keep/replace `discountTiers` with whatever was passed on the
  LATEST call for that product (last-call-wins for tiers; if a later call
  omits `discountTiers` — i.e. uses the default `[]` — that call, per its
  own default parameter value, DOES reset tiers to empty, this is
  expected/documented behavior, not a bug).
- Do not change the public signature of `removeItem` or `lineItems`.

## Testing decisions

New tests must prove:

- (a) a line below the lowest tier's `minQuantity` gets no discount.
- (b) a line meeting exactly a tier's `minQuantity` gets that tier's
  discount.
- (c) a line meeting a HIGHER tier gets the higher discount, not the lower
  one it also qualifies for (best-match, not first-match/stacking).
- (d) rounding: a specific price/quantity/discount combination that
  produces a fractional half-cent rounds up per the half-up rule (pick
  concrete numbers and hand-verify the expected cents value in the test
  itself, e.g. comment showing the arithmetic).
- (e) `subtotalCents()` with two different products, one discounted and one
  not, sums correctly to reflect only the discounted line's reduction.

## Out of scope

- No stacking multiple discount tiers on one line.
- No cart-wide/order-level discounts or coupon codes.
- No currency/locale formatting — cents-as-integer only.
- No tax calculation.

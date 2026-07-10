# shopping-cart

A small shopping cart keyed by `productId`. All money values are integer
cents (`unitPriceCents`) to avoid floating-point rounding issues.

`Cart` supports adding a line item (merging quantity on repeat calls for
the same product), removing a line, listing line items, and computing a
subtotal across the whole cart.

Run tests:

```
npm install
npm test
```

You are given a small TypeScript project (a memoizing price cache in front of a
simulated remote pricing service, plus a regional pricing façade) at the root of your
working directory. Its test suite has a failure.

Find and fix the underlying bug so that the entire test suite passes. Do not modify
any file under `test/`. Do not weaken, delete, or skip any test. Do not change the
public API of `PriceCache` or `RegionalPricingService`.

Run the tests with:

```
npm install
npm test
```

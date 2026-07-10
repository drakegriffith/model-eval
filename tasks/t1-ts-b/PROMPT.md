You are given a small TypeScript project (an in-flight async request
de-duplicator, plus an inventory stock-lookup façade built on top of it) at
the root of your working directory. Its test suite has a failure.

Find and fix the underlying bug so that the entire test suite passes. Do not
modify any file under `test/`. Do not weaken, delete, or skip any test. Do
not change the public API of `Deduplicator` or `InventoryLookupService`.

Run the tests with:

```
npm install
npm test
```

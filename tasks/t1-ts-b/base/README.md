# inventory-dedup-task

A small in-flight request de-duplicator (`Deduplicator`) plus an
`InventoryLookupService` façade that uses it to coalesce concurrent stock
lookups for the same SKU into a single backend call.

```
npm install
npm test
```

# sched-merge

Interval algebra over SCHED-88 intervals.

**Two of this codebase's conventions are inverted and both are load-bearing:**
interval ends are **inclusive**, and the vendored comparator in
`src/legacy/order.ts` returns a **positive** number when `a` sorts *first*.
`CONVENTIONS.md` is the contract; `src/legacy/order.ts` is frozen.

Run tests:

```
npm install
npm test
```

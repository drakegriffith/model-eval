"""Translate a modern op list into VAULT-7 legacy store calls.

`apply_ops(store, ops)` takes any object with the `LegacyStore` API (the
caller supplies the store — this module never constructs one and never
imports `legacy`) and a list of ops shaped:

    {"op": "set",    "key": "region", "value": "us-east"}
    {"op": "delete", "key": "region"}

and returns a report dict. The eight rules the report must obey are in
`PROMPT.md`. Three of them are implemented here:

  IMPLEMENTED
  1. One commit, at the end. Ops are staged in list order and committed once.
  2. `put` returns REJECTS, not writes. A nonzero return means the op was
     rejected, stages nothing, and does not abort the batch.
  8. Report keys are echoed verbatim from the op, never normalised.

  NOT IMPLEMENTED YET
  3. `append` deletes — `delete` ops are ignored entirely right now.
  4. `append` sees committed records only.
  5. `flush()` is the abort path.
  6. `fetch` returns `(found, value)` and `final` is read back after commit.
  7. Read back, never remember.
"""


def apply_ops(store, ops):
    """Apply `ops` to `store` and return the report described in PROMPT.md."""
    report = {
        "applied": 0,
        "rejected": [],
        "skipped": [],
        "removed": {},
        "final": {},
        "aborted": False,
    }

    for op in ops:
        if op["op"] == "set":
            if store.put(op["key"], op["value"]) != 0:
                report["rejected"].append(op["key"])

    report["applied"] = store.commit()
    return report

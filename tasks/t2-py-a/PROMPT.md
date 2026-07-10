You are given a small Python record-validation library at the root of your working
directory (package `validation`, under `src/validation/`). Its test suite has
failures because the tests already reference two functions that don't exist yet.
Implement them.

## Problem statement

Today `validate_record` only ever returns the FIRST validation error it finds.
Product wants a form UI that highlights every invalid field at once, so a single
validation pass needs to report ALL failing fields, not just the first.
Separately, some fields (e.g. `sku`, `email`) must be unique across a whole batch
of records (e.g. no two products sharing a SKU), and nothing today checks that
at all.

## Solution

Add a new `validate_record_all(record, schema) -> ValidationResult` function that
behaves like `validate_record` but does NOT stop at the first error — it collects
every field's error into `.errors` (do not change `validate_record`'s existing
fail-fast behavior; it must remain unchanged so its existing tests keep passing).

Add a new `validate_batch(records: list[dict], schema, unique_fields: list[str] = [])
-> list[ValidationResult]` that runs `validate_record_all` on every record AND
additionally appends a duplicate-value error (e.g.
`"duplicate value for 'sku': 'ABC'"`) to the `ValidationResult` of every record
(not just the second one) whose value for any field named in `unique_fields`
collides with another record in the same batch.

## User stories

As a form-builder developer, I want `validate_record_all` so I can show a user
every mistake in their submission at once instead of one at a time.

As a catalog importer, I want `validate_batch(records, schema, unique_fields=["sku"])`
so duplicate SKUs across an import file are flagged on every offending row.

## Implementation decisions

- Do not change the public signature or behavior of `validate_record` (fail-fast
  stays fail-fast).
- `ValidationResult.is_valid` must be `False` whenever `.errors` is non-empty,
  for both new and old paths.
- Field validation order within `validate_record_all` should follow schema field
  order (so error message order is deterministic for tests).
- Duplicate-check comparison is by exact value equality (not case-insensitive)
  and only applies to values that are present (missing/None values are never
  considered duplicates of each other).
- `validate_batch` must not mutate the input `records` list or dicts.

## Testing decisions

New tests must prove:

- (a) `validate_record_all` returns 2+ errors when 2+ fields are invalid on the
  same record.
- (b) existing `validate_record` fail-fast tests still pass unmodified.
- (c) `validate_batch` flags duplicate SKUs on BOTH colliding records, not just
  the second.
- (d) `validate_batch` with no `unique_fields` behaves like calling
  `validate_record_all` on each record independently.
- (e) a record that is individually valid but part of a duplicate pair still
  ends up `is_valid=False` due to the duplicate error.

These tests already exist in `tests/test_validation.py` — that's why the suite
currently fails (it imports `validate_record_all` and `validate_batch`, which
don't exist in `src/validation/` yet). Do not modify `tests/`. Do not weaken,
delete, or skip any test. Implement the two functions in `src/validation/` (and
export them from the package's `__init__.py`) so the whole suite passes.

## Out of scope

No new field types beyond what already exists. No async/concurrency. No
persistence/database. No CLI wrapper — this is a library only.

Run the tests with:

```
pip install -r requirements.txt
pytest -q
```

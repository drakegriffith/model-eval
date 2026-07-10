# record-validation

A tiny record-validation library used to check plain-dict records (e.g. a
product row from a form or an import file) against a declarative `Schema`
of `Field` specs.

`validate_record(record, schema)` checks a single record and returns a
`ValidationResult` (`.is_valid`, `.errors`). It is **fail-fast**: it stops
at the first invalid field.

Run tests:

```
pip install -r requirements.txt
pytest -q
```

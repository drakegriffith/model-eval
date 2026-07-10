You are given a small Python project (an in-memory, TTL-based session store) at the
root of your working directory. Its test suite has a failure.

Find and fix the underlying bug so that the entire test suite passes. Do not modify
any file under `tests/`. Do not weaken, delete, or skip any test. Do not change the
public signatures of `SessionStore` or `Session`.

Run the tests with:

```
pip install -r requirements.txt
pytest -q
```

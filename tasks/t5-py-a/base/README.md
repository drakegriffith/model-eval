# vaultstore

An adapter from a modern op list to the VAULT-7 legacy record store.

**The legacy API is inverted in several places and the inversions are
load-bearing.** `src/vaultstore/legacy.py` is vendored and frozen; its
docstrings are the contract. In short:

- `put()` returns the number of records **rejected**. `0` means success.
- `append()` **deletes** — it is the 1970s tape-format name for appending a
  deletion marker. There is no delete method.
- `flush()` **discards** the staging area. `commit()` is the write.
- `fetch()` returns `(found, value)` — the flag comes **first**.
- `commit()` folds keys to lower case and truncates `str` values to 32
  characters, silently.

Do not modify `legacy.py`. The graders construct their own instance of the
same store and hand it to `apply_ops`, so edits to that file are not used.

Run tests:

```
pip install -r requirements.txt
pytest -q
```

# ledger

An append-only posting ledger.

Every posting is permanent. A mistake is corrected by posting a compensating
`reverse()` entry, never by editing or deleting the original — both rows stay
in the audit trail and only the balance nets out. Multiple postings to the
same account on the same date are normal.

Run tests:

```
pip install -r requirements.txt
pytest -q
```

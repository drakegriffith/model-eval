"""A double-entry-ish posting ledger.

A `Book` records `Entry` rows against accounts. Every posting is permanent:
the ledger is append-only, so a mistake is corrected by posting a
compensating `reverse()` entry rather than by editing or removing the
original. Both rows stay in the audit trail forever; only the *balance*
nets to zero.

Multiple postings to the same account on the same date are normal and
expected (a day's worth of coffee receipts, a same-day charge and its
reversal, a correction posted minutes after the mistake). Dates are opaque
`YYYY-MM-DD` strings; this module never parses or compares them as dates,
it only groups by their exact string value.

`latest(account, date)` is a convenience index over the most recent posting
for one account-day — it is a lookup helper, not the ledger itself.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    """One immutable posting. `entry_id` is assigned by the Book, starting at 1."""

    entry_id: int
    account: str
    date: str
    amount_cents: int
    memo: str = ""


class Book:
    def __init__(self):
        # Index: (account, date) -> the most recent Entry posted for that
        # account on that date. Used by latest().
        self._latest: dict[tuple[str, str], Entry] = {}
        self._next_id = 1

    def post(self, account: str, date: str, amount_cents: int, memo: str = "") -> Entry:
        """Append a posting and return it. Amounts are integer cents, signed."""
        entry = Entry(
            entry_id=self._next_id,
            account=account,
            date=date,
            amount_cents=amount_cents,
            memo=memo,
        )
        self._next_id += 1
        self._latest[(account, date)] = entry
        return entry

    def reverse(self, entry: Entry) -> Entry:
        """Post the compensating entry for `entry`. The original is untouched."""
        return self.post(
            entry.account,
            entry.date,
            -entry.amount_cents,
            memo="reversal of #%d" % entry.entry_id,
        )

    def latest(self, account: str, date: str) -> Entry | None:
        """The most recent posting for one account-day, or None."""
        return self._latest.get((account, date))

    def entries(self, account: str | None = None) -> list[Entry]:
        """Every posting ever made, oldest first. Filtered by account if given."""
        rows = sorted(self._latest.values(), key=lambda e: e.entry_id)
        if account is not None:
            rows = [e for e in rows if e.account == account]
        return rows

    def balance(self, account: str) -> int:
        """Net cents across every posting for `account`."""
        return sum(e.amount_cents for e in self.entries(account))

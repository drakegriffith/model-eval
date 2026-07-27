import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ledger import Book


def test_post_returns_entry_with_incrementing_ids():
    b = Book()
    first = b.post("cash", "2026-01-01", 1000, memo="opening")
    second = b.post("cash", "2026-01-02", 250, memo="coffee")
    assert first.entry_id == 1
    assert second.entry_id == 2
    assert first.amount_cents == 1000


def test_balance_sums_postings_across_dates():
    b = Book()
    b.post("cash", "2026-01-01", 1000)
    b.post("cash", "2026-01-02", -250)
    assert b.balance("cash") == 750


def test_balance_is_isolated_per_account():
    b = Book()
    b.post("cash", "2026-01-01", 1000)
    b.post("fees", "2026-01-01", -300)
    assert b.balance("cash") == 1000
    assert b.balance("fees") == -300


def test_latest_returns_most_recent_posting_for_that_account_day():
    b = Book()
    b.post("cash", "2026-01-01", 1000, memo="first")
    newer = b.post("cash", "2026-01-01", 400, memo="second")
    assert b.latest("cash", "2026-01-01") == newer
    assert b.latest("cash", "2026-01-09") is None


def test_reversal_restores_balance():
    b = Book()
    charge = b.post("cash", "2026-01-01", 5000, memo="duplicate charge")
    b.reverse(charge)
    assert b.balance("cash") == 0

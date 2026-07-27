"""Hidden acceptance suite for tasks/t4-py-a.

This file never ships inside `base/`, so the candidate cannot read it. It is
run by `verify.sh` with `--tb=no`, which means a failure reports only the
test's node id — the candidate learns *that* something is wrong, never *what*
the assertion said. That is the anti-over-fitting mechanism for this tier:
grinding the visible suite to green is not sufficient.

It imports the candidate's package out of the working copy (`./src`), captured
at import time before any test can change the working directory.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path.cwd() / "src"))

from ledger import Book  # noqa: E402


def test_ac_01():
    b = Book()
    b.post("cash", "2026-01-01", 1000)
    b.post("cash", "2026-01-01", 500)
    assert b.balance("cash") == 1500


def test_ac_02():
    b = Book()
    charge = b.post("cash", "2026-01-01", 5000, memo="duplicate charge")
    b.reverse(charge)
    rows = b.entries("cash")
    assert len(rows) == 2
    assert [e.amount_cents for e in rows] == [5000, -5000]
    assert rows[0].entry_id == charge.entry_id
    assert rows[0].memo == "duplicate charge"


def test_ac_03():
    b = Book()
    big = b.post("cash", "2026-01-01", 1000)
    b.post("cash", "2026-01-01", 500)
    b.reverse(big)
    assert b.balance("cash") == 500
    assert len(b.entries("cash")) == 3


def test_ac_04():
    b = Book()
    b.post("cash", "2026-01-02", 1)
    b.post("fees", "2026-01-01", 2)
    b.post("cash", "2026-01-01", 3)
    assert [e.entry_id for e in b.entries()] == [1, 2, 3]
    assert [e.entry_id for e in b.entries("cash")] == [1, 3]


def test_ac_05():
    b = Book()
    charge = b.post("cash", "2026-01-01", 700)
    undo = b.reverse(charge)
    b.reverse(undo)
    assert b.balance("cash") == 700
    assert len(b.entries("cash")) == 3


def test_ac_06():
    b = Book()
    b.post("cash", "2026-01-01", 1000, memo="first")
    newer = b.post("cash", "2026-01-01", 400, memo="second")
    assert b.latest("cash", "2026-01-01") == newer
    assert b.latest("cash", "2026-01-02") is None


def test_ac_07():
    b = Book()
    for i in range(10):
        b.post("cash", "2026-03-04", 11 * (i + 1))
    assert b.balance("cash") == sum(11 * (i + 1) for i in range(10))
    assert len(b.entries("cash")) == 10


def test_ac_08():
    b = Book()
    charge = b.post("cash", "2026-01-01", 250)
    undo = b.reverse(charge)
    assert str(charge.entry_id) in undo.memo
    assert undo.account == charge.account
    assert undo.date == charge.date


def test_ac_09():
    b = Book()
    entry = b.post("cash", "2026-01-01", 100)
    try:
        entry.amount_cents = 999
    except Exception:
        pass
    else:
        raise AssertionError("Entry must stay immutable")
    assert b.balance("cash") == 100


def test_ac_10():
    b = Book()
    b.post("cash", "2026-01-01", 100)
    b.post("cash", "2026-01-01", -100)
    b.post("cash", "2026-01-02", 40)
    assert b.balance("cash") == 40
    assert b.balance("nosuch") == 0
    assert b.entries("nosuch") == []

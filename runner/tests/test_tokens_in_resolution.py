"""test_tokens_in_resolution.py — pins `tables.resolve_tokens_in` and the ledger
join it routes into (`usage_ledger.recovered_tokens_in`). Ticket 31 AC#3.

WHY THIS FILE EXISTS. It was written to close four surviving mutants found on
2026-07-30, when the whole reader-gate suite was mutation-checked for the first
time. `corpus_gates`' predicates were well pinned — widening `summarizable` to
admit any exit_reason turned four tests red. The function those predicates feed,
which is the only thing standing between a published $/task and a number
undercounted 30x-400x, was pinned by nothing at all:

  * making the recoverable branch fall back to the row's own raw `tokens_in`  — GREEN
  * making a ledger MISS resolve to 0 instead of None                          — GREEN
  * making a quarantined row resolve to 0 instead of None                      — GREEN
  * making the ledger offer its `unfixable_floor_only` rows as recovered       — GREEN

Every one of those is the same failure in a different place: a number that is
not a measurement gets handed to a consumer that cannot tell. $/task is now
load-bearing (it is the one cell that still needs the input axis at all), so the
routing gets tests of its own rather than inheriting confidence from the
predicate tests upstream of it.

The three dispositions, and the answer each one owes:

  measured             -> the row's own number, and the ledger is not consulted
  recovered_in_ledger  -> the LEDGER's number; the row's own is the pre-fix
                          undercount and must never be the fallback
  anything else        -> None, which is not 0. A cell with no input number
                          renders `unavailable`; a cell that averaged in a zero
                          renders a plausible, wrong, cheap-looking dollar figure.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import tables  # noqa: E402
import usage_ledger  # noqa: E402

RESULTS = os.path.join(RUNNER_DIR, "results", "results.jsonl")
USAGE = os.path.join(RUNNER_DIR, "results", "usage.jsonl")

# --------------------------------------------------------------------------- #
# THE DECISION RULE -- INDEPENDENT COPY
# --------------------------------------------------------------------------- #
# DO NOT DRY THESE against corpus_gates / usage_ledger. Same standing reason as
# the block in tests/test_reader_token_gates.py (harness #5): a checker that
# imports its expected answer from the module under inspection cannot detect a
# widened rule. `EXPECTED_LEDGER_RECOVERABLE_STATUS` in particular is the literal
# `recovered_tokens_in` filters on -- restated here so that dropping the filter,
# and with it the distinction between a re-parsed measurement and a floor, fails.
EXPECTED_USABLE_STATUS = "measured"
EXPECTED_RECOVERABLE_STATUS = "recovered_in_ledger"
EXPECTED_LEDGER_RECOVERABLE_STATUS = "measured"
EXPECTED_LEDGER_UNRECOVERABLE_STATUS = "unfixable_floor_only"

ROW_TOKENS_IN = 1000        # what the row itself claims
LEDGER_TOKENS_IN = 400_000  # what the re-parse found -- deliberately far apart,
                            # so a fallback to the row cannot pass by coincidence


def row(**kw):
    base = {"run_id": "r1", "exit_reason": "ok", "tokens_in": ROW_TOKENS_IN,
            "tokens_out": 500, "tokens_in_status": EXPECTED_USABLE_STATUS}
    base.update(kw)
    return base


LEDGER = {"r1": LEDGER_TOKENS_IN}


# --------------------------------------------------------------------------- #
# 1. measured -- the row is the truth and the ledger is not consulted.
# --------------------------------------------------------------------------- #

def test_a_measured_row_resolves_to_its_own_number():
    assert tables.resolve_tokens_in(row(), LEDGER) == ROW_TOKENS_IN


def test_a_measured_row_ignores_the_ledger_even_when_they_disagree():
    """The ledger's re-parse is a recovery path, not an override. A measured row
    already holds the cache-inclusive total; silently preferring the ledger would
    make the published number depend on which file was read last."""
    assert tables.resolve_tokens_in(row(), {"r1": 999_999}) == ROW_TOKENS_IN


def test_a_measured_row_with_a_non_numeric_count_resolves_to_none():
    """Fail closed on a malformed row rather than propagating a string into an
    arithmetic mean."""
    assert tables.resolve_tokens_in(row(tokens_in=None), LEDGER) is None
    assert tables.resolve_tokens_in(row(tokens_in="19.4k"), LEDGER) is None


# --------------------------------------------------------------------------- #
# 2. recovered_in_ledger -- the ledger's number, NEVER the row's own.
# --------------------------------------------------------------------------- #

def test_a_recoverable_row_resolves_to_the_ledger_not_to_itself():
    """MUTANT KILLED: `return row.get("tokens_in")` on this branch. The row's own
    number is the pre-fix undercount -- that is the entire meaning of the
    `recovered_in_ledger` label -- so falling back to it hands the consumer the
    exact number the whole ticket exists to stop publishing, and does it under a
    label that says the value was recovered."""
    r = row(tokens_in_status=EXPECTED_RECOVERABLE_STATUS)
    got = tables.resolve_tokens_in(r, LEDGER)
    assert got == LEDGER_TOKENS_IN
    assert got != r["tokens_in"]


def test_a_recoverable_row_with_no_ledger_entry_resolves_to_none_not_zero():
    """MUTANT KILLED: `ledger.get(run_id, 0)`. A missing join is an absence of
    evidence, not a measurement of zero -- and zero is the most dangerous wrong
    answer available here, because it prices the row at the floor and drags a
    $/task mean toward "cheap" instead of dropping the row and saying so."""
    got = tables.resolve_tokens_in(
        row(run_id="not-in-the-ledger",
            tokens_in_status=EXPECTED_RECOVERABLE_STATUS), LEDGER)
    assert got is None
    assert got != 0


# --------------------------------------------------------------------------- #
# 3. everything else -- None, and None is not 0.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status", ["quarantined", "", None, "unheard_of_status"])
def test_a_row_that_is_neither_measured_nor_recoverable_resolves_to_none(status):
    """MUTANT KILLED: `return 0` as the fallthrough. Includes a status nobody has
    ever written, so a new disposition added upstream is refused by default
    instead of falling into whichever branch is written last."""
    got = tables.resolve_tokens_in(row(tokens_in_status=status), LEDGER)
    assert got is None
    assert got != 0


def test_an_unstamped_row_resolves_to_none():
    r = row()
    del r["tokens_in_status"]
    assert tables.resolve_tokens_in(r, LEDGER) is None


def test_none_and_zero_are_distinguishable_to_the_caller():
    """The consumer contract, asserted rather than assumed: table 6 drops a row
    when this function returns None and prices it when it returns a number. A
    genuine zero-input row would be priced at zero, which is why the absent case
    may not be spelled the same way."""
    absent = tables.resolve_tokens_in(row(tokens_in_status="quarantined"), LEDGER)
    real_zero = tables.resolve_tokens_in(row(tokens_in=0), LEDGER)
    assert absent is None
    assert real_zero == 0
    assert absent is not real_zero


# --------------------------------------------------------------------------- #
# 4. The other half of the join: what the ledger is willing to offer.
# --------------------------------------------------------------------------- #

def write_ledger(tmp_path, rows):
    p = tmp_path / "usage.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return str(p)


def test_the_ledger_offers_only_its_re_parsed_measurements(tmp_path):
    """MUTANT KILLED: dropping the `retrofit_status == "measured"` filter from
    `recovered_tokens_in`. `unfixable_floor_only` is the ledger saying it could
    not recover the value either -- its `tokens_in` is a floor derived from what
    survived, not a measurement. Offering it would produce a row that looks
    recovered, resolves to a number, and is wrong in the same direction as the
    original bug: too low.
    """
    path = write_ledger(tmp_path, [
        {"run_id": "good", "tokens_in": 400_000,
         "retrofit_status": EXPECTED_LEDGER_RECOVERABLE_STATUS},
        {"run_id": "floor", "tokens_in": 1_200,
         "retrofit_status": EXPECTED_LEDGER_UNRECOVERABLE_STATUS},
    ])
    offered = usage_ledger.recovered_tokens_in(path)
    assert offered == {"good": 400_000}
    assert "floor" not in offered


def test_the_ledger_ignores_non_worker_rows(tmp_path):
    """usage.jsonl also carries judge spend. Joining a judge's tokens onto a
    worker run_id would price the wrong subject."""
    path = write_ledger(tmp_path, [
        {"run_id": "w", "tokens_in": 400_000, "retrofit_status": "measured"},
        {"run_id": "w", "tokens_in": 9, "retrofit_status": "measured",
         "kind": "judge"},
    ])
    assert usage_ledger.recovered_tokens_in(path) == {"w": 400_000}


def test_a_ledger_row_with_a_non_numeric_count_is_not_offered(tmp_path):
    path = write_ledger(tmp_path, [
        {"run_id": "a", "tokens_in": None, "retrofit_status": "measured"},
        {"run_id": "b", "tokens_in": "lots", "retrofit_status": "measured"},
    ])
    assert usage_ledger.recovered_tokens_in(path) == {}


def test_a_missing_ledger_file_is_an_empty_join_not_a_crash(tmp_path):
    assert usage_ledger.recovered_tokens_in(str(tmp_path / "nope.jsonl")) == {}


# --------------------------------------------------------------------------- #
# 5. The real corpus, by count. Assert presence, count the subjects.
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def corpus():
    if not os.path.exists(RESULTS):
        pytest.skip(f"no corpus at {RESULTS}")
    return [json.loads(l) for l in open(RESULTS, encoding="utf-8") if l.strip()]


@pytest.fixture(scope="module")
def ledger():
    if not os.path.exists(USAGE):
        pytest.skip(f"no ledger at {USAGE}")
    return usage_ledger.recovered_tokens_in(USAGE)


def test_the_corpus_resolves_exactly_where_the_audit_said_it_would(corpus, ledger):
    """The denominators under every $/task cell, pinned by count and named by
    disposition. 204 of 268 rows can be priced; 64 cannot and are dropped WITH
    their count. A silent change to any of these three numbers changes what the
    dollar column means.
    """
    priced = [r for r in corpus if tables.resolve_tokens_in(r, ledger) is not None]
    dropped = [r for r in corpus if tables.resolve_tokens_in(r, ledger) is None]
    assert len(corpus) == 268
    assert len(priced) == 204
    assert len(dropped) == 64
    assert {r["tokens_in_status"] for r in dropped} == {"quarantined"}


def test_every_recoverable_row_actually_found_its_ledger_entry(corpus, ledger):
    """Asserted positively, because the failure mode is silent: a recoverable row
    whose join misses resolves to None and is simply dropped from the mean, which
    is byte-identical to a row that was never recoverable. All 56 join today; if
    that count falls, the ledger and the corpus have drifted apart."""
    recoverable = [r for r in corpus
                   if r.get("tokens_in_status") == EXPECTED_RECOVERABLE_STATUS]
    missing = [r["run_id"] for r in recoverable if r["run_id"] not in ledger]
    assert len(recoverable) == 56
    assert missing == [], missing


def test_a_recovered_row_reads_higher_than_the_number_on_the_row(corpus, ledger):
    """The undercount, demonstrated on the real corpus rather than asserted from
    the ticket. Every recovered row's true input exceeds what the row recorded --
    if any row resolved to its own value, the join has quietly stopped happening.
    """
    recoverable = [r for r in corpus
                   if r.get("tokens_in_status") == EXPECTED_RECOVERABLE_STATUS]
    ratios = [tables.resolve_tokens_in(r, ledger) / r["tokens_in"]
              for r in recoverable if r.get("tokens_in")]
    assert len(ratios) == 56
    assert min(ratios) > 1.0, min(ratios)


def test_the_quarantined_rows_are_the_ones_with_no_copy_anywhere(corpus, ledger):
    """Named, not counted: the 64 unpriceable rows are fable's, and the reason
    they are unpriceable is that the ledger could not recover them either."""
    dropped = [r for r in corpus if tables.resolve_tokens_in(r, ledger) is None]
    assert {r["model"] for r in dropped} == {"fable"}
    assert [r["run_id"] for r in dropped if r["run_id"] in ledger] == []

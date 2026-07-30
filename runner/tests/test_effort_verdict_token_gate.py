"""test_effort_verdict_token_gate.py — pins ticket 35.

`effort_verdict.py` was named in ticket 31 AC#3 as a reader that must gate
`tokens_in`, read it ungated for three days, and was absent from
tests/test_reader_token_gates.py's READER_MODULES roster the whole time. The
wiring assertion lives there (it is the roster's job); THIS file pins the two
things that are specific to this reader:

  1. Its two `tokens_in` reads are not the same read. One is a MAGNITUDE read --
     medianned and published as `scaffold_in_median` -- and must go through
     corpus_gates. The other is a PRESENCE read: did the usage block come back
     empty at all. A pre-fix undercount is still nonzero, so quarantining does
     not change the presence test's answer on any row, and it stays ungated ON
     PURPOSE. The tests below are what make that a recorded decision rather than
     an omission the next audit re-finds and cannot classify.
  2. The verdicts are computed on `tokens_out` alone. Ticket 35 AC#6 says that is
     asserted, not assumed -- `test_verdicts_are_invariant_under_tokens_in` runs
     the classifier twice over the same rows with every `tokens_in` corrupted the
     second time and demands byte-identical verdicts.

The ladder corpus is NOT results.jsonl. It is results/ladder-*.jsonl, written by
probe_endpoints.py, never touched by ticket 31's backfill, entirely recorded
before the parser fix, and unretrofittable (probe_endpoints retains no
transcript). Its honest disposition is 256/256 quarantined, which section 3 pins
by count.
"""
import glob
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import corpus_gates  # noqa: E402
import effort_verdict  # noqa: E402

LADDER_GLOB = os.path.join(RUNNER_DIR, "results", "ladder-*.jsonl")


def ladder_row(**kw):
    """A ladder row as probe_endpoints.py writes it: reachable, phase=ladder, and
    -- like every row in the real corpus -- carrying NO `tokens_in_status`."""
    base = {"phase": "ladder", "reachable": True, "family": "claude",
            "model_id": "m1", "effort": "low", "tokens_in": 20000,
            "tokens_out": 500, "answer_correct": True, "wall_s": 3.0}
    base.update(kw)
    return base


def ladder(model, per_tier, **kw):
    """Rows for one model: {tier: [out-token samples]} -> flat row list."""
    out = []
    for tier, samples in per_tier.items():
        for s in samples:
            out.append(ladder_row(model_id=model, effort=tier, tokens_out=s, **kw))
    return out


# --------------------------------------------------------------------------- #
# 1. The presence read: permitted, and permitted for a stated reason.
# --------------------------------------------------------------------------- #

def test_the_usage_block_is_empty_only_when_both_counts_are_zero():
    assert effort_verdict.usage_block_empty({"tokens_in": 0, "tokens_out": 0})
    assert not effort_verdict.usage_block_empty({"tokens_in": 0, "tokens_out": 1})
    assert not effort_verdict.usage_block_empty({"tokens_in": 1, "tokens_out": 0})


def test_a_missing_usage_block_reads_as_empty():
    """Fail closed the other way round from the token gate: a row with no counts
    at all is a measurement failure, not a free run worth zero tokens."""
    assert effort_verdict.usage_block_empty({})


def test_a_pre_fix_undercount_is_not_an_empty_usage_block():
    """THE reason this read stays ungated. The parser bug produced numbers that
    were 30x-400x too small -- 85, 114, 35670 -- never zero. Routing this test
    through corpus_gates would drop every ladder row as a 'measurement failure'
    and manufacture a corpus of nothing, which is a different lie from the one
    the quarantine is fixing."""
    for undercount in (85, 114, 35670, 16839):
        assert not effort_verdict.usage_block_empty(
            {"tokens_in": undercount, "tokens_out": 2128})


def test_the_presence_read_never_consults_provenance():
    """It asks whether the instrument returned anything, not whether what it
    returned is trustworthy. A quarantined row with real counts is still a row
    that ran."""
    quarantined = {"tokens_in": 85, "tokens_out": 2128,
                   "tokens_in_status": "quarantined"}
    assert not corpus_gates.tokens_in_usable(quarantined)
    assert not effort_verdict.usage_block_empty(quarantined)


# --------------------------------------------------------------------------- #
# 2. The magnitude read: gated, counted, and never published bare.
# --------------------------------------------------------------------------- #

def test_scaffold_median_is_withheld_when_no_row_carries_a_usable_tokens_in():
    rows = ladder("m1", {"low": [100, 110], "high": [400, 420]})
    built = effort_verdict.build_report(rows)
    entry = built["report"][0]
    assert entry["scaffold_in_median"] is None
    assert entry["scaffold_in_status"] == "quarantined"


def test_scaffold_median_is_published_when_the_rows_are_measured():
    """The gate is a gate, not a blanket suppression: stamped rows still report."""
    rows = ladder("m1", {"low": [100, 110], "high": [400, 420]},
                  tokens_in_status="measured")
    for i, r in enumerate(rows):
        r["tokens_in"] = [10, 20, 30, 40][i]
    built = effort_verdict.build_report(rows)
    entry = built["report"][0]
    assert entry["scaffold_in_median"] == 25
    assert entry["scaffold_in_status"] == "measured"


def test_a_recovered_in_ledger_row_is_not_usable_here_either():
    """Same trap as everywhere else: the row's OWN tokens_in is still the pre-fix
    number. The ladder has no ledger to join against, so there is no third answer
    to give -- it is withheld."""
    rows = ladder("m1", {"low": [100, 110], "high": [400, 420]},
                  tokens_in_status="recovered_in_ledger")
    entry = effort_verdict.build_report(rows)["report"][0]
    assert entry["scaffold_in_median"] is None
    assert entry["scaffold_in_status"] == "quarantined"


def test_a_partially_stamped_model_medians_only_the_usable_rows():
    """Mixed provenance must not average a clean number with a quarantined one."""
    rows = ladder("m1", {"low": [100, 110], "high": [400, 420]})
    rows[0]["tokens_in_status"] = "measured"
    rows[0]["tokens_in"] = 999
    rows[1]["tokens_in"] = 5
    entry = effort_verdict.build_report(rows)["report"][0]
    assert entry["scaffold_in_median"] == 999
    assert entry["scaffold_in_status"] == "measured"
    assert entry["scaffold_in_n"] == 1


# --------------------------------------------------------------------------- #
# 3. Counting the subjects out loud (ticket 35 AC#5).
# --------------------------------------------------------------------------- #

def test_zero_usable_rows_does_not_render_like_a_fully_usable_corpus():
    quarantined = effort_verdict.build_report(
        ladder("m1", {"low": [100, 110], "high": [400, 420]}))
    clean = effort_verdict.build_report(
        ladder("m1", {"low": [100, 110], "high": [400, 420]},
               tokens_in_status="measured"))
    assert quarantined["tokens_in_note"] != clean["tokens_in_note"]
    assert "inspected=4 kept=0 excluded=4" in quarantined["tokens_in_note"]
    assert "unstamped=4" in quarantined["tokens_in_note"]
    assert "inspected=4 kept=4 excluded=0" in clean["tokens_in_note"]


def test_an_empty_ladder_does_not_render_like_a_quarantined_one():
    """Zero rows inspected is its own result. A run that found no ladder files at
    all must not print what a run over 256 quarantined rows prints."""
    nothing = effort_verdict.build_report([])
    assert "NO ROWS INSPECTED" in nothing["tokens_in_note"]
    assert nothing["report"] == []


# --------------------------------------------------------------------------- #
# 4. The verdicts do not move (ticket 35 AC#6).
# --------------------------------------------------------------------------- #

def verdict_shape(built):
    return [{k: e[k] for k in ("model_id", "verdict", "spread", "monotone",
                               "between_cv", "within_cv", "n_per_tier",
                               "out_tokens_by_tier")}
            for e in built["report"]]


def test_verdicts_are_invariant_under_tokens_in():
    """Corrupt every input count and demand the classification is byte-identical.
    The docstring says spread is computed on output tokens; this is the assertion
    that makes it a fact about the code rather than a claim about it."""
    rows = (ladder("m1", {"low": [100, 110], "medium": [300, 320],
                          "high": [900, 940]})
            + ladder("m2", {"low": [500, 510], "high": [520, 530]}))
    before = verdict_shape(effort_verdict.build_report(rows))

    corrupted = [dict(r) for r in rows]
    for i, r in enumerate(corrupted):
        # Non-zero on purpose: zeroing BOTH counts would trip the presence check
        # and drop the row, which would be a real difference, not a token-in one.
        r["tokens_in"] = 1 + i * 7777
    after = verdict_shape(effort_verdict.build_report(corrupted))

    assert before == after


def test_a_row_with_an_empty_usage_block_is_still_dropped_and_counted():
    rows = ladder("m1", {"low": [100, 110], "high": [400, 420]})
    rows.append(ladder_row(model_id="m1", effort="high", tokens_in=0, tokens_out=0))
    built = effort_verdict.build_report(rows)
    assert built["dropped"] == 1
    assert built["report"][0]["n_per_tier"] == [2, 2]


# --------------------------------------------------------------------------- #
# 5. The real ladder corpus, dispositioned by count rather than by sample.
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def ladder_corpus():
    paths = sorted(glob.glob(LADDER_GLOB))
    if not paths:
        pytest.skip(f"no ladder corpus at {LADDER_GLOB}")
    rows = []
    for p in paths:
        rows += [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    return rows


def test_the_ladder_corpus_is_entirely_unstamped(ladder_corpus):
    """Measured 2026-07-30 and pinned. Ticket 31's backfill stamped
    results.jsonl's 268 rows and never reached this corpus; if a later backfill
    does reach it, this test is where that becomes visible instead of silently
    changing what effort_verdict publishes."""
    assert len(ladder_corpus) == 256
    stamped = [r for r in ladder_corpus if "tokens_in_status" in r]
    assert stamped == [], f"{len(stamped)} ladder rows are now stamped"


def test_every_published_scaffold_median_is_withheld_on_the_real_corpus(ladder_corpus):
    built = effort_verdict.build_report(
        [r for r in ladder_corpus if r.get("phase") == "ladder"])
    assert len(built["report"]) == 16
    assert all(e["scaffold_in_median"] is None for e in built["report"])
    assert all(e["scaffold_in_status"] == "quarantined" for e in built["report"])
    assert "kept=0" in built["tokens_in_note"]


def test_the_real_corpus_verdicts_match_the_pre_gate_capture(ladder_corpus):
    """Ticket 35 AC#6, against the actual numbers, captured from master at
    fa1559b before a line of this ticket was written.

    UPDATED 2026-07-30 by ticket 42, which split BACKWARDS out of AMBIGUOUS and
    so had to move this capture. It is REPHRASED, NOT RELAXED: the original
    capture is still asserted, in the pre-split vocabulary it was taken in, by
    mapping today's verdicts back through pre_split_verdict(). Both halves have
    to hold, which is strictly more than the single line this replaced. The
    2 models that moved are named in the transition tally below, so a future
    re-classification cannot hide inside an aggregate count."""
    built = effort_verdict.build_report(
        [r for r in ladder_corpus if r.get("phase") == "ladder"])
    counts = {}
    for e in built["report"]:
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    assert counts == {"REAL": 10, "AMBIGUOUS": 4, "BACKWARDS": 2}

    # The fa1559b capture itself, unchanged, recovered by rename.
    pre = {}
    for e in built["report"]:
        v = effort_verdict.pre_split_verdict(e["verdict"])
        pre[v] = pre.get(v, 0) + 1
    assert pre == {"REAL": 10, "AMBIGUOUS": 6}

    # Ticket 42 AC#4: which models moved, not merely how many.
    assert effort_verdict.transition_tally(
        e["verdict"] for e in built["report"]) == {
        "REAL -> REAL": 10,
        "AMBIGUOUS -> AMBIGUOUS": 4,
        "AMBIGUOUS -> BACKWARDS": 2,
    }
    assert sorted(e["model_id"] for e in built["report"]
                  if e["verdict"] == "BACKWARDS") == [
        "claude-haiku-4-5", "claude-haiku-4-5-20251001"]

    assert built["dropped"] == 7
    # Unchanged, and that is the point: BACKWARDS collapses to one frontier
    # point exactly as AMBIGUOUS did, so the split cannot move the study's
    # design out from under ticket 13.
    points = sum(len(e["tiers_probed"]) if e["verdict"] == "REAL" else 1
                 for e in built["report"])
    assert points == 53

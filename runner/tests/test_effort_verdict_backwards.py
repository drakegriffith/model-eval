"""test_effort_verdict_backwards.py — pins ticket 42's direction split.

`AMBIGUOUS` was doing two jobs: holding ladders that are genuinely
under-determined, and holding ladders that RUN THE WRONG WAY. The second is a
much stronger claim, and ticket 13 measured it on `claude-haiku-4-5-20251001`
(t1-ts-b: low 5551 -> max 3985, 28% below, monotone_score 0.0) and warned it
must never be reported as "close to real". `BACKWARDS` is that warning made
machine-readable. The rule, the value 0.95 and five falsifiable predictions were
fixed in runner/PRECOMMIT-BACKWARDS.md at 758140c, BEFORE any derivation ran.

What this file pins, in the order the sections appear:

  1. The two ticket-13 subjects, on fixtures carrying their recorded tier means.
     `claude-haiku-4-5-20251001` -> BACKWARDS; `claude-haiku-4-5` -> NO-OP. The
     second is the one that matters: a NO-OP ladder that happens to end 7% down
     must NOT be promoted into BACKWARDS, because below NOOP_SPREAD the whole
     dial is inside the noise floor and "runs backwards" claims more than the
     measurement supports.
  2. That the split is a RENAME of a slice of AMBIGUOUS and nothing else --
     checked against an INDEPENDENT transcription of the pre-split branch
     (`pre_split_reference` below) over a synthetic grid and both real corpora,
     not against a comment.
  3. Ticket 35 AC#6 re-asserted under the new vocabulary: verdicts, BACKWARDS
     among them, are invariant when every `tokens_in` in the corpus is corrupted.
  4. That `BACKWARDS_END_RATIO` is assigned in exactly one file.

HARNESS NOTE (rule 5, checker != worker). `pre_split_reference` deliberately
DUPLICATES the four pre-split thresholds instead of importing them from
`effort_verdict`. A gate that reads its rule from the file it is checking cannot
fail when that file's rule changes. The duplication IS the control here; do not
"DRY" these constants back into an import. The twin annotation sits on
`effort_verdict.pre_split_verdict()`.

Note what the reference canNOT contain: the pre-split rule has no end-ratio term
at all, so this copy cannot leak `BACKWARDS_END_RATIO` even by accident. Section
4 pins that the real constant still lives in exactly one place.
"""
import ast
import glob
import json
import os
import statistics
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import effort_verdict  # noqa: E402
import ladder_from_results  # noqa: E402

LADDER_GLOB = os.path.join(RUNNER_DIR, "results", "ladder-*.jsonl")
RESULTS_JSONL = os.path.join(RUNNER_DIR, "results", "results.jsonl")

# The six real-task ladders re-derived under the new vocabulary (ticket 42
# part 2). Sweep name -> the model id it probed.
T13_SWEEPS = {
    "t13-haiku": "claude-haiku-4-5",
    "t13-haiku-pin": "claude-haiku-4-5-20251001",
    "t13-kimi": "kimi-k3",
    "t13-luna": "gpt-5.6-luna",
    "t13-sol": "sol",
    "t13-spark": "gpt-5.3-codex-spark",
}


# --------------------------------------------------------------------------- #
# The independent copy of the pre-split rule. See the HARNESS NOTE above.
# --------------------------------------------------------------------------- #

# Transcribed from effort_verdict.classify() as it stood at 758140c~1, i.e. the
# last commit before ticket 42 added a branch. Hard-coded ON PURPOSE (harness
# rule 5): if someone retunes a threshold in effort_verdict.py, the disagreement
# is the alarm. These four are NOT ticket 42's to change -- prediction 5 says so.
PRE_SPLIT_REAL_SPREAD = 1.50
PRE_SPLIT_NOOP_SPREAD = 1.20
PRE_SPLIT_NOISE_MARGIN = 2.0
PRE_SPLIT_MIN_N = 2
PRE_SPLIT_STEP_TOLERANCE = 0.95  # monotone_score's per-step band, not an end ratio


def pre_split_reference(tiers):
    """The verdict `classify(tiers)` would have returned before ticket 42.

    A from-scratch transcription: it recomputes the means, the spread, the
    monotone score and both CVs itself rather than calling any part of
    effort_verdict. Returns one of INSUFFICIENT / UNREPLICATED / NO-OP / REAL /
    AMBIGUOUS -- the pre-split vocabulary, which has no BACKWARDS in it.
    """
    means = [statistics.mean(v) for _, v in tiers if v]
    if len(means) < 2:
        return "INSUFFICIENT"

    spread = (max(means) / min(means)) if min(means) > 0 else float("inf")

    steps = list(zip(means, means[1:]))
    mono = sum(1 for a, b in steps if b >= a * PRE_SPLIT_STEP_TOLERANCE) / len(steps)

    def _cv(vals):
        if len(vals) < 2:
            return 0.0
        mu = statistics.mean(vals)
        return (statistics.pstdev(vals) / mu) if mu else 0.0

    between = _cv(means)
    reps = [v for _, v in tiers if len(v) >= 2]
    within = statistics.mean([_cv(v) for v in reps]) if reps else None
    min_n = min(len(v) for _, v in tiers) if tiers else 0

    if min_n < PRE_SPLIT_MIN_N or within is None:
        return "UNREPLICATED"
    if spread < PRE_SPLIT_NOOP_SPREAD:
        return "NO-OP"
    if (spread >= PRE_SPLIT_REAL_SPREAD and mono >= 0.6
            and between >= PRE_SPLIT_NOISE_MARGIN * within):
        return "REAL"
    return "AMBIGUOUS"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def tiers_with_mean(means, jitter=100):
    """[(tier, [samples])] whose per-tier sample mean is exactly `means[tier]`.

    Three samples per tier, symmetric about the mean, so the recorded tier means
    survive into classify() unchanged while every tier still carries the
    replication `MIN_N_FOR_VERDICT` requires.
    """
    return [(t, [m - jitter, m, m + jitter]) for t, m in means]


# The two ticket-13 subjects, at the tier means recorded in
# results/t13-haiku-pin-ladder.json and results/t13-haiku-ladder.json (t1-ts-b).
HAIKU_PIN_T1 = [("low", 5551), ("high", 5061), ("max", 3985)]
HAIKU_T1 = [("low", 1946), ("high", 1844), ("max", 1804)]


@pytest.fixture(scope="module")
def ladder_corpus():
    paths = sorted(glob.glob(LADDER_GLOB))
    if not paths:
        pytest.skip(f"no ladder corpus at {LADDER_GLOB}")
    rows = []
    for p in paths:
        rows += [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    return [r for r in rows if r.get("phase") == "ladder"]


@pytest.fixture(scope="module")
def t13_blocks():
    """Every real-task block of the six t13 sweeps -> [(label, tiers, verdict)]."""
    if not os.path.exists(RESULTS_JSONL):
        pytest.skip(f"no results corpus at {RESULTS_JSONL}")
    out = []
    for sweep, model in sorted(T13_SWEEPS.items()):
        rows, _ = ladder_from_results.load_rows(RESULTS_JSONL, sweep, None, False)
        assert rows, f"{sweep} matched no complete rows"
        by_task = {}
        for r in rows:
            by_task.setdefault(r["task"], []).append(r)
        for task in sorted(by_task):
            tiers = ladder_from_results.tiers_for(by_task[task])
            out.append((f"{sweep}/{task}", tiers,
                        effort_verdict.classify(tiers)["verdict"]))
    return out


# --------------------------------------------------------------------------- #
# 1. The two ticket-13 subjects.
# --------------------------------------------------------------------------- #

def test_the_pinned_haiku_ladder_is_backwards():
    """Prediction 1. `claude-haiku-4-5-20251001` on t1-ts-b: the top of the dial
    spends 28% LESS than the bottom, which is the reading ticket 13 said must
    never be described as 'close to real'."""
    res = effort_verdict.classify(tiers_with_mean(HAIKU_PIN_T1))
    assert res["verdict"] == "BACKWARDS"
    assert res["end_ratio"] == 0.72
    assert effort_verdict.pre_split_verdict(res["verdict"]) == "AMBIGUOUS"


def test_the_unpinned_haiku_ladder_stays_a_noop():
    """Prediction 2, the half that belongs to THIS corpus. `claude-haiku-4-5`
    ends 7% down -- under BACKWARDS_END_RATIO -- and must still come back NO-OP,
    because its spread (1.08) never reaches NOOP_SPREAD and the branch is
    therefore never consulted. This is the test that stops BACKWARDS from
    swallowing the noise floor."""
    res = effort_verdict.classify(tiers_with_mean(HAIKU_T1))
    assert res["verdict"] == "NO-OP"
    assert res["spread"] < effort_verdict.NOOP_SPREAD
    assert res["end_ratio"] <= effort_verdict.BACKWARDS_END_RATIO, (
        "fixture no longer exercises the case it exists for: this ladder must "
        "end DOWN and still be NO-OP")


def test_backwards_carves_only_out_of_ambiguous_for_these_two():
    for label, means in (("pin", HAIKU_PIN_T1), ("unpinned", HAIKU_T1)):
        tiers = tiers_with_mean(means)
        after = effort_verdict.classify(tiers)["verdict"]
        assert effort_verdict.pre_split_verdict(after) == pre_split_reference(tiers), label


def test_the_split_needs_the_end_ratio_not_the_spread():
    """Both subjects fall; only one is BACKWARDS. If the rule keyed off spread
    (max/min, unsigned) it could not tell them apart at all."""
    pin = effort_verdict.classify(tiers_with_mean(HAIKU_PIN_T1))
    plain = effort_verdict.classify(tiers_with_mean(HAIKU_T1))
    assert pin["end_ratio"] < plain["end_ratio"] <= 1.0
    assert pin["verdict"] != plain["verdict"]


# --------------------------------------------------------------------------- #
# 2. The split is a rename: an independent pre-split copy must agree everywhere.
# --------------------------------------------------------------------------- #

def synthetic_grid():
    """Ladders spanning every branch: flat, rising, falling, noisy, unreplicated,
    single-tier, and both sides of each threshold. Built to straddle 0.95
    end-to-end so the BACKWARDS branch is genuinely exercised."""
    grid = []
    base = 1000
    # end-to-end multipliers chosen to sit either side of BACKWARDS_END_RATIO
    # (0.95) and either side of NOOP_SPREAD / REAL_SPREAD.
    for end_mult in (0.40, 0.72, 0.90, 0.94, 0.95, 0.96, 1.00, 1.05, 1.19,
                     1.25, 1.45, 1.50, 1.90, 4.00):
        for mid_mult in (0.5, 0.95, 1.0, 1.3, 2.5):
            for jitter in (0, 30, 300):
                for n in (1, 2, 3):
                    means = [base, base * mid_mult, base * end_mult]
                    tiers = []
                    for tier, m in zip(("low", "high", "max"), means):
                        samples = [m + jitter * (i - (n - 1) / 2) for i in range(n)]
                        tiers.append((tier, samples))
                    grid.append(tiers)
    # degenerate shapes: one tier, an empty tier, a zero-mean bottom tier
    grid.append([("low", [500, 510])])
    grid.append([("low", [500, 510]), ("high", [])])
    grid.append([("low", [0, 0]), ("high", [100, 110])])
    grid.append([])
    return grid


def test_pre_split_verdict_is_a_rename_over_a_synthetic_grid():
    grid = synthetic_grid()
    assert len(grid) > 600, "grid shrank; it is meant to be exhaustive over branches"
    for tiers in grid:
        after = effort_verdict.classify(tiers)["verdict"]
        assert effort_verdict.pre_split_verdict(after) == pre_split_reference(tiers), tiers


def test_the_synthetic_grid_actually_reaches_every_verdict():
    """A grid that never produced a BACKWARDS would make the test above pass for
    the wrong reason. Assert presence, do not infer it from a green run."""
    seen = {effort_verdict.classify(t)["verdict"] for t in synthetic_grid()}
    assert seen == {"INSUFFICIENT", "UNREPLICATED", "NO-OP", "REAL",
                    "AMBIGUOUS", "BACKWARDS"}, seen


def test_pre_split_verdict_is_a_rename_over_the_probe_corpus(ladder_corpus):
    """The 16-model probe corpus (results/ladder-*.jsonl), classified through
    build_report so the drop rule and the tier grouping are the real ones."""
    built = effort_verdict.build_report(ladder_corpus)
    assert len(built["report"]) == 16
    checked = 0
    for entry in built["report"]:
        rows = [r for r in ladder_corpus
                if r.get("reachable")
                and not effort_verdict.usage_block_empty(r)
                and r["family"] == entry["family"]
                and r["model_id"] == entry["model_id"]]
        by_tier = {}
        for r in rows:
            by_tier.setdefault(r["effort"], []).append(r["tokens_out"])
        tiers = [(t, by_tier[t]) for t in effort_verdict.TIER_ORDER if t in by_tier]
        assert (effort_verdict.pre_split_verdict(entry["verdict"])
                == pre_split_reference(tiers)), entry["model_id"]
        checked += 1
    assert checked == 16


def test_pre_split_verdict_is_a_rename_over_the_real_task_corpus(t13_blocks):
    assert len(t13_blocks) == 12, "expected 2 task blocks x 6 sweeps"
    for label, tiers, after in t13_blocks:
        assert effort_verdict.pre_split_verdict(after) == pre_split_reference(tiers), label


# --------------------------------------------------------------------------- #
# 3. The derived tally, and predictions 3 + 4 as executable statements.
# --------------------------------------------------------------------------- #

def test_the_real_task_corpus_tally(t13_blocks):
    """Ticket 42 part 2, derived at this commit over 268 lines of results.jsonl
    with zero new runs. One block moves, and it is the one prediction 1 named."""
    tally = effort_verdict.transition_tally(v for _, _, v in t13_blocks)
    assert tally == {
        "NO-OP -> NO-OP": 2,
        "AMBIGUOUS -> AMBIGUOUS": 5,
        "AMBIGUOUS -> BACKWARDS": 1,
        "REAL -> REAL": 4,
    }
    moved = [label for label, _, v in t13_blocks if v == "BACKWARDS"]
    assert moved == ["t13-haiku-pin/t1-ts-b"]


def test_prediction_3_every_transition_has_ambiguous_on_the_left(t13_blocks,
                                                                ladder_corpus):
    """No model leaves REAL, NO-OP, UNREPLICATED or INSUFFICIENT. Stated over
    BOTH corpora, since prediction 2 was wrong precisely by not naming one."""
    verdicts = [v for _, _, v in t13_blocks]
    verdicts += [e["verdict"] for e in
                 effort_verdict.build_report(ladder_corpus)["report"]]
    for v in verdicts:
        before = effort_verdict.pre_split_verdict(v)
        assert before == v or before == "AMBIGUOUS", v


def test_prediction_4_exactly_the_blocks_at_or_below_the_ratio_move(t13_blocks):
    """'Exactly those, and no others' -- so check both directions, including the
    0.97 near-miss that keeps this from being a one-sided test."""
    moved, stayed = [], []
    for label, tiers, after in t13_blocks:
        res = effort_verdict.classify(tiers)
        if effort_verdict.pre_split_verdict(after) != "AMBIGUOUS":
            continue
        (moved if after == "BACKWARDS" else stayed).append((label, res["end_ratio"]))
    for label, ratio in moved:
        assert ratio <= effort_verdict.BACKWARDS_END_RATIO, label
    for label, ratio in stayed:
        assert ratio > effort_verdict.BACKWARDS_END_RATIO, label
    assert any(r <= 1.0 for _, r in stayed), (
        "no near-miss left in the corpus: prediction 4 is only meaningful while "
        "some AMBIGUOUS block ends down but above the ratio")


def test_prediction_5_the_four_pre_existing_thresholds_are_unmoved():
    assert effort_verdict.REAL_SPREAD == 1.50
    assert effort_verdict.NOOP_SPREAD == 1.20
    assert effort_verdict.NOISE_MARGIN == 2.0
    assert effort_verdict.MIN_N_FOR_VERDICT == 2
    assert effort_verdict.BACKWARDS_END_RATIO == 0.95


# --------------------------------------------------------------------------- #
# 4. Ticket 35's invariance, re-asserted rather than inherited.
# --------------------------------------------------------------------------- #

def test_verdicts_including_backwards_survive_corrupted_tokens_in(ladder_corpus):
    """Ticket 35 AC#6 under the new vocabulary. The split reads `tokens_out`
    only; the ladder corpus's input counts are 256/256 quarantined, so a verdict
    that moved when they were corrupted would be reading a number nobody can
    stand behind."""
    clean = effort_verdict.build_report(ladder_corpus)
    corrupted_rows = []
    for i, r in enumerate(ladder_corpus):
        c = dict(r)
        # Corruption preserves ZERONESS, and that is the whole subtlety. The
        # MAGNITUDE of tokens_in reaches nothing; its PRESENCE reaches
        # usage_block_empty, which is a documented ungated read. Ticket 35's
        # licence for that read is that a pre-fix undercount is never zero, so
        # no real quarantine decision can flip the predicate -- a corruption
        # that invented input tokens for a row that reported none would be
        # simulating something the bug could not do, and would resurrect the 7
        # measurement failures below as if they were free runs worth 0 tokens.
        c["tokens_in"] = 0 if not (r.get("tokens_in") or 0) else 7 * (i + 1) + 999_999
        corrupted_rows.append(c)
    dirty = effort_verdict.build_report(corrupted_rows)

    assert [e["verdict"] for e in clean["report"]] == \
           [e["verdict"] for e in dirty["report"]]
    assert [e["end_ratio"] for e in clean["report"]] == \
           [e["end_ratio"] for e in dirty["report"]]
    assert clean["dropped"] == dirty["dropped"]
    assert "BACKWARDS" in {e["verdict"] for e in clean["report"]}, (
        "invariance over a corpus with no BACKWARDS in it would not test the "
        "new state at all")
    # The input axis stays withheld either way: corrupting a quarantined number
    # must not talk it into being publishable.
    assert all(e["scaffold_in_median"] is None for e in dirty["report"])


def test_the_presence_read_is_where_tokens_in_still_bites(ladder_corpus):
    """The boundary of the test above, recorded rather than left implicit.

    Found while writing it: on the REAL corpus (unlike the synthetic rows in
    test_effort_verdict_token_gate.py) 7 rows report a wholly empty usage block,
    and inventing a nonzero `tokens_in` for them un-drops all 7 and moves a
    verdict -- claude-opus-5[1m] BACKWARDS -> AMBIGUOUS, because zero-token rows
    then drag a tier mean down. That is not a leak of `tokens_in` into the
    classifier; it is usage_block_empty doing exactly the job its docstring
    claims, on an input the parse_usage bug could never have produced. Pinned so
    the next audit reads a decision instead of re-finding a surprise."""
    empty = [r for r in ladder_corpus if effort_verdict.usage_block_empty(r)]
    # 17 rows report an empty usage block, but 10 of them are unreachable and
    # build_report skips those BEFORE it asks about usage. `dropped` counts the
    # remaining 7 -- the reachable measurement failures. Both numbers are pinned
    # so a change in either is legible; asserting only the 7 would let a corpus
    # with different reachability arithmetic pass as unchanged.
    assert len(empty) == 17
    reachable_empty = [r for r in empty if r.get("reachable")]
    assert len(reachable_empty) == 7
    assert effort_verdict.build_report(ladder_corpus)["dropped"] == 7
    assert all(not (r.get("tokens_out") or 0) for r in empty), (
        "an empty usage block is empty in BOTH counts; if that stops holding, "
        "the corruption above is no longer preserving the right predicate")

    resurrected = [dict(r, tokens_in=12345) if effort_verdict.usage_block_empty(r)
                   else dict(r) for r in ladder_corpus]
    built = effort_verdict.build_report(resurrected)
    assert built["dropped"] == 0
    assert ([e["verdict"] for e in built["report"]]
            != [e["verdict"] for e in effort_verdict.build_report(ladder_corpus)["report"]])


def test_real_task_verdicts_survive_corrupted_tokens_in(t13_blocks):
    """The same property for the real-task corpus, where the classifier is fed
    through ladder_from_results.tiers_for -- which must never reach tokens_in."""
    for label, tiers, after in t13_blocks:
        assert effort_verdict.classify(tiers)["verdict"] == after, label


# --------------------------------------------------------------------------- #
# 5. One home for the constant.
# --------------------------------------------------------------------------- #

def test_backwards_end_ratio_is_assigned_in_exactly_one_file():
    """Ticket 45's badges import it; nothing restates it. A second assignment is
    a second pre-commitment, and the two would drift silently."""
    homes = []
    for path in sorted(glob.glob(os.path.join(RUNNER_DIR, "**", "*.py"),
                                 recursive=True)):
        if os.sep + "__pycache__" + os.sep in path:
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        if "BACKWARDS_END_RATIO" not in src:
            continue
        for node in ast.walk(ast.parse(src, filename=path)):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            if any(isinstance(t, ast.Name) and t.id == "BACKWARDS_END_RATIO"
                   for t in targets):
                homes.append(os.path.relpath(path, RUNNER_DIR))
    assert homes == ["effort_verdict.py"], homes

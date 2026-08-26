"""test_reader_token_gates.py — pins runner/corpus_gates.py (ticket 31 AC#3,
ticket 34 carryover).

WHY THIS FILE IS THE GATE AND corpus_gates.py IS NOT. corpus_gates holds the two
predicates every reader calls; it cannot check itself. Ticket 34's objection to
copying a rule into five readers was drift — five copies, nothing comparing them.
The answer was one copy plus this file, which restates the two literals
INDEPENDENTLY so the module under inspection is not the source of the answer it
is being checked against (harness #5).

THE THREE THINGS ASSERTED, in the order they can fail:

  1. The predicates say what they are supposed to say, including the middle
     state. `recovered_in_ledger` is NOT usable on the row, and that single
     assertion is the whole point of the file: a reader that treats it as usable
     reads the 30x-400x undercount while believing it has been corrected.
  2. The readers are actually WIRED to them. A predicate module with zero
     importers passes every test about its predicates and gates nothing —
     byte-identical to one that is doing its job (harness: silence is not
     evidence). `test_every_reader_is_wired_to_the_shared_predicates` asserts
     presence, by name, over a literal roster.
  3. The real corpus is fully dispositioned. Every row carries both fields, and
     the counts are named rather than sampled, so "the gate inspected 268 rows"
     is distinguishable from "the gate inspected whatever it was handed".
"""
import ast
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import corpus_gates  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
# Frozen, not live: the audit's counts below (268, 267, 148...) are named
# against the corpus as it stood at dd8f532. The live results.jsonl grows
# (PR #37 added 5 rows and pushed it to 273) without re-running the audit,
# so this file must not read the live corpus -- see runner/tests/fixtures/
# results-268.jsonl and issue #43.
RESULTS = os.path.join(TESTS_DIR, "fixtures", "results-268.jsonl")

# --------------------------------------------------------------------------- #
# THE DECISION RULE -- INDEPENDENT COPY
# --------------------------------------------------------------------------- #
# DO NOT DRY THESE. Both literals are restated from runner/corpus_gates.py on
# purpose and the duplication IS the control (harness #5: the checker must not
# learn its answer from the module it is checking). Replacing either with
# `corpus_gates.SUMMARIZABLE_EXIT_REASON` / `.TOKENS_IN_USABLE_STATUS` makes a
# widened predicate — say, one that also admits "recovered_in_ledger" — pass this
# suite green. Reject that tidy-up; this comment is the standing reason.
EXPECTED_SUMMARIZABLE_EXIT_REASON = "ok"
EXPECTED_TOKENS_IN_USABLE_STATUS = "measured"
EXPECTED_TOKENS_IN_RECOVERABLE_STATUS = "recovered_in_ledger"

# The roster for the wiring assertion. Every reader that reports a number derived
# from `pass` or from `tokens_in` belongs here. Adding a reader without adding it
# to this line is the failure mode this roster exists to make loud: the new
# reader would be ungated and nothing would say so.
#
# effort_verdict.py joined 2026-07-30 (ticket 35). It was named in ticket 31 AC#3
# alongside the other three, read `tokens_in` ungated the whole time, and this
# roster reported three passes and zero failures over its absence -- the exact
# failure the comment above describes, happening on the roster's own watch. It
# reads a DIFFERENT corpus (results/ladder-*.jsonl, written by
# probe_endpoints.py), so nothing asserted about results.jsonl could have caught
# it; only the roster could, and the roster did not contain it.
READER_MODULES = ("calibration_report.py", "effort_verdict.py", "stats.py",
                  "tables.py")


def row(**kw):
    """A results row with both provenance fields present and clean by default,
    so each test below mutates exactly the one field it is about."""
    base = {"run_id": "r1", "exit_reason": "ok", "tokens_in": 1000,
            "tokens_out": 500, "tokens_in_status": "measured"}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# 1. The two literals, and the two predicates over them.
# --------------------------------------------------------------------------- #

def test_the_modules_literals_match_this_files_independent_copies():
    """The harness #5 control. A widened predicate fails here first."""
    assert corpus_gates.SUMMARIZABLE_EXIT_REASON == EXPECTED_SUMMARIZABLE_EXIT_REASON
    assert corpus_gates.TOKENS_IN_USABLE_STATUS == EXPECTED_TOKENS_IN_USABLE_STATUS
    assert (corpus_gates.TOKENS_IN_RECOVERABLE_STATUS
            == EXPECTED_TOKENS_IN_RECOVERABLE_STATUS)


def test_only_a_clean_exit_is_summarizable():
    assert corpus_gates.summarizable(row(exit_reason="ok"))
    for bad in ("cli_error", "no_completion", "timeout", "cap_exhausted"):
        assert not corpus_gates.summarizable(row(exit_reason=bad)), bad


def test_an_unstamped_row_is_not_summarizable():
    """Fail closed. A row nobody dispositioned is not the same thing as a clean
    one, and `.get()` returning None must not read as a pass."""
    r = row()
    del r["exit_reason"]
    assert not corpus_gates.summarizable(r)
    assert not corpus_gates.summarizable(row(exit_reason=None))


def test_only_measured_is_usable_on_the_row():
    """THE load-bearing assertion. `recovered_in_ledger` names a row whose OWN
    `tokens_in` is still the pre-fix undercount — the true value is in
    usage.jsonl, joinable by run_id, and was deliberately not written back. A
    reader that admits it here reads the wrong number while believing it has been
    fixed, which is worse than reading a number it knows is quarantined.
    """
    assert corpus_gates.tokens_in_usable(row(tokens_in_status="measured"))
    assert not corpus_gates.tokens_in_usable(row(tokens_in_status="recovered_in_ledger"))
    assert not corpus_gates.tokens_in_usable(row(tokens_in_status="quarantined"))


def test_an_unstamped_row_is_not_token_usable():
    r = row()
    del r["tokens_in_status"]
    assert not corpus_gates.tokens_in_usable(r)


def test_the_two_taints_are_independent():
    """Never collapsed into one "good row" flag. A run can exit cleanly while its
    input tokens are quarantined, and vice versa; each reader gates on the taint
    that touches the number it is about to print."""
    clean_exit_dirty_tokens = row(exit_reason="ok", tokens_in_status="quarantined")
    assert corpus_gates.summarizable(clean_exit_dirty_tokens)
    assert not corpus_gates.tokens_in_usable(clean_exit_dirty_tokens)

    dirty_exit_clean_tokens = row(exit_reason="cli_error", tokens_in_status="measured")
    assert not corpus_gates.summarizable(dirty_exit_clean_tokens)
    assert corpus_gates.tokens_in_usable(dirty_exit_clean_tokens)


# --------------------------------------------------------------------------- #
# 2. Partitioning: the tally comes back whether or not anything was excluded.
# --------------------------------------------------------------------------- #

def test_partition_returns_the_tally_alongside_the_kept_rows():
    rows = [row(run_id="a"), row(run_id="b", tokens_in_status="quarantined"),
            row(run_id="c", tokens_in_status="recovered_in_ledger"),
            row(run_id="d", tokens_in_status="quarantined")]
    kept, excluded = corpus_gates.tokens_in_rows(rows)
    assert [r["run_id"] for r in kept] == ["a"]
    assert excluded == {"quarantined": 2, "recovered_in_ledger": 1}


def test_an_unstamped_exclusion_is_counted_under_its_own_name():
    """Not folded into any real status. "unstamped" is a distinct disposition and
    a reader must be able to see that it happened."""
    r = row()
    del r["tokens_in_status"]
    kept, excluded = corpus_gates.tokens_in_rows([r])
    assert kept == []
    assert excluded == {"unstamped": 1}


def test_a_clean_partition_still_returns_a_tally():
    kept, excluded = corpus_gates.summarizable_rows([row(), row(run_id="b")])
    assert len(kept) == 2
    assert excluded == {}


# --------------------------------------------------------------------------- #
# 3. Counting the subjects out loud (AC#4). Zero is a result, not a default.
# --------------------------------------------------------------------------- #

def test_zero_rows_inspected_does_not_render_like_a_clean_corpus():
    """The rule this ticket keeps tripping over: a gate that passed over zero
    subjects must not print what a gate that passed over all of them prints."""
    nothing = corpus_gates.format_exclusions("rows", 0, [], {})
    clean = corpus_gates.format_exclusions("rows", 2, [row(), row()], {})
    assert nothing != clean
    assert "NO ROWS INSPECTED" in nothing
    assert "inspected=2 kept=2 excluded=0" in clean
    assert "nothing to exclude" in clean


def test_the_exclusion_line_names_every_reason_and_its_count():
    kept, excluded = corpus_gates.tokens_in_rows(
        [row(), row(tokens_in_status="quarantined"),
         row(tokens_in_status="recovered_in_ledger")])
    text = corpus_gates.format_exclusions("rows", 3, kept, excluded)
    assert "inspected=3 kept=1 excluded=2" in text
    assert "quarantined=1" in text
    assert "recovered_in_ledger=1" in text


def test_the_markdown_note_carries_the_counts_and_the_join_key():
    kept, excluded = corpus_gates.tokens_in_rows(
        [row(), row(tokens_in_status="quarantined")])
    note = corpus_gates.tokens_in_note(2, kept, excluded)
    assert "inspected=2 kept=1 excluded=1" in note
    assert "usage.jsonl" in note and "run_id" in note


# --------------------------------------------------------------------------- #
# 4. The wiring. A predicate module nobody imports gates nothing.
# --------------------------------------------------------------------------- #

def module_imports(path):
    """Top-level names imported by `path`, read with ast so nothing executes."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("reader", READER_MODULES)
def test_every_reader_is_wired_to_the_shared_predicates(reader):
    """Assert presence, by name. Before this test corpus_gates had zero
    importers: every predicate test above passed while the published tables were
    still summing ungated rows, and nothing in the suite could tell the
    difference. This is the assertion that can."""
    path = os.path.join(RUNNER_DIR, reader)
    assert os.path.exists(path), f"{reader} is on the roster but not on disk"
    assert "corpus_gates" in module_imports(path), (
        f"{reader} reports numbers derived from `pass` or `tokens_in` without "
        f"importing corpus_gates -- it is filtering by its own private copy of "
        f"the rule, or not filtering at all")


def test_the_reader_roster_is_not_empty():
    """A parametrized test over an empty roster reports zero failures and looks
    exactly like a pass.

    The floor is the count ticket 31 AC#3 named: four readers, and the roster
    carried three of them for three days. It rises with the roster so a reader
    cannot be dropped back off it and leave a green suite behind (ticket 35).
    """
    assert len(READER_MODULES) >= 4


def test_the_roster_names_every_reader_ticket_31_ac3_did():
    """INDEPENDENT COPY of AC#3's list -- do not derive it from READER_MODULES.
    AC#3 enumerates the consumers that sum or compare `tokens_in` across models;
    the roster is supposed to be that list. Restating it here is what turns a
    reader missing from the roster into a failure instead of a smaller loop
    (harness #5: the checker must not read its rule from the thing checked).
    """
    ac3_named_readers = {"tables.py", "stats.py", "calibration_report.py",
                         "effort_verdict.py"}
    assert ac3_named_readers <= set(READER_MODULES), (
        f"ticket 31 AC#3 names readers absent from the roster: "
        f"{sorted(ac3_named_readers - set(READER_MODULES))}")


def test_corpus_gates_declares_itself_core():
    """It is imported BY stats.py, which import_gate.py holds to the stdlib-only
    rule; a core module may only import the stdlib and other core modules. The
    declaration is what puts corpus_gates inside that boundary, and the gate's
    set-equality check is what makes removing it fail rather than silently shrink
    the core."""
    with open(os.path.join(RUNNER_DIR, "corpus_gates.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    declared = any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "CORE_MODULE" for t in n.targets)
        and isinstance(n.value, ast.Constant) and n.value.value is True
        for n in tree.body)
    assert declared, "corpus_gates.py must declare CORE_MODULE = True"
    assert module_imports(os.path.join(RUNNER_DIR, "corpus_gates.py")) == set(), \
        "a core module imports the stdlib and the core only; this one imports nothing"


# --------------------------------------------------------------------------- #
# 5. The real corpus, dispositioned by count rather than by sample.
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def corpus():
    if not os.path.exists(RESULTS):
        pytest.skip(f"no corpus at {RESULTS}")
    return [json.loads(l) for l in open(RESULTS, encoding="utf-8") if l.strip()]


def test_every_corpus_row_carries_both_dispositions(corpus):
    """No row is silently un-dispositioned. Checked before any count below, since
    a missing field would make each count a statement about a smaller corpus than
    the one being reported."""
    missing_exit = [r["run_id"] for r in corpus if "exit_reason" not in r]
    missing_status = [r["run_id"] for r in corpus if "tokens_in_status" not in r]
    assert missing_exit == [], missing_exit
    assert missing_status == [], missing_status


def test_the_corpus_splits_where_the_audit_said_it_did(corpus):
    """The 2026-07-30 audit's counts, pinned. These are the denominators every
    published figure rests on; a silent change to any of them changes what the
    tables mean."""
    assert len(corpus) == 268
    kept, excluded = corpus_gates.summarizable_rows(corpus)
    assert len(kept) == 267
    assert excluded == {"cli_error": 1}

    kept_t, excluded_t = corpus_gates.tokens_in_rows(corpus)
    assert len(kept_t) == 148
    assert excluded_t == {"quarantined": 64, "recovered_in_ledger": 56}


def test_the_one_excluded_run_is_the_one_the_audit_named(corpus):
    """Named, not counted. A different row failing the exit gate would leave the
    count at 1 and change which published number moved."""
    bad = [r for r in corpus if not corpus_gates.summarizable(r)]
    assert [r["run_id"] for r in bad] == ["sweep2b--fable--medium--bare--t3-a--r1"]
    assert bad[0]["pass"] is True, (
        "the row is excluded for its exit_reason, not its verdict -- it is a "
        "PASSING row whose numbers describe a truncated run")


def test_every_token_usable_row_is_codex_family(corpus):
    """Determined positively in the audit: the 148 measured rows are exactly the
    codex-family runs, whose parse branch was never buggy. The family carries
    three labels, not one -- `sol` plus the two t13 comparison heads -- and each
    count is named so that a row migrating between labels cannot leave the total
    at 148 and pass. If a claude-family row (fable, claude-haiku-4-5, kimi-k3,
    hybrid) ever shows up as `measured` under the old parser, the allowlist has
    been widened and the undercount is back in the published numbers."""
    kept, _ = corpus_gates.tokens_in_rows(corpus)
    by_model = {}
    for r in kept:
        by_model[r["model"]] = by_model.get(r["model"], 0) + 1
    assert by_model == {"sol": 112, "gpt-5.6-luna": 18,
                        "gpt-5.3-codex-spark": 18}, by_model
    assert sum(by_model.values()) == 148
    assert len(kept) == 148

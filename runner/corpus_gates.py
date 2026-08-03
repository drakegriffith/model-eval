"""Reader-side dispositions for the results corpus. ONE place, so the readers
cannot drift apart.

Two independent taints, two predicates. A row can be clean on one and dirty on
the other, so they are never collapsed into a single "good row" flag:

  `summarizable`          -- did the run reach a clean exit? A truncated run's
                             numbers describe a truncated run. Ticket 34.
  `tokens_in_usable`      -- is the `tokens_in` ON THIS ROW the true
                             cache-inclusive total? Ticket 31 AC#3.

Why this module exists at all. Ticket 34 declined to gate five readers and gave
the right reason: "one rule copied into five readers with nothing checking the
five agree is drift, not defense." That objection is answered by copying the
rule into *no* reader -- every reader calls in here, and the tests below the
line in tests/test_reader_token_gates.py pin the predicates themselves.

Not the harness #5 checker-vs-worker case. These are readers of the same data,
not a gate reading its decision rule out of a file the worker can write. The
gate that enforces #5 for this corpus is the test file, which restates the
literals ("ok", "measured") independently of anything importable from here --
see the annotation on TOKENS_IN_USABLE_STATUS below.
"""

# DECLARATION READ BY runner/import_gate.py. This module is imported by stats.py,
# which the gate holds to the stdlib-only rule, so it has to be inside the core
# boundary rather than a third-party dependency of it. The name is ALSO listed in
# import_gate.CORE_MODULES and, independently, in
# tests/test_import_gate.EXPECTED_CORE_MODULES -- three places on purpose, and
# that is the intended friction, not duplication to design away.
CORE_MODULE = True

# --------------------------------------------------------------------------- #
# The two rules.
# --------------------------------------------------------------------------- #
# A run that did not exit cleanly is excluded from every summary. Its tokens
# describe a truncated session and its `pass` was never earned by enumeration --
# ticket 34 found exactly this row being judged (score 8.75) and averaged into
# published quality means.
SUMMARIZABLE_EXIT_REASON = "ok"

# Only "measured" means the number on the row is the cache-inclusive truth.
# "recovered_in_ledger" is NOT usable here on purpose: the row's own tokens_in is
# still the wrong pre-fix number, and the recovered value lives in usage.jsonl to
# be joined by run_id. A reader that treated it as usable would read the wrong
# number while believing it had been fixed -- the worst of the three states.
#
# DUPLICATED RULE (harness #5) -- this literal is restated in
# tests/test_reader_token_gates.py::test_only_measured_is_usable_on_the_row and
# in usage_ledger.tokens_in_status's docstring. The duplication IS the control:
# the test must not learn the answer from the module it is checking. Do not DRY
# these into one shared constant.
TOKENS_IN_USABLE_STATUS = "measured"

# The middle state, named HERE rather than at a reader so that "which rows get a
# ledger join" stays one decision in one place. This module never reads a file --
# it says which rows may take a joined value; usage_ledger.recovered_tokens_in
# performs the join, and only offers rows the ledger itself marked measured.
#
# DUPLICATED RULE (harness #5), same as the two literals above: restated in
# tests/test_reader_token_gates.py::test_only_the_middle_status_is_recoverable.
TOKENS_IN_RECOVERABLE_STATUS = "recovered_in_ledger"


def summarizable(row):
    """True when this run's numbers may enter any published summary."""
    return row.get("exit_reason") == SUMMARIZABLE_EXIT_REASON


def tokens_in_usable(row):
    """True when `row["tokens_in"]` is the true cache-inclusive total.

    An unstamped row is NOT usable. A row that never went through the provenance
    backfill is a row nobody has dispositioned, which is not the same thing as a
    clean one -- fail closed (harness: silence is not evidence).
    """
    return row.get("tokens_in_status") == TOKENS_IN_USABLE_STATUS


def tokens_in_recoverable(row):
    """True when an input number for this row exists in usage.jsonl, joinable by
    run_id. Disjoint from `tokens_in_usable` by construction: a row is either
    right on its face or recoverable elsewhere, never both, and a quarantined row
    is neither.
    """
    return row.get("tokens_in_status") == TOKENS_IN_RECOVERABLE_STATUS


def invocation_mode_of(row):
    """The row's recorded invocation mode (ticket 32), with the two absences
    kept apart:

      key missing  -> "unstamped"     nobody dispositioned this row
      value None   -> "inapplicable"  a mock row; no model was invoked
      otherwise    -> the recorded value ("single_shot"/"multi_turn"/"unknown")

    Never consults `turns`: turn counts are an artifact of the parse branch
    (structurally 1 on every codex row) and are barred from citation. The rule
    that assigns modes lives in usage_ledger.invocation_mode; this reads only
    what the writer or the backfill recorded.
    """
    if "invocation_mode" not in row:
        return "unstamped"
    mode = row["invocation_mode"]
    return "inapplicable" if mode is None else mode


# --------------------------------------------------------------------------- #
# Partitioning, with the count kept attached to the result.
# --------------------------------------------------------------------------- #
def partition(rows, predicate, reason_key):
    """Split `rows` into (kept, excluded_counts).

    `excluded_counts` maps the value of `reason_key` to how many rows it cost.
    Returned rather than printed so the caller decides where it renders, but it
    is returned ALWAYS -- a consumer cannot filter without receiving the tally,
    which is what keeps AC#4 from rotting into a silent filter.
    """
    kept, excluded = [], {}
    for r in rows:
        if predicate(r):
            kept.append(r)
        else:
            key = r.get(reason_key) or "unstamped"
            excluded[key] = excluded.get(key, 0) + 1
    return kept, excluded


def summarizable_rows(rows):
    return partition(rows, summarizable, "exit_reason")


def tokens_in_rows(rows):
    return partition(rows, tokens_in_usable, "tokens_in_status")


def format_exclusions(label, n_in, kept, excluded):
    """Count the subjects out loud (AC#4).

    Renders the zero case as a distinct sentence rather than as an empty string:
    a corpus with nothing to exclude and a corpus that was never inspected must
    not print identically.
    """
    if n_in == 0:
        return f"{label}: NO ROWS INSPECTED — not a clean corpus, no corpus at all"
    head = f"{label}: inspected={n_in} kept={len(kept)} excluded={n_in - len(kept)}"
    if not excluded:
        return head + " (nothing to exclude)"
    return head + " — " + ", ".join(f"{k}={v}" for k, v in sorted(excluded.items()))


def tokens_in_note(n_in, kept, excluded):
    """One-line markdown footnote for a table that had to drop input tokens."""
    return ("\n\n> **input tokens gated** (ticket 31 AC#3): "
            + format_exclusions("rows", n_in, kept, excluded)
            + ". Excluded rows carry a pre-fix `tokens_in` undercounted 30x–400x; "
            "`recovered_in_ledger` rows have the true value in `usage.jsonl`, "
            "joinable by `run_id`.\n")

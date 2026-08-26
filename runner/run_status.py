#!/usr/bin/env python3
"""run_status.py -- what a run's exit_reason means for the ESTIMAND: does this
row belong in the pass-rate denominator, or is it a status reported beside it?

THE RULE, from the pre-registered bundle (~/studio-handoff/prompt-2-run-experiment.md,
PR #9, panel-reviewed):

    Timeouts and infra errors are distinct statuses, excluded from the
    denominator and reported separately, never counted as model failures.

findings.md auto-assert rule 7 says the same thing for the wall clock
specifically ("wall-clock timeouts log a distinct status, never a task fail"),
and so does issue #8's surviving design: cap TURNS, not wall clock;
`finish_reason=length` and timeouts are void, never FAIL.

WHY THIS IS A MODULE AND NOT A LINE IN A READER. The predicate has two callers
already -- run.py stamps it on the row it writes, tables.py gates the pass rate
with it -- and the rule was previously stated in neither: it lived in a docstring
in run.py that asserted the OPPOSITE, while tables.py counted `len(rs)` and made
no claim at all. One rule copied into two readers with nothing checking they
agree is drift, so this follows corpus_gates.py's shape: the rule lives here, the
readers call in, and the test file restates the literals independently.

Not merged into corpus_gates.py, though it is a close neighbour, for two reasons.
corpus_gates answers "may this row's NUMBERS be published" (is it truncated, is
tokens_in the real total); this answers "is this row a model measurement at all",
which is an estimand question and can differ -- a cap_exhausted row is a model
measurement whose numbers describe a truncated session. And corpus_gates is a
FROZEN comparability surface: changing it changes the meaning of 193 archived
judgments retroactively, which is a much larger claim than adding a disposition
this corpus has never carried.

WHY IT MATTERS ON THIS STACK, in numbers. Prefill runs 57-71 tok/s; a 61k-token
prefill was clocked at 1077 s; under PARALLEL=4 a neighbour's prefill starved a
decode to 0.05 tok/s -- a 380x wall-clock swing on identical work. Counting a
wall-clock kill as a task failure puts that swing in the accuracy column, so the
SCHEDULER grades the model. Worse, it is not random across the dose ladder: the
high-harness arms carry the largest prompts, so it manufactures exactly the "L5
looks worse" result the harness-dose experiment exists to measure.

WHAT DELIBERATELY STAYS SCORED. `cap_exhausted` -- the BROKER's K acceptance cap.
Pre-registration amendment A1 (docs/studio-handoff/prompt-2-run-experiment.md at
a0cef36, registered 2026-08-25: K=20, cap_exhausted SCORED, stage-0 flip at >= 10
requests) scores it a failure on purpose: the model spent its K acceptance
requests and did not converge, which is a fact about the model. It is a
different cap from the wall clock, and folding the two together would smuggle
a protocol treatment out of the denominator.

Stdlib only. No file I/O, no imports of the instrument.
"""

# DECLARATION READ BY runner/import_gate.py. stats.py imports this module,
# and a core module may import only the stdlib and other core modules, so
# leaving it outside the boundary would make stats.py's own import illegal.
# It qualifies on the merits and not merely by necessity: pure predicates
# over a dict, no file I/O, no environment reads, nothing to configure.
#
# The name is ALSO listed in import_gate.CORE_MODULES and, independently, in
# tests/test_import_gate.EXPECTED_CORE_MODULES -- three places on purpose,
# and that is the intended friction, not duplication to design away.
CORE_MODULE = True

# The five dispositions, plus the one that says nobody has decided.
SCORED = "scored"
TIMEOUT = "timeout"
INFRA = "infra"
STRUCTURAL = "structurally_impossible"
# Mock runs apply solution.patch and never call a model. These were filed under
# INFRA, which kept every denominator correct and made the REPORTED number
# misleading: a --mock sweep printed "infra=75", and "infra=75" is what a badly
# broken serving stack looks like. The separate report exists to be read by an
# operator, so a class that makes it misread defeats the point of having one.
MOCK = "mock"
UNCLASSIFIED = "unclassified"

# Only this one puts a row in the denominator.
IN_DENOMINATOR = (SCORED,)

# The declared table. An exit_reason absent from it is UNCLASSIFIED, never
# assumed -- see status_class.
EXIT_REASON_CLASS = {
    # Complete runs. The grade is the model's.
    "ok": SCORED,
    # The broker's K acceptance cap. A protocol treatment with a pre-registered
    # scoring rule (pre-registration amendment A1, docs/studio-handoff/
    # prompt-2-run-experiment.md at a0cef36, registered 2026-08-25: K=20,
    # cap_exhausted SCORED, stage-0 flip at >= 10 requests), not an instrument
    # fault. See the module note.
    "cap_exhausted": SCORED,

    # The wall clock ran out. This says the cap was too small, or the server was
    # busy, or a neighbour was prefilling -- it says nothing about the task.
    "timeout": TIMEOUT,

    # The instrument did not produce a measurement of the model.
    "cli_error": INFRA,
    "auth_unavailable": INFRA,
    "broker_failed": INFRA,
    "no_completion": INFRA,
    "kimi_key_missing": INFRA,
    "verify_timeout": INFRA,
    # run.py has no dispatch path for this row's declared driver (issue #25),
    # so no CLI was ever launched. An instrument fact, like cli_error -- never
    # a model measurement -- and distinct from structurally_impossible, which
    # answers a different question (can the driver EXPRESS this cell at all,
    # a serving_registry capability-manifest fact) than this one (does run.py
    # itself have launch code for the driver, independent of the registry).
    "driver_unsupported": INFRA,

    # The cell does not exist for this driver (issue #12 c). Never a 0: a 0
    # asserts the model attempted the task and failed it.
    "structurally_impossible": STRUCTURAL,

    # Mock runs apply a patch instead of calling a model. They are apparatus
    # tests, and they are not measurements of anything, so they never enter a
    # rate. Named rather than left to fall through to UNCLASSIFIED, so a real
    # unknown status stays distinguishable from a deliberate non-measurement.
    "mock": MOCK,
    "mock_fail": MOCK,
    "mock_patch_failed": MOCK,
}

# When run.py appends a second reason with '+', the row carries both facts. The
# worse one wins, worst first: a run that timed out AND whose grader hung is
# still, first, a run nobody measured.
_SEVERITY = (UNCLASSIFIED, STRUCTURAL, TIMEOUT, INFRA, MOCK, SCORED)


def status_class(exit_reason):
    """The estimand disposition of one exit_reason. Fail-closed BOTH ways.

    An unknown reason returns UNCLASSIFIED rather than defaulting. Defaulting it
    to SCORED re-creates the bug for the next status somebody adds; defaulting it
    to excluded silently inflates the pass rate by dropping rows nobody looked
    at. Cannot-determine is its own answer and gets reported by name -- a status
    that inspected nothing is not a pass.
    """
    if not exit_reason:
        return UNCLASSIFIED
    parts = [p for p in str(exit_reason).split("+") if p]
    classes = [EXIT_REASON_CLASS.get(p, UNCLASSIFIED) for p in parts]
    if not classes:
        return UNCLASSIFIED
    return min(classes, key=_SEVERITY.index)


def in_denominator(row):
    """True when this row is a model measurement that a pass rate may count."""
    return status_class(row.get("exit_reason")) in IN_DENOMINATOR


def partition_for_rate(rows):
    """(scored_rows, {class: count}) -- the denominator and what left it.

    Returned together, always, because "excluded from the denominator" and
    "reported separately" are one instruction. A caller that gets only the kept
    rows has been handed a cleaner-looking table and no way to know what it cost.
    """
    kept, excluded = [], {}
    for row in rows:
        cls = status_class(row.get("exit_reason"))
        if cls in IN_DENOMINATOR:
            kept.append(row)
        else:
            excluded[cls] = excluded.get(cls, 0) + 1
    return kept, excluded


def format_excluded(excluded):
    """The separate report, as one short phrase. Empty string when nothing was
    excluded -- the caller decides whether to print a column at all."""
    return " ".join(f"{cls}={n}" for cls, n in sorted(excluded.items()))

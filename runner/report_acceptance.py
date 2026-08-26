#!/usr/bin/env python3
"""report_acceptance.py -- the acceptance-request summary issue #22 requires
beside the pass rate: max(acceptance_requests), its distribution, and the
cap_exhausted count.

WHY THIS EXISTS (A1, drakegriffith/model-eval@a0cef36): "The stage-1 report
states max(acceptance_requests), its distribution, and the cap_exhausted
count beside the pass rate." A1 also fixes the K_FLIP_THRESHOLD decision rule
itself -- re-registering K before stage 1 if any stage-0 rep reaches >= 10
acceptance requests -- and A7 supersedes the separate clause that used to tie
a stage-1 cap_exhausted run to blocking publication on #18; A7 is explicit
that "#22's reporting requirement stands" regardless. This module is ONLY the
reporting half: it renders the three numbers, it does not gate anything on
them or re-derive K.

WHY A NEW MODULE AND NOT tables.py. tables.py's own docstring scopes it to
"the six deliverable tables (video chapters)" read by build_report, each
keyed on `qual`/`ledger` and emitted as markdown. This summary has none of
that shape: it is plain text meant to sit beside `ladder_from_results.py`'s
per-block table (which is not markdown either), and it needs no judgments and
no usage ledger. stage0_probe.py already computes ONE of these three
independently -- `max_acceptance_requests` over its own scored rows
(stage0_probe.py:190-192) -- but no distribution and no cap_exhausted count;
those two exist only here. Putting the shared piece (the max) behind one rule
instead of a second private copy inside tables.py, and putting the two pieces
stage0_probe.py does not have anywhere at all, gives every future caller
(ladder_from_results now; stage0_probe.py's `render_comment` switching its own
max over to this module is a candidate follow-up, out of scope for this fix)
one place to agree on all three, not just one.

DENOMINATOR. Same estimand as everywhere else in this stack: the summary is
computed over `run_status.partition_for_rate(rows)`'s SCORED set, which is
also the pass rate's own denominator (`run_status.py`, `ladder_from_results
.report_block`'s `n_scored`) -- so "beside the pass rate" is also "over the
same rows as the pass rate", not a second, uncomparable count. A row with no
acceptance suite at all (`acceptance_requests is None` -- mocked or
unbrokered) is counted separately, never folded into 0: a 0 asserts the
model made zero acceptance requests, which is not what "no suite ran" means.

Stdlib only. No file I/O, no imports of the instrument.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_status  # noqa: E402


def acceptance_summary(rows):
    """-> {n_scored, max_acceptance_requests, distribution, no_acceptance_suite,
    cap_exhausted_count}, computed over the SCORED rows in `rows`.

    `distribution` is {value: count} over non-None acceptance_requests among
    scored rows. `no_acceptance_suite` is the count of scored rows whose
    acceptance_requests is None (mocked or never brokered) -- reported
    separately, never as a 0. `max_acceptance_requests` is None when no
    scored row carries a value at all, distinct from a real 0.
    """
    scored, _excluded = run_status.partition_for_rate(rows)
    acc_values = [r.get("acceptance_requests") for r in scored]
    non_null = [v for v in acc_values if v is not None]

    distribution = {}
    for v in non_null:
        distribution[v] = distribution.get(v, 0) + 1

    return {
        "n_scored": len(scored),
        "max_acceptance_requests": max(non_null) if non_null else None,
        "distribution": distribution,
        "no_acceptance_suite": sum(1 for v in acc_values if v is None),
        "cap_exhausted_count": sum(
            1 for r in scored if r.get("exit_reason") == "cap_exhausted"),
    }


def format_acceptance_summary(summary):
    """One printable line, meant to sit beside a pass_rate line (issue #22)."""
    max_acc = summary["max_acceptance_requests"]
    max_line = "n/a" if max_acc is None else str(max_acc)

    dist_parts = [f"{k}={v}" for k, v in sorted(summary["distribution"].items())]
    if summary["no_acceptance_suite"]:
        dist_parts.append(f"no acceptance suite={summary['no_acceptance_suite']}")
    dist_line = ", ".join(dist_parts) if dist_parts else "(no acceptance_requests recorded)"

    return (f"acceptance_requests -- max: {max_line}  distribution: {dist_line}  "
            f"cap_exhausted: {summary['cap_exhausted_count']}")

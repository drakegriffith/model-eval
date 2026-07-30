#!/usr/bin/env python3
"""regrade_36.py -- ticket 36: re-grade t2-py-b's 78 recorded rows.

All 78 t2-py-b rows in results.jsonl were graded by the suite ticket 28's broken
arm walked straight through (`second_gap == first_gap * 2`, which linear backoff
satisfies exactly). Ticket 29 repaired the suite prospectively and left the
disqualification standing for those 78. This driver closes it, by replaying each
archived scratch tree against TODAY's canonical apparatus:

    graded_run(scratch, task_dir)   # run.py:959

graded_run opens a grading_tree: the model's tree minus NOT_GRADED_DIRS, minus
any apparatus file the model added, plus every canonical apparatus file overlaid
on top. So the model's surviving `src/jobqueue/` is graded by the repaired
`base/tests/test_jobqueue.py`, by construction. No model is invoked. Zero tokens.

Two properties this script is built around, both learned the hard way:

1.  A script that grades nothing and a script that grades everything correctly
    are byte-identical from a run of 78 passes. So `main()` ALWAYS runs the two
    negative-control gates first -- ticket 28's broken arm must grade False, the
    reference solution.patch must grade True -- and refuses to touch the 78 if
    either gate does not produce its expected verdict. There is no flag to skip
    them.

2.  Silence is not evidence. The inspected count is printed and asserted equal
    to the number of subjects resolved from results.jsonl; a row that could not
    be graded at all is recorded as U with a named cause, never rounded into
    either column; and a run that exits 0 without a pytest summary line in its
    output is treated as ungradable, not as a pass.

Provenance is recorded per row and re-read per row, not assumed: the sha256 of
`verify.sh` and of every canonical apparatus file grading_tree overlays, plus
the model-gauntlet HEAD and whether the tree was dirty.

Writes `runner/results/regrade-36.jsonl` (78 rows keyed by run_id, verdict under
its own `regrade_36_pass` field) and `runner/results/regrade-36-controls.jsonl`
(the two gate rows). `results.jsonl` is never opened for writing: the corpus is
append-only, stays at 268 rows, and its original `pass` field is preserved under
its own name rather than edited -- ticket 34's pattern.

The decision rule (ticket 36 "Decision rule -- fixed at filing") is applied by
`report()` below. It was written before any number was seen and is not
amendable; the thresholds are duplicated into this file deliberately so the gate
does not read its rule from something a later run could rewrite. If you are
tidying: DO NOT "DRY" these constants against the ticket text. The duplication is
the control.

Usage:
    python3 negative_control/regrade_36.py                # gates, then all 78
    python3 negative_control/regrade_36.py --gates-only   # gates, then stop
    python3 negative_control/regrade_36.py --limit 3      # gates, then a smoke 3
"""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNNER_DIR = os.path.join(ROOT, "runner")
sys.path.insert(0, RUNNER_DIR)
sys.path.insert(0, HERE)

import run as runner  # noqa: E402
from broken_arm import BROKEN  # noqa: E402
from run_arms import apply_broken  # noqa: E402

TASK = "t2-py-b"

# The test ticket 29 repaired, by name. 110053c renamed
# test_backoff_delay_doubles_each_attempt -> this, and changed it from a
# two-gap ratio assertion (which linear backoff satisfies) to a four-gap
# closed-form assertion (which it does not). "The failure output names the
# backoff test" in the decision rule means this test and no other: it is the
# one whose repair is the reason the 78 rows are being re-graded at all.
BACKOFF_TEST = "test_backoff_delay_is_exponential_not_merely_increasing"

# --- decision rule thresholds, duplicated from ticket 36 on purpose ---------
# See module docstring: a gate must not read its rule from a file a worker can
# rewrite. The copy in projects/frontier-benchmark/tickets/36-*.md is annotated
# to point back here. Changing either copy without the other is the bug this
# duplication exists to catch.
RULE3_F_BACKOFF_ESCALATION = 8   # F_backoff >= 8 (~10% of 78) -> file the audit
RULE2_F_TOTAL_HALT = 1           # F_total >= 1 -> stop, number goes to Drake
# ---------------------------------------------------------------------------


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_provenance(task_dir):
    """relpath -> sha256 for every file grading_tree will overlay.

    Read from runner.canonical_grading_files, which is the same function
    grading_tree itself calls, so this cannot drift from what is actually
    installed into the tree. Re-read on every row rather than cached once, so a
    mid-run edit to the suite shows up as a per-row provenance mismatch instead
    of being papered over by a stale snapshot.
    """
    canon = runner.canonical_grading_files(task_dir)
    return {rel.replace(os.sep, "/"): sha256_file(src)
            for rel, src in sorted(canon.items())}


def git_head(root):
    def g(*args):
        return subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True).stdout.strip()
    return g("rev-parse", "HEAD"), bool(g("status", "--porcelain"))


PYTEST_RAN = re.compile(
    r"\b\d+\s+(?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed)\b")
FAILED_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)
FAILURE_HEADER = re.compile(r"^_{3,}\s+(\S+?)\s+_{3,}$", re.M)


def pytest_ran(out):
    """True only on positive evidence that pytest collected and reported.

    verify.sh is `set -euo pipefail`, so a venv build or `pip install` failure
    aborts it before pytest ever runs and returns non-zero -- which is
    indistinguishable from a test failure by return code alone. This is the
    channel that separates *did it execute* from *what did it conclude*.
    """
    return bool(PYTEST_RAN.search(out) or FAILED_LINE.search(out)
                or FAILURE_HEADER.search(out))


def failing_tests(out):
    """Every failing/erroring test nodeid, in report order; first one is first."""
    names = [m.group(1) for m in FAILED_LINE.finditer(out)]
    if not names:
        names = [m.group(1) for m in FAILURE_HEADER.finditer(out)]
    seen, ordered = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def classify(first):
    """F_backoff iff the first failing test IS the test ticket 29 repaired."""
    if first is None:
        return "F_other"
    return "F_backoff" if first.split("::")[-1] == BACKOFF_TEST else "F_other"


def ungradable_cause(rc, out):
    tail = out[-4000:]
    if "No module named venv" in tail or "Error: Command" in tail:
        return "venv_build_failed"
    for probe in ("Could not find a version", "Temporary failure in name resolution",
                  "Network is unreachable", "SSLError", "Connection refused",
                  "Failed to establish a new connection", "ReadTimeoutError"):
        if probe in tail:
            return "pip_install_failed_network"
    if "pip" in tail and "ERROR" in tail:
        return "pip_install_failed"
    return f"verify_sh_aborted_before_pytest_rc{rc}"


def grade_one(scratch, task_dir):
    """-> dict describing one graded_run. Never raises for a subject's own sake."""
    started = time.time()
    try:
        rc, out = runner.graded_run(scratch, task_dir)
    except subprocess.TimeoutExpired:
        return {"status": "ungradable", "passed": None, "rc": None,
                "cause": "graded_run_timeout_600s", "failing": [],
                "tail": "", "wall_s": round(time.time() - started, 1)}
    except Exception as exc:  # harness error, not a verdict
        return {"status": "ungradable", "passed": None, "rc": None,
                "cause": f"harness_error:{type(exc).__name__}: {exc}"[:300],
                "failing": [], "tail": "",
                "wall_s": round(time.time() - started, 1)}
    wall = round(time.time() - started, 1)
    ran = pytest_ran(out)
    tail = out.strip()[-1500:]
    if not ran:
        # Includes the rc==0-with-no-test-evidence case: a green with nothing
        # behind it is withheld, not counted as a pass.
        cause = ("rc0_without_pytest_summary" if rc == 0
                 else ungradable_cause(rc, out))
        return {"status": "ungradable", "passed": None, "rc": rc,
                "cause": cause, "failing": [], "tail": tail, "wall_s": wall}
    failing = failing_tests(out)
    return {"status": "graded", "passed": rc == 0, "rc": rc, "cause": None,
             "failing": failing, "tail": tail, "wall_s": wall}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- gates -----------------------------------------------------------------

def gate_broken_arm(task_dir, scratch_root):
    """Ticket 28's broken arm through the same path. MUST grade False."""
    scratch = os.path.join(scratch_root, "regrade36--gate-broken")
    runner.prepare_scratch(task_dir, scratch, harness=False)
    touched = apply_broken(scratch, TASK)
    res = grade_one(scratch, task_dir)
    return {
        "arm": "broken", "expected_pass": False, "files_touched": touched,
        "why": BROKEN[TASK]["why"], "scratch": scratch, **res,
    }


def gate_solution(task_dir, scratch_root):
    """The reference solution.patch through the same path. MUST grade True."""
    scratch = os.path.join(scratch_root, "regrade36--gate-solution")
    runner.prepare_scratch(task_dir, scratch, harness=False)
    patch = os.path.join(task_dir, "solution.patch")
    ap = subprocess.run(["git", "apply", "--whitespace=nowarn", patch],
                        cwd=scratch, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
    if ap.returncode != 0:
        raise SystemExit(
            "GATE ABORT: solution.patch did not apply cleanly to a fresh "
            f"scratch. The task changed; fix that before trusting any "
            f"re-grade.\n{ap.stdout}")
    res = grade_one(scratch, task_dir)
    return {"arm": "solution", "expected_pass": True, "files_touched": [],
            "why": "reference solution; the suite must accept it",
            "scratch": scratch, **res}


def run_gates(task_dir, scratch_root, head, dirty, controls_path):
    print("=" * 72)
    print("GATES -- proving the script can produce BOTH verdicts")
    print("A run of 78 passes proves nothing until these two do.")
    print("=" * 72, flush=True)
    rows = []
    ok = True
    for fn in (gate_broken_arm, gate_solution):
        row = fn(task_dir, scratch_root)
        row.update({
            "ticket": 36, "task": TASK, "regraded_at": now_iso(),
            "gauntlet_head": head, "gauntlet_dirty": dirty,
            "canonical_sha256": canonical_provenance(task_dir),
            "grading_path": "prepare_scratch -> graded_run",
        })
        first = row["failing"][0] if row["failing"] else None
        row["first_failing_test"] = first
        agrees = (row["passed"] is not None
                  and row["passed"] == row["expected_pass"])
        row["gate_held"] = agrees
        rows.append(row)
        print(f"  arm={row['arm']:<8} expected_pass={row['expected_pass']!s:<5} "
              f"got={row['passed']!s:<5} rc={row['rc']} "
              f"status={row['status']} first_failing={first} "
              f"({row['wall_s']}s)  -> {'HELD' if agrees else 'BROKEN'}",
              flush=True)
        if not agrees:
            ok = False
    with open(controls_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"  gate rows -> {os.path.relpath(controls_path, ROOT)}")
    if not ok:
        raise SystemExit(
            "GATE FAILED. The script has not demonstrated it can produce both "
            "verdicts, so none of the 78 may be interpreted. Fix the driver, "
            "not the subjects.")
    # A held broken-arm gate should fail on the backoff test specifically; if it
    # fails on something else the arm is no longer measuring what it claims.
    broken = rows[0]
    if broken["first_failing_test"] and \
            broken["first_failing_test"].split("::")[-1] != BACKOFF_TEST:
        print(f"  NOTE: broken arm's first failing test is "
              f"{broken['first_failing_test']}, not {BACKOFF_TEST}.")
    print("  Both gates held.\n", flush=True)
    return rows


# --- the 78 ----------------------------------------------------------------

def subjects(results_path):
    rows = []
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                if r.get("task") == TASK:
                    rows.append(r)
    return rows


def report(graded, expected_n, inspected_n):
    """Apply ticket 36's decision rule verbatim. Zeros are stated, not omitted."""
    F_total = [r for r in graded if r["regrade_36_class"] in ("F_backoff", "F_other")]
    F_backoff = [r for r in F_total if r["regrade_36_class"] == "F_backoff"]
    F_other = [r for r in F_total if r["regrade_36_class"] == "F_other"]
    U = [r for r in graded if r["regrade_36_class"] == "U"]
    P = [r for r in graded if r["regrade_36_class"] == "pass"]

    print("=" * 72)
    print("TICKET 36 -- RE-GRADE OF t2-py-b's RECORDED ROWS")
    print("=" * 72)
    print(f"subjects resolved from results.jsonl : {expected_n}")
    print(f"subjects the run actually inspected  : {inspected_n}")
    if inspected_n != expected_n:
        print(f"SHORTFALL: {expected_n - inspected_n} subject(s) never inspected. "
              f"The counts below do not cover the corpus.")
    print(f"re-graded pass                       : {len(P)}")
    print(f"F_total                              : {len(F_total)}")
    print(f"F_backoff                            : {len(F_backoff)}")
    print(f"F_other                              : {len(F_other)}")
    print(f"U (ungradable, withheld)             : {len(U)}")
    print("-" * 72)

    if U:
        causes = {}
        for r in U:
            causes[r["ungradable_cause"]] = causes.get(r["ungradable_cause"], 0) + 1
        print("U by cause (rule 5 -- withheld with a named refill, never struck):")
        for cause, n in sorted(causes.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3}  {cause}")
        for r in U:
            print(f"       {r['run_id']}  [{r['ungradable_cause']}]")
    else:
        print("U = 0. Every subject was graded; nothing is withheld.")

    if F_total:
        print("-" * 72)
        print("Failing rows, first failing test:")
        for r in sorted(F_total, key=lambda r: (r["regrade_36_class"], r["run_id"])):
            print(f"  [{r['regrade_36_class']:<9}] {r['run_id']}  "
                  f"{r['first_failing_test']}")
        by_model = {}
        for r in F_total:
            by_model[r["model"]] = by_model.get(r["model"], 0) + 1
        print("F_total by model: " + ", ".join(
            f"{m}={n}" for m, n in sorted(by_model.items(), key=lambda kv: -kv[1])))

    print("=" * 72)
    print("DECISION RULE (fixed at filing, applied verbatim)")
    print("=" * 72)
    if len(F_total) == 0:
        print("Rule 1 FIRES -- F_total = 0.")
        print("  The disqualification lifts RETROACTIVELY for all "
              f"{len(P)} graded rows.")
        print("  05's 190/190 and 28's excluded-n restatement stand unchanged.")
        print("  The t2-py-b ceiling is thereby shown to be a fact about the")
        print("  task, not about its grader.")
        print("  This is a POSITIVE RESULT and must be published as one. Zero")
        print("  flips is a finding, not a null.")
    if len(F_total) >= RULE2_F_TOTAL_HALT:
        print(f"Rule 2 FIRES -- F_total = {len(F_total)} >= {RULE2_F_TOTAL_HALT}.")
        print("  At least one recorded pass:true is a demonstrated false pass.")
        print("  05's ceiling number must be AMENDED and the amended number goes")
        print("  to Drake BEFORE the 05 fork ruling. STOP HERE.")
    if len(F_backoff) >= RULE3_F_BACKOFF_ESCALATION:
        print(f"Rule 3 FIRES -- F_backoff = {len(F_backoff)} >= "
              f"{RULE3_F_BACKOFF_ESCALATION}.")
        print("  28's '1 of 12, localized permissiveness' no longer bounds how")
        print("  much the corpus rests on permissive grading. FILE the")
        print("  eleven-suites audit as its own ticket (do not run it here).")
    else:
        print(f"Rule 3 does not fire -- F_backoff = {len(F_backoff)} < "
              f"{RULE3_F_BACKOFF_ESCALATION}.")
    if len(F_other) >= 1:
        print(f"Rule 4 FIRES -- F_other = {len(F_other)} >= 1.")
        print("  Reported SEPARATELY. These rows fail a test other than the")
        print("  backoff test and are NOT evidence for the permissive-grader")
        print("  narrative; they count toward F_total but not toward rule 3.")
    else:
        print("Rule 4 does not fire -- F_other = 0.")
    if len(U) >= 1:
        print(f"Rule 5 FIRES -- U = {len(U)} >= 1. Those rows stay withheld with")
        print("  the named causes above; not passes, not fails.")
    else:
        print("Rule 5 does not fire -- U = 0.")
    print("=" * 72)
    return {"F_total": len(F_total), "F_backoff": len(F_backoff),
            "F_other": len(F_other), "U": len(U), "pass": len(P),
            "expected_n": expected_n, "inspected_n": inspected_n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(RUNNER_DIR, "results", "results.jsonl"))
    ap.add_argument("--out", default=os.path.join(RUNNER_DIR, "results", "regrade-36.jsonl"))
    ap.add_argument("--controls-out", default=os.path.join(RUNNER_DIR, "results", "regrade-36-controls.jsonl"))
    ap.add_argument("--tasks-dir", default=os.path.join(ROOT, "tasks"))
    ap.add_argument("--scratch", default=os.path.join(ROOT, ".scratch"))
    ap.add_argument("--gates-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="smoke mode: grade only the first N subjects. The "
                         "inspected-count assertion is reported as a shortfall.")
    args = ap.parse_args()

    task_dir = os.path.join(args.tasks_dir, TASK)
    head, dirty = git_head(ROOT)
    baseline_prov = canonical_provenance(task_dir)

    print(f"model-gauntlet HEAD {head}{' (DIRTY)' if dirty else ''}")
    print(f"canonical apparatus overlaid by grading_tree ({len(baseline_prov)} files):")
    for rel, digest in baseline_prov.items():
        print(f"  {digest[:16]}  {rel}")
    print(flush=True)

    with tempfile.TemporaryDirectory(prefix="regrade36-gates-") as gate_root:
        run_gates(task_dir, gate_root, head, dirty, args.controls_out)

    if args.gates_only:
        print("--gates-only: stopping before the 78.")
        return 0

    subs = subjects(args.results)
    expected_n = len(subs)
    if args.limit:
        subs = subs[:args.limit]
    print(f"Re-grading {len(subs)} of {expected_n} resolved {TASK} subjects.\n",
          flush=True)

    graded, inspected = [], 0
    with open(args.out, "w", encoding="utf-8") as f:
        for i, src in enumerate(subs, 1):
            run_id = src["run_id"]
            scratch = os.path.join(args.scratch, run_id)
            prov = canonical_provenance(task_dir)
            if not os.path.isdir(scratch):
                res = {"status": "ungradable", "passed": None, "rc": None,
                       "cause": "scratch_tree_missing", "failing": [],
                       "tail": "", "wall_s": 0.0}
            else:
                res = grade_one(scratch, task_dir)
            inspected += 1
            first = res["failing"][0] if res["failing"] else None
            if res["status"] == "ungradable":
                klass = "U"
            elif res["passed"]:
                klass = "pass"
            else:
                klass = classify(first)
            row = {
                "ticket": 36,
                "run_id": run_id,
                "task": src.get("task"),
                "sweep": src.get("sweep"),
                "model": src.get("model"),
                "effort": src.get("effort"),
                "harness": src.get("harness"),
                "rep": src.get("rep"),
                "original_pass": src.get("pass"),
                "original_exit_reason": src.get("exit_reason"),
                "regrade_36_pass": res["passed"],
                "regrade_36_status": res["status"],
                "regrade_36_class": klass,
                "regrade_36_rc": res["rc"],
                "first_failing_test": first,
                "failing_tests": res["failing"],
                "ungradable_cause": res["cause"],
                "output_tail": res["tail"],
                "wall_s": res["wall_s"],
                "regraded_at": now_iso(),
                "gauntlet_head": head,
                "gauntlet_dirty": dirty,
                "canonical_sha256": prov,
                "provenance_matches_baseline": prov == baseline_prov,
                "grading_path": "graded_run(archived .scratch tree, task_dir)",
                "scratch": os.path.relpath(scratch, ROOT),
            }
            f.write(json.dumps(row) + "\n")
            f.flush()
            graded.append(row)
            flag = {"pass": "ok", "F_backoff": "FAIL-backoff",
                    "F_other": "FAIL-other", "U": "UNGRADABLE"}[klass]
            print(f"[{i:>3}/{len(subs)}] {run_id:<58} {flag:<12} "
                  f"rc={res['rc']} {res['wall_s']}s", flush=True)

    print()
    drift = [r["run_id"] for r in graded if not r["provenance_matches_baseline"]]
    if drift:
        print(f"PROVENANCE DRIFT during the run on {len(drift)} row(s): {drift}")
    else:
        print(f"Provenance stable across all {len(graded)} rows "
              f"(canonical apparatus hashes identical, re-read per row).")

    # Assert positively, on the count actually inspected.
    print(f"\nINSPECTED COUNT: {inspected} (expected {expected_n})")
    summary = report(graded, expected_n, inspected)
    print(f"\nrows -> {os.path.relpath(args.out, ROOT)}")

    if inspected != expected_n:
        print(f"\nASSERTION FAILED: inspected {inspected} != {expected_n} "
              f"subjects. Shortfall named above; the disposition is not final.")
        return 1
    assert inspected == expected_n, "unreachable"
    if summary["F_total"] >= RULE2_F_TOTAL_HALT:
        return 3   # rule 2: stop, the number goes to Drake before the 05 ruling
    return 0


if __name__ == "__main__":
    sys.exit(main())

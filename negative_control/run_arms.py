#!/usr/bin/env python3
"""run_arms.py -- ticket 28 arms 1 and 2 (empty diff, deliberately broken work).

Both arms are model-free, which is the whole reason they are cheap: the question
"does this suite reject work that does not solve the task?" is answered by the
grader, and the grader does not care whether a human, a model, or a script wrote
the tree it is handed. Deliverable 1 of the ticket established that the twelve
selftest.sh scripts already assert phase 1 of arm 1 (unpatched base must fail)
-- but by invoking verify.sh directly against base/, which is a DIFFERENT
measurement from the one the instrument scores on. This driver closes that gap
by going through the same two functions every recorded run goes through:

    prepare_scratch()  -> the working copy a model is actually handed
    run_verify()       -> graded_run() -> the authoritative gate (run.py:892-926)

verify.sh is never invoked directly here. That is a ticket requirement and it is
load-bearing: graded_run builds a throwaway grading tree with canonical
apparatus overlaid, and a permissiveness result measured against a raw verify.sh
would not be a statement about the instrument.

Arm 1 (empty)  -- prepare_scratch and grade. Nothing else. A pass means the
                  suite accepts the starting state.
Arm 2 (broken) -- prepare_scratch, then apply the wrong answer from
                  broken_arm.py, then grade. A pass means the suite accepts an
                  implementation that violates a requirement its own PROMPT.md
                  states in writing.

Results go to a SEPARATE file (default results/negative-control-28.jsonl). They
are deliberately not appended to results.jsonl: these rows are not model runs,
they carry no model/effort/token fields, and letting them into the corpus would
corrupt every downstream pass-rate that reads it.

The verdict recorded per row is whatever run_verify() returned, full stop. The
diagnostic tail is captured with a second graded_run() call, whose return code
is asserted to agree -- a disagreement would mean the gate is not deterministic,
which is a finding in its own right and must not be silently averaged away.
Pass --no-diagnostics to skip that second call (halves wall time, loses the
evidence trail).
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNNER_DIR = os.path.join(ROOT, "runner")
sys.path.insert(0, RUNNER_DIR)
sys.path.insert(0, HERE)

import run as runner  # noqa: E402
from broken_arm import BROKEN  # noqa: E402
from broken_arm_t3t4 import BROKEN_T3T4  # noqa: E402

BROKEN.update(BROKEN_T3T4)

# The twelve. t5-* are ticket 23's domain probe and are not part of the suite
# whose ceiling ticket 05 recorded, so they are out of scope here.
TASKS = [
    "t1-py-a", "t1-py-b", "t1-ts-a", "t1-ts-b",
    "t2-py-a", "t2-py-b", "t2-ts-a", "t2-ts-b",
    "t3-a",
    "t4-py-a", "t4-py-b", "t4-ts-a",
]


def apply_broken(scratch, task):
    """Write the wrong answer into the model's working copy.

    Applied AFTER prepare_scratch so it lands exactly where a model's own work
    would land: after the base commit, inside the tree the model owns, visible
    to loc_changed() and to tamper_report(). A "replace" edit asserts its old
    text was present, so if a task's base file is ever edited out from under
    this file the arm fails loudly instead of quietly degrading into arm 1 and
    reporting a pass that means nothing.
    """
    spec = BROKEN[task]
    touched = []
    for edit in spec["edits"]:
        rel, mode = edit[0], edit[1]
        path = os.path.join(scratch, rel)
        os.makedirs(os.path.dirname(path) or path, exist_ok=True)
        if mode == "write":
            contents = edit[2]
            with open(path, "w", encoding="utf-8") as f:
                f.write(contents)
        elif mode == "replace":
            old, new = edit[2], edit[3]
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if old not in text:
                raise SystemExit(
                    f"[{task}] broken-arm edit no longer applies to {rel}: "
                    f"anchor text not found. The task changed; fix the arm "
                    f"before trusting any result from it.")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace(old, new, 1))
        else:
            raise SystemExit(f"unknown edit mode {mode!r}")
        touched.append(rel)
    return touched


def one(task, arm, tasks_dir, scratch_root, diagnostics):
    task_dir = os.path.join(tasks_dir, task)
    scratch = os.path.join(scratch_root, f"negctl--{arm}--{task}")

    runner.prepare_scratch(task_dir, scratch, harness=False)
    touched = apply_broken(scratch, task) if arm == "broken" else []

    started = time.time()
    passed = runner.run_verify(scratch, task_dir)
    wall_s = round(time.time() - started, 1)

    row = {
        "ticket": 28,
        "arm": arm,
        "task": task,
        "passed": passed,
        "wall_s": wall_s,
        "path": "prepare_scratch -> run_verify -> graded_run",
        "files_touched": touched,
        "loc_changed": runner.loc_changed(scratch),
        "tamper_report": runner.tamper_report(scratch, task_dir),
    }
    if arm == "broken":
        row["violates"] = BROKEN[task]["why"]

    if diagnostics:
        rc, out = runner.graded_run(scratch, task_dir)
        row["rc"] = rc
        row["agrees"] = (rc == 0) == passed
        row["output_tail"] = out.strip()[-1500:]

    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-dir", default=os.path.join(ROOT, "tasks"))
    ap.add_argument("--scratch", default=os.path.join(ROOT, ".scratch"))
    ap.add_argument("--out", default=os.path.join(
        RUNNER_DIR, "results", "negative-control-28.jsonl"))
    ap.add_argument("--arms", default="empty,broken")
    ap.add_argument("--only", default=None, help="substring filter on task name")
    ap.add_argument("--no-diagnostics", action="store_true")
    args = ap.parse_args()

    if os.path.abspath(args.out) == os.path.abspath(
            os.path.join(RUNNER_DIR, "results", "results.jsonl")):
        raise SystemExit("refusing to write garbage-arm rows into the main corpus")

    os.makedirs(args.scratch, exist_ok=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    tasks = [t for t in TASKS if not args.only or args.only in t]
    missing = [t for t in tasks if t not in BROKEN]
    if "broken" in args.arms.split(",") and missing:
        raise SystemExit(f"no broken-arm implementation for: {', '.join(missing)}")

    with open(args.out, "a", encoding="utf-8") as sink:
        for arm in args.arms.split(","):
            for task in tasks:
                row = one(task, arm, args.tasks_dir, args.scratch,
                          not args.no_diagnostics)
                sink.write(json.dumps(row) + "\n")
                sink.flush()
                flag = "PASS <-- garbage accepted" if row["passed"] else "fail"
                print(f"{arm:7s} {task:9s} {flag}  ({row['wall_s']}s)", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""score.py -- apply ticket 28's decision rule, which was fixed at filing.

The rule is quoted here verbatim rather than paraphrased, and the branch is
selected by arithmetic. Nothing in this file was written after seeing a number:
its whole job is to make the mapping from count to verdict mechanical, so the
verdict cannot drift toward whatever the result turned out to be.

    Score = the number of tasks (of 12) on which ANY garbage arm passes.
      0 tasks  -> the graders discriminate; reading A stands
      1-3      -> localized permissiveness; those tasks are disqualified from
                  any difficulty inference until their suites are repaired
      >= 4/12  -> the ceiling is substantially a grading artifact

    "The weak-model arm is scored separately and does not count toward the 12."

So the score counts arms `empty` and `broken` only. The weak arm is reported
beside it and never added in.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(os.path.dirname(HERE), "runner", "results")

N_TASKS = 12


def rows(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    arms = rows(os.path.join(RESULTS, "negative-control-28.jsonl"))
    weak = rows(os.path.join(RESULTS, "negative-control-28-weak.jsonl"))

    disagreements = [r for r in arms if r.get("agrees") is False]

    counted = {}          # task -> list of arms that passed
    for r in arms:
        if r["arm"] not in ("empty", "broken"):
            continue
        counted.setdefault(r["task"], [])
        if r["passed"]:
            counted[r["task"]].append(r["arm"])

    score = sum(1 for t, a in counted.items() if a)

    if score == 0:
        verdict = ("0 tasks -- the graders discriminate. Reading A stands, the "
                   "ceiling is a fact about task difficulty, and 05 / 23 / 24 "
                   "keep their premises.")
    elif score <= 3:
        verdict = (f"{score} task(s) -- localized permissiveness. Those tasks "
                   f"are disqualified from any difficulty inference until "
                   f"their suites are repaired.")
    else:
        verdict = (f"{score} of 12 -- the ceiling is substantially a grading "
                   f"artifact. Ticket 24 is not interpretable as specified.")

    print(f"tasks graded (empty+broken): {len(counted)} of {N_TASKS}")
    print(f"rows: arms={len(arms)} weak={len(weak)}")
    if len(counted) != N_TASKS:
        print("!! INCOMPLETE -- score below is not the ticket's score")
    if disagreements:
        print(f"!! {len(disagreements)} run_verify/graded_run disagreement(s) "
              f"-- the gate is not deterministic; this outranks the score")
        for r in disagreements:
            print(f"   {r['arm']}/{r['task']}: run_verify={r['passed']} rc={r.get('rc')}")
    print()
    print(f"SCORE = {score} of {N_TASKS}")
    print(f"VERDICT: {verdict}")
    print()

    print("per-task:")
    for task in sorted(counted):
        passed_arms = counted[task]
        mark = "GARBAGE PASS: " + ",".join(passed_arms) if passed_arms else "rejected both arms"
        print(f"  {task:9s} {mark}")

    if weak:
        # run.py writes the outcome under "pass"; run_arms.py (the model-free
        # arms) writes "passed". Reading "passed" here silently scored every
        # weak row a failure -- the direction that understates arm 3.
        wpass = [r for r in weak if r.get("pass")]
        print()
        print(f"weak-model arm (scored separately, NOT in the {N_TASKS}): "
              f"{len(wpass)}/{len(weak)} passed")
        for r in sorted(weak, key=lambda r: r.get("task", "")):
            print(f"  {r.get('task',''):9s} {'PASS' if r.get('pass') else 'fail'}"
                  f"  {r.get('exit_reason', '')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

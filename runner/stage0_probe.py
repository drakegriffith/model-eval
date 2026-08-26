#!/usr/bin/env python3
"""stage0_probe.py -- the invoker for the stage-0 noise probe (issue #26).

Before this file, two things needed for stage 0 of the pre-registered GLM
experiment (docs/studio-handoff/prompt-2-run-experiment.md; model-eval
issue #8) did not exist: a sweep config for the 5-sequential-rep probe
(runner/runs-glm-stage0.yaml, alongside this module) and a caller of
`serving_registry.record_noise_probe` outside its own tests. This module is
that caller, and the documented command is:

    python3 runner/stage0_probe.py --config runner/runs-glm-stage0.yaml \\
        --results <scratch>/results.jsonl --scratch <scratch>/.scratch \\
        --registry-path <scratch>/models.yaml [--mock]

It (1) invokes run.py as a subprocess against the given config, (2) reads
back the resulting rows, tags the ones belonging to this probe's sweep with
`stage: 0`, (3) derives the pre-registered numbers (amendments A1, A3, A6)
through a PURE function of those rows, (4) calls
serving_registry.record_noise_probe with the measured values and persists
the registry, and (5) prints the comment amendment A1/A3/A6 mandate be
posted to model-eval issue #8 before stage 1 runs.

WHY A SEPARATE MODULE RATHER THAN A NEW run.py SUBCOMMAND. run.py's job
stops at "did the row get produced"; everything here -- reading rows back,
deriving numbers from them, deciding whether to re-register K, writing the
registry -- is stage-0-specific analysis that has no business growing
run.py's own CLI surface, and keeping it out means this file can be read,
tested and changed without touching the module four other things already
import.

THE REGISTRY WRITE HAS ITS OWN corpus_guard, THE SAME SHAPE run.py's --mock
GUARD USES FOR results.jsonl/usage.jsonl (issues #23/#24). A --mock run's
rows are not measurements of anything -- they come from `git apply`, not a
model -- so calling record_noise_probe on them and persisting the result to
the LIVE models.yaml would write a fabricated noise-probe measurement into
the registry every other reader trusts. --registry-path defaults to the
live file (the correct target for the one real, non-mock run this script
exists to make), but a --mock invocation that still resolves to the live
registry is refused with corpus_guard.REFUSE_EXIT, before run.py is ever
invoked, exactly like a --mock run that still resolves to the live
results.jsonl.
"""
import argparse
import datetime as _dt
import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus_guard  # noqa: E402
import serving_registry as sr  # noqa: E402

DEFAULT_CONFIG = os.path.join(HERE, "runs-glm-stage0.yaml")
DEFAULT_SWEEP = "glm-stage0"
K_FLIP_THRESHOLD = 10  # A1: "any rep reaching >= 10 acceptance requests"


# --------------------------------------------------------------------------- #
# Pure functions -- driven entirely by fixtures in tests/test_stage0_probe.py,
# no filesystem, no subprocess.
# --------------------------------------------------------------------------- #
def derive_stage0(rows):
    """The pre-registered stage-0 numbers (A1, A3, A6), derived from a list
    of row dicts -- each carrying at least `pass`, `turns`, `wall_s` and
    `acceptance_requests` (None when the run was mocked or unbrokered).

    Refuses an empty list outright: a probe over zero rows asserts nothing
    about noise, and reporting zeroed-out numbers for it would be a silent
    pass indistinguishable from a real 0/0.

    `of` is len(rows), not a hardcoded 5, so a caller that reruns this over
    a different rep count (a re-probe, say) is not silently mis-scored --
    the pre-registration's specific numbers (5 reps, A6's "5/5 identical")
    are a property of the CONFIG this is run against, not of this function.
    """
    of = len(rows)
    if of == 0:
        raise ValueError(
            "derive_stage0: 0 rows inspected; a probe over zero rows proves "
            "nothing about noise (a gate that inspected zero subjects failed)")

    reference = bool(rows[0]["pass"])
    flips = sum(1 for r in rows if bool(r["pass"]) != reference)
    identical = of - flips
    flip_rate = flips / of

    # A6: 5/5 identical -> cut stage-1 reps from 3 to 2; any flip -> keep 3.
    rep_decision = "reps 2" if flips == 0 else "reps 3"

    # A1: any rep at or above the broker's K_CEILING-adjacent threshold of 10
    # acceptance requests re-registers K before stage 1. None entries (mock
    # or unbrokered reps) are not measurements and are excluded, not read as 0.
    acc_values = [r["acceptance_requests"] for r in rows
                 if r.get("acceptance_requests") is not None]
    max_acceptance_requests = max(acc_values) if acc_values else None
    k_flip = any(v >= K_FLIP_THRESHOLD for v in acc_values)
    k_decision = ("re-register K" if k_flip else
                  "K unchanged (no rep reached "
                  f"{K_FLIP_THRESHOLD} acceptance requests)")

    # A3: N = 3 x max(turns), rounded UP to the next multiple of 10. An exact
    # multiple (e.g. 3*10=30) is not bumped further -- math.ceil already
    # leaves an exact multiple alone, so no extra branch is needed to state
    # that rule; the parametrized test pins the case (max_turns=10 -> N=30).
    max_turns = max(r["turns"] for r in rows)
    n_cap = int(math.ceil((3 * max_turns) / 10.0) * 10)

    # Descriptive only (A3: "as a descriptive, not a gate"). Guarded against
    # min(wall_s) == 0: every --mock row never calls run_cli and so never
    # updates wall_s off its 0.0 default, and a ratio against zero elapsed
    # time is not a measurement -- reported absent rather than as inf.
    wall_values = [r["wall_s"] for r in rows]
    max_wall_s = max(wall_values)
    min_wall_s = min(wall_values)
    wall_ratio = (max_wall_s / min_wall_s) if min_wall_s > 0 else None

    return {
        "of": of, "flips": flips, "identical": identical,
        "flip_rate": flip_rate, "rep_decision": rep_decision,
        "max_acceptance_requests": max_acceptance_requests,
        "k_flip": k_flip, "k_decision": k_decision,
        "max_turns": max_turns, "n_cap": n_cap,
        "max_wall_s": max_wall_s, "min_wall_s": min_wall_s,
        "wall_ratio": wall_ratio,
    }


def tag_stage0(rows, sweep=DEFAULT_SWEEP):
    """Return a NEW list, each row belonging to `sweep` carrying `stage: 0`
    and every other row byte-for-byte untouched. Never mutates its input --
    the caller may still need the untagged rows (e.g. to rewrite a results
    file that holds more than this probe's own sweep)."""
    out = []
    for row in rows:
        if row.get("sweep") == sweep:
            tagged = dict(row)
            tagged["stage"] = 0
            out.append(tagged)
        else:
            out.append(row)
    return out


def render_comment(derived, task, model="glm-4.7", driver="claude-code", date=None):
    """The text amendments A1/A3/A6 mandate be posted to model-eval issue #8
    before anything past stage 0 runs, carrying every derived number."""
    date = date or _dt.date.today().isoformat()
    d = derived
    wall_line = (f"wall_s ratio (max/min): {d['wall_ratio']:.2f}"
                if d["wall_ratio"] is not None
                else "wall_s ratio (max/min): n/a (a --mock probe measures no wall time)")
    max_acc = d["max_acceptance_requests"]
    max_acc_line = "n/a" if max_acc is None else str(max_acc)
    lines = [
        f"Stage 0 noise probe -- {task}, {model} x {driver}, "
        f"{d['of']} sequential reps ({date}).",
        "",
        f"flips: {d['flips']}/{d['of']} ({d['identical']}/{d['of']} identical)",
        wall_line,
        f"max(turns): {d['max_turns']}",
        f"max(acceptance_requests): {max_acc_line}",
        "",
        "Decisions (pre-registration A1, A3, A6):",
        f"- reps: {d['rep_decision']}",
        f"- K: {d['k_decision']}",
        f"- N (turn cap): {d['n_cap']} "
        f"(= 3 x max(turns)={d['max_turns']}, rounded up to next multiple of 10)",
        "",
        f"Recorded to registry: {model} x {driver} noise_probe = "
        f"{d['identical']}/{d['of']} identical, flip_rate={d['flip_rate']:.2f}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# I/O -- thin wrappers the pure functions above never need.
# --------------------------------------------------------------------------- #
def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def run_stage0(config, results, scratch, tasks_dir=None, mock=False):
    """Invoke run.py as a subprocess for `config`, then read back and tag
    (in place, on disk) exactly the rows belonging to this probe's sweep.

    Raises rather than returning a short list: a stage-0 probe that produced
    fewer (or more) than the 5 rows its config declared is not a probe
    result, it is a manifest gap, and printing derived numbers from it would
    be a silent pass over a subject count nobody asserted.
    """
    cmd = [sys.executable, os.path.join(HERE, "run.py"),
          "--config", config, "--results", results, "--scratch", scratch]
    if tasks_dir:
        cmd += ["--tasks-dir", tasks_dir]
    if mock:
        cmd.append("--mock")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"run.py exited {proc.returncode} for stage-0 config {config!r}:\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")

    all_rows = read_jsonl(results)
    tagged_all = tag_stage0(all_rows, sweep=DEFAULT_SWEEP)
    write_jsonl(results, tagged_all)

    probe_rows = [r for r in tagged_all if r.get("sweep") == DEFAULT_SWEEP]
    return probe_rows


def _guess_task(rows):
    tasks = sorted({r.get("task") for r in rows if r.get("task")})
    return tasks[0] if len(tasks) == 1 else "/".join(tasks) or "(unknown task)"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--results", required=True,
                    help="where run.py writes this probe's rows (must be a "
                         "scratch path under --mock; see corpus_guard)")
    ap.add_argument("--scratch", required=True,
                    help="run.py's own --scratch, forwarded unchanged")
    ap.add_argument("--tasks-dir", default=None,
                    help="run.py's own --tasks-dir, forwarded unchanged; "
                         "defaults to run.py's own default (the real tasks/)")
    ap.add_argument("--registry-path", default=sr.REGISTRY_PATH,
                    help="models.yaml to record the probe into (default: "
                         "the live registry). Under --mock this must be "
                         "redirected to a scratch copy, or the run is "
                         "refused before run.py is ever invoked.")
    ap.add_argument("--date", default=None,
                    help="probe date stamp; defaults to today")
    ap.add_argument("--mock", action="store_true",
                    help="forwarded to run.py: apply solution.patch instead "
                         "of calling a CLI (no tokens, no inference)")
    ap.add_argument("--expected-reps", type=int, default=5,
                    help="rows the probe's own sweep must produce (default: "
                         "5, the pre-registered stage-0 rep count)")
    args = ap.parse_args(argv)

    if args.mock:
        msg = corpus_guard.refusal_message(
            [(args.registry_path, sr.REGISTRY_PATH, "model registry")],
            "--registry-path at a scratch copy of models.yaml, e.g. "
            "--registry-path /tmp/scratch/models.yaml")
        if msg:
            print(msg, file=sys.stderr)
            sys.exit(corpus_guard.REFUSE_EXIT)

    rows = run_stage0(args.config, args.results, args.scratch,
                      tasks_dir=args.tasks_dir, mock=args.mock)
    if len(rows) != args.expected_reps:
        raise RuntimeError(
            f"stage-0 sweep {DEFAULT_SWEEP!r} produced {len(rows)} row(s) "
            f"in {args.results!r}, expected {args.expected_reps}: rows "
            f"produced / rows dispatched must match, or this is not a "
            f"probe result")

    derived = derive_stage0(rows)
    date = args.date or _dt.date.today().isoformat()

    registry_rows = sr.load_rows(args.registry_path)
    row = sr.find_row(registry_rows, "glm-4.7", "claude-code")
    sr.record_noise_probe(row, flip_rate=derived["flip_rate"], date=date,
                          identical=derived["identical"], of=derived["of"])
    with open(args.registry_path, "w", encoding="utf-8") as f:
        f.write(sr.dump_registry_yaml({"models": registry_rows}))

    comment = render_comment(derived, task=_guess_task(rows), date=date)
    print(comment)
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

It (1) preflights the live server against the registry row (skipped, loudly,
under --mock), (2) invokes run.py as a subprocess against the given config,
(3) reads back the resulting rows, tags the ones belonging to this probe's
sweep with `stage: 0`, (4) partitions them by run_status class and derives
the pre-registered numbers (amendments A1, A3, A6) as a function of the
SCORED rows only, (5) calls serving_registry.record_noise_probe with the
measured values and persists the registry -- but only when every dispatched
rep was scored -- and (6) prints the comment amendments A1/A3/A6 mandate be
posted to model-eval issue #8 before stage 1 runs.

WHY A SEPARATE MODULE RATHER THAN A NEW run.py SUBCOMMAND. run.py's job
stops at "did the row get produced"; everything here -- reading rows back,
deriving numbers from them, deciding whether to re-register K, writing the
registry -- is stage-0-specific analysis that has no business growing
run.py's own CLI surface, and keeping it out means this file can be read,
tested and changed without touching the module four other things already
import.

ONLY SCORED ROWS ARE MEASUREMENTS (run_status.py; amendments A3/A4). A row
whose exit_reason classifies TIMEOUT, INFRA or MOCK is not a fact about the
model -- run.py forces `pass=False` on every non-"ok" exit_reason
(run.py:~1848), so reading exit_reason-blind would let 5 timed-out reps
derive as "0/5 flips, 5/5 identical" and record deterministic_loops: true
from zero evidence. derive_stage0 partitions with
run_status.partition_for_rate (never re-implemented here) and computes every
decision over the SCORED subset only. Two refusal-shaped outcomes follow
from that split, both distinct from a clean recording:

  - zero scored rows (5 timeouts; every --mock row, since "mock" is its own
    excluded class, never "ok") -- derive_stage0 raises before anything is
    printed or written. The CLI turns this into exit 2 ("could not
    determine"), never a silent zero.
  - some but not all rows scored (4 ok + 1 timeout) -- derive_stage0 returns
    numbers computed over the 4, marked `provisional`; the CLI still prints
    the comment (with a PROVISIONAL banner and the lost-runs line) but does
    NOT call record_noise_probe -- a probe with lost reps is re-run, not
    recorded.

N = 0 IS ALSO COULD-NOT-DETERMINE, NEVER A DECISION. Every --mock row's
`turns` sits at run.py's own zero default (mock never calls a model), so
`max(turns) == 0` would otherwise print "N (turn cap): 0" as if 0 were a
real cap. derive_stage0 refuses (RuntimeError) whenever the scored rows'
max(turns) is 0, or any scored row is missing `turns` entirely -- no
comment, no registry write, same "could not determine" posture as the
zero-scored case above.

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
results.jsonl. (Belt and suspenders: every --mock row is also unscored, so
the provisional/refused split above would catch it even without this
guard -- but the guard fires first, before run.py spends a subprocess, and
names the fix in one sentence instead of a stack of derived numbers.)

PREFLIGHT: THE DECLARED CONFIG MATCHING THE ROW IS NOT THE SAME CLAIM AS THE
LIVE SERVER MATCHING THE ROW (serving_registry.py's own module note on
cmd_preflight). run.py's dispatch loop only checks the former -- it compares
this config's declared `serving:` block against the registry row, entirely
from files in version control, which is right for a deterministic gate but
cannot see that LM Studio is actually running some third config. Before a
non-mock dispatch, this module also calls serving_registry.cmd_preflight,
which reads `lms ps` and refuses (exit 3/4/5) if the live server disagrees
or cannot be inspected. Skipped under --mock, loudly, since --mock never
contacts LM Studio anyway.
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
import run_status  # noqa: E402
import serving_registry as sr  # noqa: E402

DEFAULT_CONFIG = os.path.join(HERE, "runs-glm-stage0.yaml")
DEFAULT_SWEEP = "glm-stage0"
# The model/driver this probe measures by default -- GLM stage 0, issue #26.
# Every function below that takes model=/driver= defaults to these two so an
# unqualified call (every existing call site, every existing test) is
# byte-identical to before --model/--driver/--sweep existed on the CLI; a
# second model rides the same probe by passing --model/--driver/--sweep/
# --config, never by editing these.
DEFAULT_MODEL = "glm-4.7"
DEFAULT_DRIVER = "claude-code"
K_FLIP_THRESHOLD = 10  # A1: "any rep reaching >= 10 acceptance requests"

# "Could not determine" -- distinct from corpus_guard.REFUSE_EXIT (3), which
# is a different refusal (the registry-path guard) that never fires in the
# same invocation as this one (that guard only fires under --mock; this one
# only after a real dispatch). Matches this stack's own convention that 2 is
# "config/data rejected", never a silent pass dressed as an empty result.
CANNOT_DETERMINE_EXIT = 2


# --------------------------------------------------------------------------- #
# Pure functions -- driven entirely by fixtures in tests/test_stage0_probe.py,
# no filesystem, no subprocess.
# --------------------------------------------------------------------------- #
def derive_stage0(rows):
    """The pre-registered stage-0 numbers (A1, A3, A6), derived from a list
    of row dicts -- each carrying at least `pass`, `turns`, `wall_s`,
    `exit_reason` and `acceptance_requests` (None when the run was mocked or
    unbrokered).

    ONLY SCORED ROWS (run_status.partition_for_rate: exit_reason "ok" or
    "cap_exhausted") ENTER ANY DECISION. Timeouts, infra failures and mock
    rows are apparatus facts, not model measurements (A3/A4); folding them in
    is exactly how 5 timed-out reps would derive as "5/5 identical".

    Raises ValueError with zero rows scored -- whether because `rows` itself
    is empty or every row was excluded (5 timeouts; an all-mock sweep, since
    "mock" is never "ok") -- because a probe with no scored reps asserts
    nothing about noise, and reporting zeroed-out numbers for it would be a
    silent pass indistinguishable from a real 0/0.

    Raises RuntimeError if the scored rows' max(turns) is 0, or any scored
    row has no `turns` at all: N cannot be derived from that, and 0 printed
    as "N (turn cap): 0" would read as a decision instead of a gap. A --mock
    row never updates `turns` off its zero default, but this never actually
    fires for a --mock sweep in practice -- every mock row is unscored, so
    the zero-scored refusal above fires first.

    `identical`/`flip_rate` are ORDER-INVARIANT: identical is the count of
    the MAJORITY pass/fail outcome among scored rows (not a comparison
    against rows[0]), so [F,T,T,T,T] and [T,T,T,T,F] derive the same numbers
    -- rep order is an artifact of dispatch, not evidence about the model.

    Returns a dict whose `provisional` key is True whenever fewer rows were
    scored than were read back (`produced`); a caller must not call
    record_noise_probe when `provisional` is true (see finalize_stage0).
    """
    produced = len(rows)
    scored, excluded = run_status.partition_for_rate(rows)
    of = len(scored)
    if of == 0:
        raise ValueError(
            f"derive_stage0: 0 of {produced} row(s) were SCORED "
            f"(excluded: {run_status.format_excluded(excluded) or 'none'}); "
            f"a probe with zero scored reps proves nothing about noise (a "
            f"gate that inspected zero subjects failed)")

    # N = 3 x max(turns), rounded UP to the next multiple of 10 (A3). Refused
    # before anything else is computed: an undetermined N invalidates the
    # whole probe, not just the N line.
    turns_values = [r.get("turns") for r in scored]
    if any(t is None for t in turns_values) or max(turns_values) == 0:
        raise RuntimeError(
            f"derive_stage0: max(turns) over the {of} scored row(s) is "
            f"{max(turns_values) if all(t is not None for t in turns_values) else 'undetermined (missing turns)'} "
            f"-- N cannot be derived from that. 0 is could-not-determine, "
            f"never a decision (a --mock row never updates `turns` off its "
            f"zero default; this fires only if a real dispatch produced a "
            f"scored 0-turn row, which would itself be a data problem)")
    max_turns = max(turns_values)
    n_cap = int(math.ceil((3 * max_turns) / 10.0) * 10)

    # Order-invariant: identical = the majority outcome's count, over SCORED
    # rows only.
    true_count = sum(1 for r in scored if bool(r["pass"]))
    false_count = of - true_count
    identical = max(true_count, false_count)
    flips = of - identical
    flip_rate = 1 - (identical / of)

    # A6: 5/5 identical -> cut stage-1 reps from 3 to 2; any flip -> keep 3.
    rep_decision = "reps 2" if flips == 0 else "reps 3"

    # A1: any scored rep at or above the broker's K_CEILING-adjacent
    # threshold of 10 acceptance requests re-registers K before stage 1.
    # None entries (mock or unbrokered reps) are not measurements and are
    # excluded, not read as 0.
    acc_values = [r["acceptance_requests"] for r in scored
                 if r.get("acceptance_requests") is not None]
    max_acceptance_requests = max(acc_values) if acc_values else None
    k_flip = any(v >= K_FLIP_THRESHOLD for v in acc_values)
    k_decision = ("re-register K" if k_flip else
                  "K unchanged (no rep reached "
                  f"{K_FLIP_THRESHOLD} acceptance requests)")

    # Descriptive only (A3: "as a descriptive, not a gate"). Guarded against
    # min(wall_s) == 0: a --mock row never calls run_cli and so never updates
    # wall_s off its 0.0 default, and a ratio against zero elapsed time is
    # not a measurement -- reported absent rather than as inf.
    wall_values = [r["wall_s"] for r in scored]
    max_wall_s = max(wall_values)
    min_wall_s = min(wall_values)
    wall_ratio = (max_wall_s / min_wall_s) if min_wall_s > 0 else None

    return {
        "produced": produced, "of": of, "excluded": excluded,
        "provisional": of < produced,
        "flips": flips, "identical": identical, "flip_rate": flip_rate,
        "rep_decision": rep_decision,
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


def render_comment(derived, task, model=DEFAULT_MODEL, driver=DEFAULT_DRIVER,
                   date=None, dispatched=None):
    """The text amendments A1/A3/A6 mandate be posted to model-eval issue #8
    before anything past stage 0 runs, carrying every derived number plus,
    when the probe is provisional, the lost-runs manifest (rows scored /
    rows produced / rows dispatched) instead of a clean recording line."""
    date = date or _dt.date.today().isoformat()
    d = derived
    dispatched = d["produced"] if dispatched is None else dispatched
    wall_line = (f"wall_s ratio (max/min): {d['wall_ratio']:.2f}"
                if d["wall_ratio"] is not None
                else "wall_s ratio (max/min): n/a (a --mock probe measures no wall time)")
    max_acc = d["max_acceptance_requests"]
    max_acc_line = "n/a" if max_acc is None else str(max_acc)
    lost = run_status.format_excluded(d["excluded"]) or "none"

    lines = [
        f"Stage 0 noise probe -- {task}, {model} x {driver}, "
        f"{d['of']} scored reps ({date}).",
    ]
    if d["provisional"]:
        lines += [
            "",
            "STATUS: PROVISIONAL -- fewer reps scored than dispatched. "
            "NOT recorded to the registry; re-run stage 0 to completion "
            "before trusting these numbers.",
        ]
    lines += [
        "",
        f"rows scored / rows produced / rows dispatched: "
        f"{d['of']}/{d['produced']}/{dispatched}  (lost: {lost})",
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
    ]
    if d["provisional"]:
        lines.append(
            "Recorded to registry: NOT RECORDED (provisional -- see STATUS above)")
    else:
        lines.append(
            f"Recorded to registry: {model} x {driver} noise_probe = "
            f"{d['identical']}/{d['of']} identical, flip_rate={d['flip_rate']:.2f}")
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


def run_preflight(registry_path, model=DEFAULT_MODEL, driver=DEFAULT_DRIVER,
                  lms_output=None):
    """Call serving_registry.cmd_preflight in-process (no subprocess of our
    own) against the live LM Studio, and return its exit code unchanged (0
    = OK; 3/4/5 = MISMATCH/UNINSPECTABLE/NOT_LOADED, cmd_preflight's own
    constants -- never a pass by omission).

    `lms_output` forwards to cmd_preflight's own file-injection path (read
    `lms ps` output from a file instead of running it), which is how tests
    exercise this without ever contacting the real Mac Studio."""
    ns = argparse.Namespace(path=registry_path, model=model, driver=driver,
                            lms_output=lms_output)
    return sr.cmd_preflight(ns)


def run_stage0(config, results, scratch, tasks_dir=None, mock=False,
              sweep=DEFAULT_SWEEP):
    """Invoke run.py as a subprocess for `config`, then read back and tag
    (in place, on disk) exactly the rows belonging to `sweep` (default: this
    probe's own GLM sweep).

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
    tagged_all = tag_stage0(all_rows, sweep=sweep)
    write_jsonl(results, tagged_all)

    probe_rows = [r for r in tagged_all if r.get("sweep") == sweep]
    return probe_rows


def _guess_task(rows):
    tasks = sorted({r.get("task") for r in rows if r.get("task")})
    return tasks[0] if len(tasks) == 1 else "/".join(tasks) or "(unknown task)"


def finalize_stage0(rows, registry_path, model=DEFAULT_MODEL, driver=DEFAULT_DRIVER,
                    date=None, task=None, dispatched=None):
    """Derive, decide whether to record, and render the comment -- one place
    so main() and tests share the exact same control flow.

    Returns (comment_text, recorded: bool). Raises whatever derive_stage0
    raises (ValueError: zero scored rows; RuntimeError: N undetermined)
    BEFORE reading or writing the registry and before rendering anything --
    a caller that catches the exception is guaranteed no partial write
    happened.

    record_noise_probe is called, and the registry is persisted, only when
    derived["provisional"] is False -- i.e. every dispatched rep scored. A
    provisional probe still gets its comment (marked PROVISIONAL, carrying
    the lost-runs line) so the plumbing that produced it is still visible,
    but recorded=False and the registry file is untouched.
    """
    derived = derive_stage0(rows)
    date = date or _dt.date.today().isoformat()
    task = task or _guess_task(rows)

    recorded = False
    if not derived["provisional"]:
        registry_rows = sr.load_rows(registry_path)
        row = sr.find_row(registry_rows, model, driver)
        sr.record_noise_probe(row, flip_rate=derived["flip_rate"], date=date,
                              identical=derived["identical"], of=derived["of"])
        with open(registry_path, "w", encoding="utf-8") as f:
            f.write(sr.dump_registry_yaml({"models": registry_rows}))
        recorded = True

    comment = render_comment(derived, task=task, model=model, driver=driver,
                             date=date, dispatched=dispatched)
    return comment, recorded


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="registry model name (no -local suffix -- the "
                         "config's own serving/configs blocks carry that, "
                         "same convention as glm-4.7 today; default: "
                         f"{DEFAULT_MODEL!r})")
    ap.add_argument("--driver", default=DEFAULT_DRIVER,
                    help=f"registry driver name (default: {DEFAULT_DRIVER!r})")
    ap.add_argument("--sweep", default=DEFAULT_SWEEP,
                    help="sweep name this probe's config declares -- what "
                         "tag_stage0/run_stage0 filter results.jsonl on "
                         f"(default: {DEFAULT_SWEEP!r})")
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
                         "of calling a CLI (no tokens, no inference). Also "
                         "skips the live preflight, which --mock never needs.")
    ap.add_argument("--expected-reps", type=int, default=5,
                    help="rows the probe's own sweep must produce (default: "
                         "5, the pre-registered stage-0 rep count)")
    ap.add_argument("--preflight-lms-output", default=None,
                    help="read `lms ps` output from a FILE instead of "
                         "running it, for the non-mock preflight step -- "
                         "how tests exercise it without touching LM Studio")
    args = ap.parse_args(argv)

    # Issue #28: same guard run.py itself applies, run here BEFORE run.py is
    # ever invoked (as a subprocess, below) -- a --scratch forwarded
    # unchanged that resolves inside the live results directory would
    # otherwise let run.py's own subprocess create the checkout first and
    # this module only find out after the fact. Unconditional, same as
    # run.py's own check: not gated on --mock.
    scratch_msg = corpus_guard.refuse_scratch_inside_results(
        args.scratch, os.path.join(HERE, "results"))
    if scratch_msg:
        print(scratch_msg, file=sys.stderr)
        sys.exit(corpus_guard.REFUSE_EXIT)

    if args.mock:
        msg = corpus_guard.refusal_message(
            [(args.registry_path, sr.REGISTRY_PATH, "model registry")],
            "--registry-path at a scratch copy of models.yaml, e.g. "
            "--registry-path /tmp/scratch/models.yaml")
        if msg:
            print(msg, file=sys.stderr)
            sys.exit(corpus_guard.REFUSE_EXIT)
        print("preflight: skipped (--mock never contacts LM Studio)")
    else:
        code = run_preflight(args.registry_path, model=args.model,
                             driver=args.driver,
                             lms_output=args.preflight_lms_output)
        if code != 0:
            sys.exit(code)

    rows = run_stage0(args.config, args.results, args.scratch,
                      tasks_dir=args.tasks_dir, mock=args.mock,
                      sweep=args.sweep)
    if len(rows) != args.expected_reps:
        raise RuntimeError(
            f"stage-0 sweep {args.sweep!r} produced {len(rows)} row(s) "
            f"in {args.results!r}, expected {args.expected_reps}: rows "
            f"produced / rows dispatched must match, or this is not a "
            f"probe result")

    try:
        comment, _recorded = finalize_stage0(
            rows, args.registry_path, model=args.model, driver=args.driver,
            date=args.date, dispatched=args.expected_reps)
    except (ValueError, RuntimeError) as e:
        print(f"stage0_probe: refused -- {e}", file=sys.stderr)
        sys.exit(CANNOT_DETERMINE_EXIT)

    print(comment)
    return 0


if __name__ == "__main__":
    sys.exit(main())

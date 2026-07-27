#!/usr/bin/env python3
"""judge.py — blind dual judge for a passing run.

Given a run_id, extract the git diff from that run's scratch dir, build a blind
rubric prompt (NO mention of which model produced the code), send it to BOTH
`claude -p --model claude-opus-4-8` and `codex exec` (default model), parse the
0-10 scores, and append a row to runner/results/judgments.jsonl.

Rubric axes (0-10 each, with a one-line justification):
  correctness  — correct beyond just passing the visible tests
  simplicity   — minimal, no needless complexity
  idiomatic    — idiomatic style for the language
  spec         — adherence to the task's stated requirements

MOCK: GAUNTLET_MOCK=1 skips the CLIs and emits fixed placeholder scores so the
plumbing is testable without tokens.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage_ledger  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER_DIR = os.path.join(ROOT, "runner")

RUBRIC = """You are a strict senior code reviewer. Below is a git diff that solves a
programming task. You do not know who or what wrote it. Score the SOLUTION on four
axes, each an integer 0-10, and give a one-line justification for each:

- correctness: is it correct beyond just passing the visible tests? (edge cases, hidden inputs)
- simplicity: is it the minimal change, free of needless complexity?
- idiomatic: is it idiomatic, clean style for the language?
- spec: does it adhere to what the task asked for?

Respond with ONLY a JSON object, no prose around it, exactly this shape:
{"correctness": {"score": N, "why": "..."},
 "simplicity": {"score": N, "why": "..."},
 "idiomatic": {"score": N, "why": "..."},
 "spec": {"score": N, "why": "..."}}

TASK PROMPT:
%(prompt)s

DIFF:
%(diff)s
"""


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_diff(scratch):
    subprocess.run(["git", "add", "-A"], cwd=scratch,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r = subprocess.run(["git", "diff", "--cached", "HEAD"], cwd=scratch,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return r.stdout


def read_prompt_for_run(run_id, tasks_dir):
    # run_id = sweep--model--effort--harness--<task>--rEP
    parts = run_id.split("--")
    task = parts[-2] if len(parts) >= 2 else ""
    tdir = os.path.join(tasks_dir, task)
    out = []
    for fn in ("PROMPT.md", "TICKET.md", "SPEC.md"):
        p = os.path.join(tdir, fn)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out.append(f.read().strip())
    return "\n\n".join(out) or "(task prompt unavailable)"


def extract_json(text):
    """Pull the first JSON object out of a CLI response blob."""
    if not text:
        return None
    # claude --output-format json wraps the answer in .result; try that first
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "result" in obj:
            text = obj["result"]
    except json.JSONDecodeError:
        pass
    # codex JSONL: find the last agent_message text
    msg = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "item.completed":
            item = ev.get("item", {})
            if item.get("type") == "agent_message":
                msg = item.get("text")
    if msg:
        text = msg
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


CLAUDE_JUDGE_MODEL = "claude-opus-4-8"


def run_claude_judge(prompt):
    """Raw stdout from the claude head. Separate from parsing so the same bytes
    feed both the score extraction and the token ledger (ticket 20 item 3)."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    r = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json",
         "--model", CLAUDE_JUDGE_MODEL, "--dangerously-skip-permissions"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env=env, timeout=900)
    return r.stdout


def run_codex_judge(prompt):
    """Raw stdout from the codex head. See run_claude_judge."""
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    r = subprocess.run(
        ["codex", "exec", "--json", "--skip-git-repo-check",
         "--dangerously-bypass-approvals-and-sandbox", prompt],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, env=env, timeout=900)
    return r.stdout


def call_claude_judge(prompt):
    return extract_json(run_claude_judge(prompt))


def call_codex_judge(prompt):
    return extract_json(run_codex_judge(prompt))


# --------------------------------------------------------------------------- #
# Metering -- ticket 20 item 3. Judge calls were unledgered, which is why
# ticket 01's retired 70/20/10 split had to guess at them. One usage.jsonl row
# per judge CALL, same schema as run.py's worker rows, joinable by
# judged_run_id. Ticket 06 sizes the panel off these measured rows, not a
# percentage of worker spend.
# --------------------------------------------------------------------------- #
def judge_model_id(family, raw, declared):
    """The model id the CLI itself reports, falling back to what we asked for.

    The claude head names the model it actually served in `modelUsage`; the
    codex head names no model anywhere in its JSONL and we invoke it without
    --model, so its id is genuinely unknown and is recorded as None rather
    than guessed at.
    """
    if family == "claude":
        try:
            keys = list((json.loads(raw).get("modelUsage") or {}).keys())
        except (json.JSONDecodeError, AttributeError):
            keys = []
        if len(keys) == 1:
            return keys[0]
    return declared


def meter_judge_call(judged_run_id, head, family, declared_model, raw, usage_path):
    """Append one usage row for a judge call. Returns the row, or None when the
    CLI's usage could not be read -- an unreadable call is not a free call, and
    a 0-token row would be a fabricated measurement.
    """
    detail = usage_ledger.parse_usage_detailed(family, raw)
    if not detail or (detail["tokens_in"] == 0 and detail["tokens_out"] == 0):
        print(f"WARN: judge head {head} on {judged_run_id}: usage unreadable, "
              f"NOT ledgered (call still happened)", file=sys.stderr)
        return None
    model_id = judge_model_id(family, raw, declared_model)
    row = usage_ledger.build_usage_row(
        {"run_id": f"judge-{head}--{judged_run_id}", "ts": now_iso(),
         "model": model_id, "tokens_in": 0, "tokens_out": 0},
        family, usage_detail=detail, model_id=model_id,
        kind="judge", judged_run_id=judged_run_id)
    usage_ledger.append_usage_row(usage_path, row)
    return row


MOCK_SCORES = {
    "correctness": {"score": 7, "why": "mock"},
    "simplicity": {"score": 7, "why": "mock"},
    "idiomatic": {"score": 7, "why": "mock"},
    "spec": {"score": 7, "why": "mock"},
}


def passing_run_ids(results_path):
    """Read results.jsonl and return run_ids of runs whose machine gate passed."""
    ids = []
    if not os.path.exists(results_path):
        return ids
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("pass"):
                ids.append(rec["run_id"])
    return ids


def already_judged(out_path):
    ids = set()
    if not os.path.exists(out_path):
        return ids
    with open(out_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["run_id"])
            except Exception:
                continue
    return ids


def judge_one(run_id, scratch_root, tasks_dir, out_path, mock, usage_path=None):
    scratch = os.path.abspath(os.path.join(scratch_root, run_id))
    tasks_dir = os.path.abspath(tasks_dir)
    if not os.path.isdir(scratch):
        print(f"skip {run_id}: scratch dir not found", file=sys.stderr)
        return False
    diff = get_diff(scratch)
    prompt_text = read_prompt_for_run(run_id, tasks_dir)
    rubric = RUBRIC % {"prompt": prompt_text, "diff": diff}

    if mock:
        # No CLI ran, so no tokens were spent and nothing is ledgered.
        j_claude, j_codex = MOCK_SCORES, MOCK_SCORES
    else:
        raw_claude = run_claude_judge(rubric)
        raw_codex = run_codex_judge(rubric)
        j_claude, j_codex = extract_json(raw_claude), extract_json(raw_codex)
        if usage_path:
            meter_judge_call(run_id, "claude", "claude", CLAUDE_JUDGE_MODEL,
                             raw_claude, usage_path)
            meter_judge_call(run_id, "codex", "codex", None,
                             raw_codex, usage_path)

    row = {"run_id": run_id, "ts": now_iso(),
           "judge_claude": j_claude, "judge_codex": j_codex}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, (json.dumps(row) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    print(f"judged {run_id}: claude={_avg(j_claude)} codex={_avg(j_codex)}")
    return True


def main():
    ap = argparse.ArgumentParser(description="blind dual judge")
    ap.add_argument("run_id", nargs="?", default=None,
                    help="single run to judge; omit with --from-results to judge all passing runs")
    ap.add_argument("--from-results", default=None,
                    help="results.jsonl path; judge every passing run not yet judged")
    ap.add_argument("--mock", action="store_true",
                    help="skip the CLIs and emit fixed placeholder scores (no tokens)")
    ap.add_argument("--scratch", default=os.path.join(ROOT, ".scratch"))
    ap.add_argument("--tasks-dir", default=os.path.join(ROOT, "tasks"))
    ap.add_argument("--out", default=os.path.join(RUNNER_DIR, "results", "judgments.jsonl"))
    ap.add_argument("--usage", default=os.path.join(RUNNER_DIR, "results", "usage.jsonl"),
                    help="append one usage row per judge call (ticket 20 item 3); "
                         "set empty to disable")
    args = ap.parse_args()

    mock = args.mock or bool(os.environ.get("GAUNTLET_MOCK"))

    if args.from_results:
        run_ids = passing_run_ids(args.from_results)
        done = already_judged(args.out)
        todo = [r for r in run_ids if r not in done]
        print(f"from-results: passing={len(run_ids)} already_judged={len(done)} "
              f"to_judge={len(todo)} mock={mock}")
        n = 0
        for rid in todo:
            if judge_one(rid, args.scratch, args.tasks_dir, args.out, mock,
                         usage_path=args.usage or None):
                n += 1
        print(f"done: judged {n} run(s)")
        if n < len(todo):
            print(f"FAIL: {len(todo) - n} run(s) skipped (scratch dirs missing? "
                  f"check --scratch/--tasks-dir)", file=sys.stderr)
            sys.exit(1)
        return

    if not args.run_id:
        ap.error("provide a run_id or --from-results")
    if not judge_one(args.run_id, args.scratch, args.tasks_dir, args.out, mock,
                     usage_path=args.usage or None):
        sys.exit(1)


def _avg(j):
    if not j:
        return None
    vals = [v.get("score") for v in j.values() if isinstance(v, dict) and isinstance(v.get("score"), (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


if __name__ == "__main__":
    main()

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


def call_claude_judge(prompt):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    r = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json",
         "--model", "claude-opus-4-8", "--dangerously-skip-permissions"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env=env, timeout=900)
    return extract_json(r.stdout)


def call_codex_judge(prompt):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    r = subprocess.run(
        ["codex", "exec", "--json", "--skip-git-repo-check",
         "--dangerously-bypass-approvals-and-sandbox", prompt],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, env=env, timeout=900)
    return extract_json(r.stdout)


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


def judge_one(run_id, scratch_root, tasks_dir, out_path, mock):
    scratch = os.path.abspath(os.path.join(scratch_root, run_id))
    tasks_dir = os.path.abspath(tasks_dir)
    if not os.path.isdir(scratch):
        print(f"skip {run_id}: scratch dir not found", file=sys.stderr)
        return False
    diff = get_diff(scratch)
    prompt_text = read_prompt_for_run(run_id, tasks_dir)
    rubric = RUBRIC % {"prompt": prompt_text, "diff": diff}

    if mock:
        j_claude, j_codex = MOCK_SCORES, MOCK_SCORES
    else:
        j_claude = call_claude_judge(rubric)
        j_codex = call_codex_judge(rubric)

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
            if judge_one(rid, args.scratch, args.tasks_dir, args.out, mock):
                n += 1
        print(f"done: judged {n} run(s)")
        if n < len(todo):
            print(f"FAIL: {len(todo) - n} run(s) skipped (scratch dirs missing? "
                  f"check --scratch/--tasks-dir)", file=sys.stderr)
            sys.exit(1)
        return

    if not args.run_id:
        ap.error("provide a run_id or --from-results")
    if not judge_one(args.run_id, args.scratch, args.tasks_dir, args.out, mock):
        sys.exit(1)


def _avg(j):
    if not j:
        return None
    vals = [v.get("score") for v in j.values() if isinstance(v, dict) and isinstance(v.get("score"), (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


if __name__ == "__main__":
    main()

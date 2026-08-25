"""test_impossible_row_dedupe.py -- a structurally-impossible cell is recorded
once, not once per invocation.

WHAT WAS OPEN. `record_structurally_impossible` appends unconditionally, and the
runner is resume-friendly by design: `main()` is re-invoked to pick up pending
work, and every re-invocation re-walks the whole matrix. The impossible cells are
still impossible, so each pass appended the same run_id again.

`existing_ids()` does not stop it, because that set is built from rows whose
exit_reason is "ok" or "cap_exhausted" -- a structurally_impossible row is
neither, deliberately, since it must never look like a completed run.

The consequence is a corpus that inflates on its own. One re-invocation per day
adds one duplicate status row per day, so `structurally_impossible=N` in any
report counts INVOCATIONS, not cells. The stage-1 manifest ("rows produced /
rows dispatched, with distinct counts for timeout and infra statuses") would be
reporting a number that grows while nothing runs.

This guard is ported from PR #16, which is the one thing that branch had and
this one lacked.
"""
import json
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(RUNNER_DIR)
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

GOOD_SERVING = {"parallel": 1, "context_length": 131072, "max_tokens": 8192,
                "temperature": 0, "seed": 42}


@pytest.fixture
def task_tree(tmp_path):
    tasks = tmp_path / "tasks"
    task_dir = tasks / "t2-py-a"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("x\n", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do it\n", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 0\n",
                                        encoding="utf-8")
    return str(tasks)


@pytest.fixture
def impossible_config(tmp_path):
    """pi tops out at harness level 2, so L5 under pi cannot exist."""
    lines = ["defaults:", "  timeout_t1_t2_s: 1200", "  seed: 1337", "", "serving:"]
    for key, value in GOOD_SERVING.items():
        lines.append(f"  {key}: {value}")
    lines += ["", "sweeps:", "  - name: imp", "    driver: pi",
              "    harness_level: 5", "    harness: false", "    reps: [1]",
              "    tasks: [t2-py-a]", "    configs:",
              "      - {model: glm-4.7-local, effort: high}"]
    path = tmp_path / "imp.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def invoke(config, task_tree, results, scratch):
    proc = subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "run.py"),
         "--config", config, "--tasks-dir", task_tree, "--mock",
         "--results", str(results), "--scratch", str(scratch)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        timeout=180)
    assert proc.returncode == 0, proc.stdout
    return proc


def rows_in(results):
    if not os.path.exists(results):
        return []
    return [json.loads(l) for l in
            open(results, encoding="utf-8").read().splitlines() if l.strip()]


# --------------------------------------------------------------------------- #
# The regression
# --------------------------------------------------------------------------- #
def test_two_invocations_record_one_impossible_row(tmp_path, task_tree,
                                                   impossible_config):
    """The verifier's probe: rows=1 after the first run, rows=2 after the second."""
    results = tmp_path / "results.jsonl"
    scratch = tmp_path / "scratch"

    invoke(impossible_config, task_tree, results, scratch)
    after_first = rows_in(results)
    invoke(impossible_config, task_tree, results, scratch)
    after_second = rows_in(results)

    assert len(after_first) == 1, after_first
    assert len(after_second) == 1, (
        f"the same impossible cell was recorded again: {after_second}")


def test_the_recorded_row_is_still_the_right_one(tmp_path, task_tree,
                                                 impossible_config):
    """The positive control. Deduping by never writing anything would satisfy
    the assertion above and lose the cell entirely."""
    results = tmp_path / "results.jsonl"
    invoke(impossible_config, task_tree, results, tmp_path / "scratch")

    row = rows_in(results)[0]

    assert row["exit_reason"] == "structurally_impossible"
    assert row["driver"] == "pi"
    assert row["harness_level"] == 5
    assert row["pass"] is None


def test_a_second_distinct_impossible_cell_is_still_recorded(tmp_path, task_tree):
    """Dedupe is per run_id, not a one-row-ever latch."""
    lines = ["defaults:", "  timeout_t1_t2_s: 1200", "  seed: 1337", "", "serving:"]
    for key, value in GOOD_SERVING.items():
        lines.append(f"  {key}: {value}")
    lines += ["", "sweeps:", "  - name: imp", "    driver: pi",
              "    harness_level: 5", "    harness: false", "    reps: [1, 2]",
              "    tasks: [t2-py-a]", "    configs:",
              "      - {model: glm-4.7-local, effort: high}"]
    config = tmp_path / "imp2.yaml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    results = tmp_path / "results.jsonl"

    invoke(str(config), task_tree, results, tmp_path / "scratch")
    invoke(str(config), task_tree, results, tmp_path / "scratch")

    rows = rows_in(results)
    assert len(rows) == 2, rows
    assert len({r["run_id"] for r in rows}) == 2


def test_recorded_impossible_ids_reads_only_impossible_rows(tmp_path):
    """The helper must not treat an ordinary row as already-recorded, or a real
    run whose id collided would be skipped."""
    results = tmp_path / "results.jsonl"
    with open(results, "w", encoding="utf-8") as f:
        f.write(json.dumps({"run_id": "a", "exit_reason": "ok"}) + "\n")
        f.write(json.dumps({"run_id": "b",
                            "exit_reason": "structurally_impossible"}) + "\n")

    ids = runner.recorded_impossible_ids(str(results))

    assert ids == {"b"}


def test_a_missing_results_file_is_an_empty_set(tmp_path):
    assert runner.recorded_impossible_ids(str(tmp_path / "nope.jsonl")) == set()


def test_a_corrupt_line_does_not_stop_the_scan(tmp_path):
    """A half-written line from a killed run must not make the guard forget
    every id after it -- that would silently restore the duplication."""
    results = tmp_path / "results.jsonl"
    with open(results, "w", encoding="utf-8") as f:
        f.write("{not json\n")
        f.write(json.dumps({"run_id": "b",
                            "exit_reason": "structurally_impossible"}) + "\n")

    assert runner.recorded_impossible_ids(str(results)) == {"b"}

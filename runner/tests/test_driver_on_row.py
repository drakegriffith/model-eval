"""test_driver_on_row.py -- the driver reaches the row, and the readers refuse to
pool two drivers into one number.

WHAT WAS OPEN. PR #17 taught build_runs to carry `driver` and `harness_level`,
taught the gate to require the driver, and shipped a pi vehicle-contrast arm in
runs-glm-stage1.yaml. But `execute_run`'s row dict never wrote either field. Only
`record_structurally_impossible` did -- and both tests that asserted "driver is on
the row" asserted it on THAT row, which is a non-discriminating control: they
passed while every actually-executed row carried no driver at all.

The consequence is the thing the registry exists to prevent. findings.md reports
pi as a separately-reported vehicle contrast -- pi has no hooks and no subagents,
so the driver is part of the treatment. With no driver on the row, a corpus of
3/3 claude-code passes and 0/3 pi passes renders as ONE model row at 50%, and the
stage-1 config's own "group by driver" instruction is unexecutable because the
column it names does not exist.

So this file asserts on rows produced by the REAL execution path (--mock, which
writes a genuine results row without calling a model), never on the
structurally-impossible row.

No model is invoked anywhere in this file.
"""
import json
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(RUNNER_DIR)
sys.path.insert(0, RUNNER_DIR)
import tables  # noqa: E402

GOOD_SERVING = {"parallel": 1, "context_length": 131072, "max_tokens": 8192,
                "temperature": 0, "seed": 42}


@pytest.fixture
def task_tree(tmp_path):
    """A minimal task the mock path can execute end to end."""
    tasks = tmp_path / "tasks"
    task_dir = tasks / "t2-py-a"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it\n", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do the task\n", encoding="utf-8")
    (task_dir / "verify.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    return str(tasks)


def write_config(tmp_path, driver="claude-code", harness_level=None):
    lines = ["defaults:", "  timeout_t1_t2_s: 1200", "  seed: 1337", "", "serving:"]
    for key, value in GOOD_SERVING.items():
        lines.append(f"  {key}: {value}")
    lines += ["", "sweeps:", "  - name: drv", f"    driver: {driver}"]
    if harness_level is not None:
        lines.append(f"    harness_level: {harness_level}")
    lines += ["    harness: false", "    reps: [1]", "    tasks: [t2-py-a]",
              "    configs:", "      - {model: glm-4.7-local, effort: high}"]
    path = tmp_path / f"drv-{driver}.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def execute_mock(tmp_path, task_tree, driver="claude-code", harness_level=None):
    """Run the REAL execution path under --mock and return the row it wrote."""
    results = tmp_path / f"results-{driver}.jsonl"
    proc = subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "run.py"),
         "--config", write_config(tmp_path, driver, harness_level),
         "--tasks-dir", task_tree, "--mock",
         "--results", str(results),
         "--scratch", str(tmp_path / f"scratch-{driver}")],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        timeout=180)
    assert proc.returncode == 0, proc.stdout
    lines = [l for l in results.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1, f"expected one executed row, got {len(lines)}"
    row = json.loads(lines[0])
    # The discriminating check: this is an EXECUTED row, not the
    # structurally-impossible one the old tests asserted on.
    assert row["exit_reason"] != "structurally_impossible"
    return row


# --------------------------------------------------------------------------- #
# The fields reach an executed row
# --------------------------------------------------------------------------- #
def test_an_executed_row_records_its_driver(tmp_path, task_tree):
    row = execute_mock(tmp_path, task_tree, driver="claude-code")

    assert row["driver"] == "claude-code"


def test_an_executed_pi_row_records_pi_not_the_default(tmp_path, task_tree):
    """The one that matters: pi is a separately-reported vehicle contrast, so a
    pi row that cannot be told apart from a claude-code row is worse than no row."""
    row = execute_mock(tmp_path, task_tree, driver="pi")

    assert row["driver"] == "pi"


def test_an_executed_row_records_its_harness_level(tmp_path, task_tree):
    row = execute_mock(tmp_path, task_tree, driver="claude-code", harness_level=2)

    assert row["harness_level"] == 2


def test_harness_level_is_none_rather_than_zero_when_undeclared(tmp_path, task_tree):
    """`harness` is the pre-ladder boolean and False is not level 0. A config
    that declares no rung must not have one invented for it."""
    row = execute_mock(tmp_path, task_tree, driver="claude-code")

    assert row["harness_level"] is None
    assert row["harness"] is False


# --------------------------------------------------------------------------- #
# The readers refuse to pool two drivers
# --------------------------------------------------------------------------- #
def rows_for(driver, n, passes, task="t1-py-a"):
    out = []
    for i in range(n):
        out.append({
            "run_id": f"s--glm-4.7-local--high--bare--{task}--{driver}-r{i}",
            "sweep": "s", "model": "glm-4.7-local", "model_id": "glm-4.7-local",
            "effort": "high", "task": task, "rep": i, "harness": False,
            "driver": driver, "pass": i < passes, "exit_reason": "ok",
            "status_class": "scored", "tokens_in": 100, "tokens_out": 100,
            "wall_s": 1.0, "loc_changed": 10, "turns": 1,
            "tokens_in_status": "measured", "invocation_mode": "multi_turn",
        })
    return out


def test_two_drivers_do_not_render_as_one_pooled_model_row():
    """The verifier's probe: 3/3 claude-code and 0/3 pi. Pooled that is one 50%
    row, which is exactly the merge findings.md forbids."""
    corpus = rows_for("claude-code", 3, 3) + rows_for("pi", 3, 0)

    out = tables.table1_effort_ladder(corpus, {})

    assert "50%" not in out, f"the two drivers were pooled into one rate:\n{out}"
    assert "100%" in out and "0%" in out, out


def test_the_pooled_row_is_split_and_each_driver_is_named():
    corpus = rows_for("claude-code", 3, 3) + rows_for("pi", 3, 0)

    out = tables.table1_effort_ladder(corpus, {})

    assert "claude-code" in out
    assert "pi" in out


def test_a_single_driver_corpus_is_unchanged():
    """The control. Splitting must not restate every existing single-driver
    table, or 268 archived rows start rendering differently for no reason."""
    corpus = rows_for("claude-code", 4, 2)

    out = tables.table1_effort_ladder(corpus, {})

    assert "50%" in out
    # No driver suffix on a corpus that has nothing to disambiguate.
    assert "glm-4.7-local |" in out


def test_a_corpus_with_no_driver_field_still_renders():
    """Every one of the 268 archived rows predates the field."""
    corpus = [dict(r) for r in rows_for("claude-code", 2, 1)]
    for r in corpus:
        del r["driver"]

    out = tables.table1_effort_ladder(corpus, {})

    assert "50%" in out


def test_the_report_says_out_loud_that_a_model_ran_under_two_drivers():
    """Silence is not evidence. A reader who does not already know the pi arm
    exists must not have to infer it from a row count."""
    corpus = rows_for("claude-code", 3, 3) + rows_for("pi", 3, 0)

    out = tables.build_report(corpus, {})

    assert "driver" in out.lower()
    assert "pi" in out


# --------------------------------------------------------------------------- #
# table4_hybrid_vs_solo, report_block, section_wilson (issue #25)
# --------------------------------------------------------------------------- #
def test_table4_splits_a_t3_model_that_ran_under_two_drivers():
    """Positive control: one T3 claude-code row and one T3 pi row must render
    as two lines, never pooled into one hybrid/solo cell."""
    corpus = rows_for("claude-code", 3, 3, task="t3-a") + rows_for("pi", 3, 0, task="t3-a")

    out = tables.table4_hybrid_vs_solo(corpus)

    data_lines = [l for l in out.splitlines() if l.startswith("| glm-4.7-local")]
    assert len(data_lines) == 2, out
    assert "[claude-code]" in out and "[pi]" in out


def test_table4_is_unchanged_for_a_single_driver_corpus():
    """The control: a corpus with nothing to disambiguate must not grow a
    driver suffix or split into extra rows."""
    corpus = rows_for("claude-code", 4, 2, task="t3-a")

    out = tables.table4_hybrid_vs_solo(corpus)

    data_lines = [l for l in out.splitlines() if l.startswith("| glm-4.7-local")]
    assert len(data_lines) == 1, out
    assert "[claude-code]" not in out


def test_report_block_splits_a_task_that_ran_under_two_drivers():
    """Positive control for ladder_from_results.py's per-task blocks."""
    import ladder_from_results as ladder  # noqa: E402 (sys.path already set above)

    corpus = rows_for("claude-code", 3, 3) + rows_for("pi", 3, 0)

    blocks = ladder.blocks_for(corpus)

    assert len(blocks) == 2, blocks
    labels = sorted(b["block"] for b in blocks)
    assert labels == ["t1-py-a [claude-code]", "t1-py-a [pi]"], labels


def test_report_block_is_unchanged_for_a_single_driver_corpus():
    import ladder_from_results as ladder  # noqa: E402

    corpus = rows_for("claude-code", 4, 2)

    blocks = ladder.blocks_for(corpus)

    assert len(blocks) == 1, blocks
    assert blocks[0]["block"] == "t1-py-a"


def test_section_wilson_splits_a_model_that_ran_under_two_drivers():
    """Positive control for stats.py's per-cell Wilson table."""
    import stats  # noqa: E402

    corpus = rows_for("claude-code", 3, 3) + rows_for("pi", 3, 0)

    out = stats.section_wilson(corpus)

    data_lines = [l for l in out.splitlines() if l.startswith("| glm-4.7-local")]
    assert len(data_lines) == 2, out
    assert "[claude-code]" in out and "[pi]" in out


def test_section_wilson_is_unchanged_for_a_single_driver_corpus():
    import stats  # noqa: E402

    corpus = rows_for("claude-code", 4, 2)

    out = stats.section_wilson(corpus)

    data_lines = [l for l in out.splitlines() if l.startswith("| glm-4.7-local")]
    assert len(data_lines) == 1, out
    assert "[claude-code]" not in out


# --------------------------------------------------------------------------- #
# The stage-1 config itself (issue #25)
# --------------------------------------------------------------------------- #
def test_stage1_yaml_has_no_pi_sweep_and_totals_sixty():
    """A2 (studio-handoff-20260825): the pi vehicle contrast is stage 1b, not
    stage 1. This is the same count `--dry-run` prints, exercised directly
    against build_runs so the regression is caught without a subprocess."""
    sys.path.insert(0, RUNNER_DIR)
    import run as runner  # noqa: E402

    with open(os.path.join(RUNNER_DIR, "runs-glm-stage1.yaml"), encoding="utf-8") as f:
        cfg = runner.parse_yaml(f.read())
    runs = runner.build_runs(cfg)

    assert len(runs) == 60, len(runs)
    assert sum(1 for r in runs if r.get("driver") == "pi") == 0
    assert "glm-stage1-pi" not in {r["sweep"] for r in runs}

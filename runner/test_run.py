"""Regression tests for runner/run.py's instrument seams.

Seams under test:
  - existing_ids(results_path), the public function main() uses to build the
    resume set. See ticket: a row's presence in results.jsonl must not by
    itself mark its run_id "done" -- only a genuinely complete run should.
  - loc_changed(scratch), ticket 22 defect 1: the field must measure the model's
    work, not the model's git habit.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import existing_ids, parse_usage, prepare_scratch, loc_changed  # noqa: E402


def _write_rows(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_failed_row_is_not_done(tmp_path):
    """A cli_error row must not block its own retry (this bit ticket 13 twice)."""
    results_path = os.path.join(tmp_path, "results.jsonl")
    _write_rows(results_path, [
        {"run_id": "sweep--model--low--bare--t1-a--r1", "exit_reason": "cli_error"},
    ])

    done = existing_ids(results_path)

    assert "sweep--model--low--bare--t1-a--r1" not in done


def test_timeout_row_is_not_done(tmp_path):
    results_path = os.path.join(tmp_path, "results.jsonl")
    _write_rows(results_path, [
        {"run_id": "sweep--model--low--bare--t1-a--r1", "exit_reason": "timeout"},
    ])

    done = existing_ids(results_path)

    assert "sweep--model--low--bare--t1-a--r1" not in done


def test_ok_row_is_done(tmp_path):
    results_path = os.path.join(tmp_path, "results.jsonl")
    _write_rows(results_path, [
        {"run_id": "sweep--model--low--bare--t1-a--r1", "exit_reason": "ok"},
    ])

    done = existing_ids(results_path)

    assert "sweep--model--low--bare--t1-a--r1" in done


def test_failed_then_ok_retry_of_same_run_id_counts_as_done(tmp_path):
    """The realistic resume scenario: first attempt failed, a later retry under
    the same run_id succeeded. The run_id must count as done once any row for it
    is genuinely complete, even though an earlier failed row for it also exists.
    """
    results_path = os.path.join(tmp_path, "results.jsonl")
    _write_rows(results_path, [
        {"run_id": "sweep--model--low--bare--t1-a--r1", "exit_reason": "cli_error"},
        {"run_id": "sweep--model--low--bare--t1-a--r1", "exit_reason": "ok"},
    ])

    done = existing_ids(results_path)

    assert "sweep--model--low--bare--t1-a--r1" in done


# --------------------------------------------------------------------------- #
# parse_usage -- ticket 08: claude/kimi tokens_in must be cache-inclusive.
#
# `claude -p --output-format json`'s final "result" event reports usage.input_tokens
# as only the LAST turn's fresh (uncached) tokens; the tokens actually read from and
# written to cache across the session live in separate cache_read_input_tokens /
# cache_creation_input_tokens fields. A real transcript (t13-haiku, r1) showed
# input_tokens=57 against cache_read_input_tokens=221097 -- summing only the first
# field undercounts real consumption by >99%. probe_endpoints.py already sums all
# three fields; run.py's parse_usage did not, until this fix.
# --------------------------------------------------------------------------- #
def test_parse_usage_claude_family_includes_cache_tokens():
    out = json.dumps({
        "type": "result",
        "num_turns": 11,
        "usage": {
            "input_tokens": 57,
            "cache_creation_input_tokens": 28063,
            "cache_read_input_tokens": 221097,
            "output_tokens": 1751,
        },
    })

    ti, to, turns = parse_usage("claude-fable-5", out)

    assert ti == 57 + 28063 + 221097
    assert to == 1751
    assert turns == 11


def test_parse_usage_kimi_family_includes_cache_tokens():
    """Kimi rides the claude family branch (same CLI, Moonshot endpoint) and is
    real money -- the same undercount silently understated actual spend."""
    out = json.dumps({
        "type": "result",
        "num_turns": 7,
        "usage": {
            "input_tokens": 31022,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 215040,
            "output_tokens": 951,
        },
    })

    ti, to, turns = parse_usage("kimi-k3", out)

    assert ti == 31022 + 215040
    assert to == 951


def test_parse_usage_codex_family_does_not_double_count_cached_tokens():
    """Codex's single turn.completed event already folds cached_input_tokens
    into input_tokens -- summing cached_input_tokens on top would double-count."""
    line = json.dumps({
        "type": "turn.completed",
        "usage": {
            "input_tokens": 351378,
            "cached_input_tokens": 314112,
            "output_tokens": 1988,
            "reasoning_output_tokens": 727,
        },
    })

    ti, to, turns = parse_usage("gpt-5.6-sol", line)

    assert ti == 351378
    assert to == 1988
    assert turns == 1


# --------------------------------------------------------------------------- #
# ticket 22 defect 1: loc_changed must be invariant to the model's git habit
# --------------------------------------------------------------------------- #
def _make_task(tmp_path, name="t9-py-a"):
    """Minimal task_dir with the base/ layout prepare_scratch expects."""
    base = os.path.join(str(tmp_path), name, "base")
    os.makedirs(base)
    with open(os.path.join(base, "lib.py"), "w", encoding="utf-8") as f:
        f.write("def f():\n    return 1\n")
    return os.path.join(str(tmp_path), name)


def _model_edit(scratch):
    """The same edit in both arms: 4 insertions, 1 deletion, no new file."""
    with open(os.path.join(scratch, "lib.py"), "w", encoding="utf-8") as f:
        f.write("def f():\n    return 2\n\n\ndef g():\n    return 3\n")


def _git(scratch, *args):
    return subprocess.run(
        ["git", "-c", "user.email=m@m", "-c", "user.name=model", *args],
        cwd=scratch, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


def test_loc_changed_is_invariant_to_the_model_committing(tmp_path):
    """Ticket 22 defect 1.

    Two runs, byte-identical work. One model leaves it in the working tree, the
    other commits it -- the git habit calib-d2's sol r2/r3 exhibited and r1 did
    not. The recorded number must not be able to tell them apart.
    """
    task_dir = _make_task(tmp_path)

    left_in_tree = os.path.join(str(tmp_path), "scratch-uncommitted")
    prepare_scratch(task_dir, left_in_tree, harness=False)
    _model_edit(left_in_tree)

    committed = os.path.join(str(tmp_path), "scratch-committed")
    prepare_scratch(task_dir, committed, harness=False)
    _model_edit(committed)
    _git(committed, "add", "-A")
    _git(committed, "commit", "-q", "-m", "fix: finish the adapter")

    # Control arm: the fixture must actually reproduce the defect condition,
    # otherwise the assertion below passes for the wrong reason. After the
    # model's own commit the index equals HEAD, which is what made the old
    # index-vs-HEAD diff read 0. That assertion is a test, not a comment.
    _git(committed, "add", "-A")
    assert _git(committed, "diff", "--cached", "--shortstat").stdout.strip() == ""

    assert loc_changed(left_in_tree) > 0, "fixture produced no measurable change"
    assert loc_changed(committed) == loc_changed(left_in_tree)

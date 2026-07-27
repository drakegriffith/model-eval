"""Regression tests for runner/run.py's resume/done-set logic.

Seam under test: existing_ids(results_path), the public function main() uses to
build the resume set. See ticket: a row's presence in results.jsonl must not by
itself mark its run_id "done" -- only a genuinely complete run should.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import existing_ids, parse_usage  # noqa: E402


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

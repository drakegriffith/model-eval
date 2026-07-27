"""Regression tests for runner/run.py's resume/done-set logic.

Seam under test: existing_ids(results_path), the public function main() uses to
build the resume set. See ticket: a row's presence in results.jsonl must not by
itself mark its run_id "done" -- only a genuinely complete run should.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import existing_ids  # noqa: E402


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

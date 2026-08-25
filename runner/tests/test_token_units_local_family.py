"""test_token_units_local_family.py -- issue #15 finding F2: the token-unit
cross-check routed family `local` to the codex parser and then inspected zero
local subjects.

WHAT WAS OPEN. `token_units.series_for_run` read

    if family in ("claude", "kimi"):
        ...                      # Claude Code session log
    idx = codex_idx if codex_idx is not None else _codex_rollout_index()

Family `local` is the same claude binary pointed at an LM Studio server
(registry.py's local family, run.py's local branch), so it writes a
Claude-format session log. Falling into the codex branch meant looking it up in
the codex rollout index, which has no entry for it, so every GLM row came back
`(None, None, [])` and filed as `no_independent_record` -- the check reporting
"no transcript to re-derive from" for rows whose transcript exists and is
readable.

This is the same defect usage_ledger.py carried and had repaired on 2026-08-25
(usage_ledger.py:86-88 records it). The repair landed in one module and not in
its sibling.

Second half: the per-family report walked the literal tuple
`("codex", "claude", "kimi")`, so `local` printed nothing even once routing was
fixed, and a family with zero rows vanished from the table rather than
declaring a zero. A cross-check that inspects zero subjects and says nothing is
not a pass; it is a check that never ran.

No model is invoked anywhere in this file. Every input is a fixture on disk.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import token_units as TU  # noqa: E402


def claude_log(path, requests):
    """A Claude Code session log: one assistant line per API response."""
    with open(path, "w", encoding="utf-8") as f:
        for i, (fresh, cc, cr, out) in enumerate(requests):
            f.write(json.dumps({
                "type": "assistant",
                "message": {"id": f"msg_{i}", "usage": {
                    "input_tokens": fresh, "cache_creation_input_tokens": cc,
                    "cache_read_input_tokens": cr, "output_tokens": out}},
            }) + "\n")


REQUESTS = [(100, 0, 0, 10), (5, 0, 110, 20), (5, 0, 125, 30)]
SESSION_TOTAL = 100 + 115 + 130
TOKENS_OUT = 60


@pytest.fixture
def local_session(tmp_path, monkeypatch):
    """One GLM run with a real Claude-format session log on disk, indexed the
    way _claude_session_index would index it."""
    run_id = "stage1--glm-4.7-local--high--bare--t2-py-a--r1"
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    (transcripts / (run_id + ".txt")).write_text(
        json.dumps({"session_id": "sess-local-1"}), encoding="utf-8")
    monkeypatch.setattr(TU, "TRANSCRIPTS_DIR", str(transcripts))

    project_dir = tmp_path / "projects" / run_id
    project_dir.mkdir(parents=True)
    claude_log(project_dir / "sess-local-1.jsonl", REQUESTS)
    return {"run_id": run_id, "claude_idx": {run_id: str(project_dir)}}


# --------------------------------------------------------------------------- #
# Routing: local reads a Claude-format session log, like every family that
# rides the claude binary.
# --------------------------------------------------------------------------- #
def test_local_family_resolves_its_claude_format_session_log(local_session):
    """The motivating failure: this returned (None, None, []) and every GLM row
    filed as no_independent_record."""
    series, path, alts = TU.series_for_run(
        local_session["run_id"], "local",
        claude_idx=local_session["claude_idx"], codex_idx={})

    assert series, "local resolved no series -- it fell into the codex branch"
    assert len(series) == 3
    assert path and path.endswith("sess-local-1.jsonl")
    assert alts == [], "the summary transcript names the session id; nothing to disambiguate"
    assert TU.figures(series)["session_total"] == SESSION_TOTAL


def test_local_family_cross_check_agrees_with_the_ledger(local_session):
    """End to end for one GLM row: the figure re-derived from the session log
    reproduces the ledger's summary figure, so the row is CHECKED rather than
    unchecked."""
    series, _path, alts = TU.series_for_run(
        local_session["run_id"], "local",
        claude_idx=local_session["claude_idx"], codex_idx={})

    result = TU.crosscheck(local_session["run_id"], SESSION_TOTAL, TOKENS_OUT,
                           series, alts)

    assert result["status"] == "agree"
    assert result["agrees"] is True
    assert result["rederived_tokens_in"] == SESSION_TOTAL


def test_the_claude_log_families_are_exactly_the_families_that_ride_that_binary():
    """Restated here rather than imported as a set (harness #5): claude, kimi
    and local are the three families run.py drives with `claude -p`, and codex
    is the one that is not."""
    assert set(TU.CLAUDE_LOG_FAMILIES) == {"claude", "kimi", "local"}
    assert "codex" not in TU.CLAUDE_LOG_FAMILIES


def test_codex_still_routes_to_the_rollout_index(tmp_path, monkeypatch):
    """The negative control. A fix that sent everything to the claude parser
    would pass every assertion above and break the family this module was
    written for."""
    run_id = "sweep2b--gpt-5.6-sol--high--bare--t3-a--r1"
    rollout = tmp_path / "rollout-codex.jsonl"
    with open(rollout, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "event_msg", "payload": {
            "type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 700,
                                     "cached_input_tokens": 0,
                                     "output_tokens": 12},
                "total_token_usage": {"input_tokens": 700,
                                      "cached_input_tokens": 0,
                                      "output_tokens": 12}}}}) + "\n")
    monkeypatch.setattr(TU, "TRANSCRIPTS_DIR", str(tmp_path / "nope"))

    series, path, _alts = TU.series_for_run(
        run_id, "codex", claude_idx={}, codex_idx={run_id: [str(rollout)]})

    assert series, "codex lost its own routing"
    assert path == str(rollout)


# --------------------------------------------------------------------------- #
# Reporting: the check must say how many local subjects it inspected.
# --------------------------------------------------------------------------- #
def corpus(tmp_path, families):
    """A usage ledger and a matching series snapshot, one measured row per
    named family."""
    usage_path = tmp_path / "usage.jsonl"
    series_path = tmp_path / "context_series.jsonl"
    with open(usage_path, "w", encoding="utf-8") as u, \
            open(series_path, "w", encoding="utf-8") as s:
        for fam in families:
            run_id = f"sweep--{fam}--r1"
            u.write(json.dumps({
                "kind": "worker", "run_id": run_id, "family": fam,
                "model_id": f"{fam}-model", "retrofit_status": "measured",
                "tokens_in": SESSION_TOTAL, "tokens_out": TOKENS_OUT}) + "\n")
            s.write(json.dumps({
                "run_id": run_id, "family": fam, "model_id": f"{fam}-model",
                "source": "sess.jsonl",
                "requests": [list(r) for r in REQUESTS], "alternates": []}) + "\n")
    return str(usage_path), str(series_path)


def test_the_check_names_local_and_counts_the_subjects_it_inspected(tmp_path, capsys):
    usage_path, series_path = corpus(tmp_path, ["claude", "local", "local"])

    TU.cmd_check(usage_path, series_path)
    out = capsys.readouterr().out

    assert "local    2/2" in out, (
        f"the per-family block did not report local's inspected count:\n{out}")


def test_a_family_with_no_subjects_declares_a_zero_rather_than_vanishing(
        tmp_path, capsys):
    """Silence is not evidence. A corpus with no local rows must SAY it
    inspected no local rows -- an empty table row reads identically to a family
    that was never in the loop, which is the exact bug this file records."""
    usage_path, series_path = corpus(tmp_path, ["claude"])

    TU.cmd_check(usage_path, series_path)
    out = capsys.readouterr().out

    assert "local    0/0" in out
    assert "no subjects inspected" in out


def test_the_report_prints_a_local_line(tmp_path, capsys):
    usage_path, series_path = corpus(tmp_path, ["claude", "local"])

    TU.cmd_report(usage_path, series_path)
    out = capsys.readouterr().out

    local_lines = [ln for ln in out.splitlines() if ln.strip().startswith("local")]
    assert local_lines, f"the per-family report omitted local entirely:\n{out}"
    assert "n=1" in local_lines[-1]


def test_the_report_declares_a_zero_for_a_family_with_no_rows(tmp_path, capsys):
    usage_path, series_path = corpus(tmp_path, ["claude"])

    TU.cmd_report(usage_path, series_path)
    out = capsys.readouterr().out

    local_lines = [ln for ln in out.splitlines() if ln.strip().startswith("local")]
    assert local_lines and "n=0" in local_lines[-1]
    assert "no subjects inspected" in local_lines[-1]

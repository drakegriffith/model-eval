"""Tests for runner/token_units.py -- ticket 20 items 1 and 2.

Item 1 asks whether a Claude token and a Codex token are the same unit. The
answer turns on how each CLI's session total relates to its per-request
contexts, so these tests pin the three candidate figures and the two per-request
parsers that produce them.

Item 2 asks for one independent check: re-derive the total from the raw
per-request record instead of the CLI's own summary event, and assert they
agree. Where they diverge the module must REPORT a discrepancy, never silently
pick a winner -- so that behaviour is tested too.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_units as TU  # noqa: E402


# --------------------------------------------------------------------------- #
# figures -- the three candidate units
# --------------------------------------------------------------------------- #
def test_session_total_is_the_sum_of_every_request_context():
    """What both CLIs report and what an API bills: every request pays for its
    whole input context, so the session total is the sum over requests."""
    series = [TU.Req(fresh=100, cache_creation=0, cache_read=0, out=10),
              TU.Req(fresh=5, cache_creation=0, cache_read=110, out=10),
              TU.Req(fresh=5, cache_creation=0, cache_read=125, out=10)]

    f = TU.figures(series)

    assert f["session_total"] == 100 + 115 + 130


def test_peak_context_is_the_last_requests_context_not_the_sum():
    series = [TU.Req(100, 0, 0, 10), TU.Req(5, 0, 110, 10), TU.Req(5, 0, 125, 10)]

    f = TU.figures(series)

    assert f["peak_context_final"] == 130
    assert f["peak_context_max"] == 130


def test_peak_context_max_and_final_differ_when_context_is_not_monotone():
    """Compaction shrinks the context, so the last request is not always the
    largest. Both are reported rather than assuming monotonicity."""
    series = [TU.Req(10, 0, 0, 1), TU.Req(5, 0, 500, 1), TU.Req(5, 0, 20, 1)]

    f = TU.figures(series)

    assert f["peak_context_max"] == 505
    assert f["peak_context_final"] == 25


def test_cache_weighted_discounts_cache_reads_at_the_stated_weight():
    """Cache reads bill at roughly a tenth of fresh input where they bill at
    all. The weight is a stated parameter, not a measurement."""
    series = [TU.Req(fresh=100, cache_creation=50, cache_read=1000, out=10)]

    f = TU.figures(series, cache_read_weight=0.1)

    assert f["cache_weighted"] == 100 + 50 + 100
    assert f["cache_read_weight"] == 0.1


def test_figures_reports_the_request_count():
    f = TU.figures([TU.Req(1, 0, 0, 1), TU.Req(1, 0, 0, 1)])

    assert f["requests"] == 2


# --------------------------------------------------------------------------- #
# claude per-request parser
# --------------------------------------------------------------------------- #
def _claude_session(tmp_path, messages, session_id="s-1"):
    p = os.path.join(tmp_path, session_id + ".jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")
    return p


def test_claude_series_reads_one_entry_per_api_response(tmp_path):
    path = _claude_session(tmp_path, [
        {"type": "user", "message": {"content": "go"}},
        {"type": "assistant", "message": {"id": "m1", "usage": {
            "input_tokens": 20, "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 0, "output_tokens": 5}}},
        {"type": "assistant", "message": {"id": "m2", "usage": {
            "input_tokens": 2, "cache_creation_input_tokens": 30,
            "cache_read_input_tokens": 120, "output_tokens": 7}}},
    ])

    series = TU.claude_series(path)

    assert len(series) == 2
    assert series[1].cache_read == 120
    assert TU.figures(series)["session_total"] == 120 + 152


def test_claude_series_deduplicates_split_content_blocks(tmp_path):
    """One API response can be written out as several assistant lines sharing a
    message id. Counting the line rather than the response triples the bill."""
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": 90, "output_tokens": 3}
    path = _claude_session(tmp_path, [
        {"type": "assistant", "message": {"id": "m1", "usage": usage}},
        {"type": "assistant", "message": {"id": "m1", "usage": usage}},
        {"type": "assistant", "message": {"id": "m1", "usage": usage}},
    ])

    assert len(TU.claude_series(path)) == 1


# --------------------------------------------------------------------------- #
# codex per-request parser
# --------------------------------------------------------------------------- #
def _codex_rollout(tmp_path, cwd, requests, name="rollout.jsonl"):
    p = os.path.join(tmp_path, name)
    lines = [{"type": "session_meta",
              "payload": {"cwd": cwd, "timestamp": "2026-07-27T00:00:00Z"}}]
    tin = tout = tcached = 0
    for fresh, cached, out in requests:
        tin += fresh + cached
        tcached += cached
        tout += out
        lines.append({"type": "event_msg", "payload": {"type": "token_count", "info": {
            "last_token_usage": {"input_tokens": fresh + cached,
                                 "cached_input_tokens": cached, "output_tokens": out},
            "total_token_usage": {"input_tokens": tin, "cached_input_tokens": tcached,
                                  "output_tokens": tout}}}})
    with open(p, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return p


def test_codex_series_reads_one_entry_per_model_request(tmp_path):
    path = _codex_rollout(tmp_path, "/x/run1", [(100, 0, 10), (20, 110, 12)])

    series = TU.codex_series(path)

    assert len(series) == 2
    assert series[1].cache_read == 110
    assert TU.figures(series)["session_total"] == 100 + 130


def test_codex_series_ignores_repeated_token_count_events(tmp_path):
    """The CLI re-emits token_count without a new request in between; the
    running total does not advance, and neither may the series."""
    path = os.path.join(tmp_path, "r.jsonl")
    ev = {"type": "event_msg", "payload": {"type": "token_count", "info": {
        "last_token_usage": {"input_tokens": 50, "cached_input_tokens": 0,
                             "output_tokens": 5},
        "total_token_usage": {"input_tokens": 50, "cached_input_tokens": 0,
                              "output_tokens": 5}}}}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "session_meta",
                            "payload": {"cwd": "/x/run1"}}) + "\n")
        for _ in range(3):
            f.write(json.dumps(ev) + "\n")

    assert len(TU.codex_series(path)) == 1


def test_codex_running_total_equals_the_sum_of_request_contexts(tmp_path):
    """The load-bearing fact for item 1: codex's turn.completed total is a
    cumulative sum over requests, exactly like Claude Code's -- not a single
    session context counted once."""
    path = _codex_rollout(tmp_path, "/x/run1", [(100, 0, 5), (20, 110, 5), (20, 140, 5)])

    series = TU.codex_series(path)
    reported_total = TU.codex_reported_total(path)

    assert TU.figures(series)["session_total"] == reported_total


# --------------------------------------------------------------------------- #
# item 2 -- the independent check
# --------------------------------------------------------------------------- #
def test_crosscheck_agrees_when_rederivation_matches_the_ledger():
    c = TU.crosscheck(run_id="r1", ledger_tokens_in=350, ledger_tokens_out=15,
                      series=[TU.Req(100, 0, 0, 5), TU.Req(5, 0, 145, 5),
                              TU.Req(5, 0, 95, 5)])

    assert c["agrees"] is True
    assert c["delta_in"] == 0


def test_crosscheck_reports_a_discrepancy_instead_of_choosing(tmp_path):
    """'Where they diverge, the dashboard shows a discrepancy, never silently
    picks a winner' -- so both figures survive into the record."""
    c = TU.crosscheck(run_id="r1", ledger_tokens_in=999, ledger_tokens_out=15,
                      series=[TU.Req(100, 0, 0, 5), TU.Req(5, 0, 145, 5),
                              TU.Req(5, 0, 95, 5)])

    assert c["agrees"] is False
    assert c["ledger_tokens_in"] == 999
    assert c["rederived_tokens_in"] == 350
    assert c["delta_in"] == 350 - 999


def test_crosscheck_without_a_series_is_unchecked_not_agreed():
    """No independent record is not the same as a passed check."""
    c = TU.crosscheck(run_id="r1", ledger_tokens_in=10, ledger_tokens_out=1, series=None)

    assert c["agrees"] is None
    assert c["status"] == "no_independent_record"


def test_crosscheck_names_attribution_ambiguity_separately_from_disagreement():
    """A scratch dir can hold several CLI sessions (retries, and the judge runs
    there afterwards), so the pre-committed earliest-session rule sometimes
    reads the wrong one. When a SIBLING session reproduces the ledger exactly,
    the run is corroborated and the session-to-run mapping is what is uncertain.
    That is a different fact from 'no record supports this number' and gets its
    own status rather than being folded into either neighbour."""
    c = TU.crosscheck(run_id="r1", ledger_tokens_in=350, ledger_tokens_out=15,
                      series=[TU.Req(90, 0, 0, 4)],
                      alternates=[[TU.Req(100, 0, 0, 5), TU.Req(5, 0, 145, 5),
                                   TU.Req(5, 0, 95, 5)]])

    assert c["status"] == "agree_after_attribution"
    assert c["agrees"] is True
    assert c["rederived_tokens_in"] == 350


def test_crosscheck_stays_a_discrepancy_when_no_session_reproduces_the_ledger():
    c = TU.crosscheck(run_id="r1", ledger_tokens_in=999, ledger_tokens_out=15,
                      series=[TU.Req(90, 0, 0, 4)],
                      alternates=[[TU.Req(100, 0, 0, 5)]])

    assert c["status"] == "DISCREPANCY"
    assert c["agrees"] is False

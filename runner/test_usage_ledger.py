"""Tests for runner/usage_ledger.py -- ticket 08, the append-only token/dollar
ledger. Raw tokens are truth, dollars are derived and pricing-mutable, joinable
to results.jsonl by run_id. See ticket for the five things this settles.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage_ledger as L  # noqa: E402


# --------------------------------------------------------------------------- #
# parse_usage_detailed
# --------------------------------------------------------------------------- #
def test_parse_usage_detailed_claude_sums_fresh_plus_both_cache_fields():
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

    d = L.parse_usage_detailed("claude", out)

    assert d["tokens_in"] == 57 + 28063 + 221097
    assert d["tokens_out"] == 1751
    assert d["cache_read_tokens"] == 221097
    assert d["cache_creation_tokens"] == 28063
    assert d["turns"] == 11


def test_parse_usage_detailed_codex_does_not_double_count_cache():
    line = json.dumps({
        "type": "turn.completed",
        "usage": {
            "input_tokens": 351378,
            "cached_input_tokens": 314112,
            "output_tokens": 1988,
        },
    })

    d = L.parse_usage_detailed("codex", line)

    assert d["tokens_in"] == 351378
    assert d["cache_read_tokens"] == 314112
    assert d["tokens_out"] == 1988
    assert d["turns"] == 1


def test_parse_usage_detailed_codex_sums_across_multiple_turns():
    lines = "\n".join(json.dumps({
        "type": "turn.completed",
        "usage": {"input_tokens": t, "cached_input_tokens": 0, "output_tokens": 10},
    }) for t in (100, 200))

    d = L.parse_usage_detailed("codex", lines)

    assert d["tokens_in"] == 300
    assert d["tokens_out"] == 20
    assert d["turns"] == 2


# --------------------------------------------------------------------------- #
# usd_estimate -- only models with a verified, dated price get a number.
# --------------------------------------------------------------------------- #
def test_usd_estimate_kimi_prices_fresh_cache_read_and_output_separately():
    # real transcript figures (t13-kimi, high, r1): fresh=31022, cache_read=215040,
    # cache_creation=0, output=951
    usd = L.usd_estimate("kimi-k3", tokens_in=31022 + 215040, tokens_out=951,
                          cache_read_tokens=215040, cache_creation_tokens=0)

    expected = 31022 / 1e6 * 3.0 + 215040 / 1e6 * 0.30 + 951 / 1e6 * 15.0
    assert abs(usd - expected) < 1e-9


def test_usd_estimate_returns_none_for_unpriced_subscription_model():
    """Claude/Codex ids run on Drake's subscription -- no verified per-token
    price exists for them, so we do not fabricate one (ticket 08 decision)."""
    assert L.usd_estimate("claude-fable-5", tokens_in=100000, tokens_out=1000) is None
    assert L.usd_estimate("gpt-5.6-sol", tokens_in=350000, tokens_out=2000) is None


# --------------------------------------------------------------------------- #
# build_usage_row -- schema + retrofit_status honesty
# --------------------------------------------------------------------------- #
def _row(**over):
    base = {
        "run_id": "sweep--claude-fable-5--high--bare--t1-a--r1",
        "ts": "2026-07-27T00:00:00Z",
        "model": "claude-fable-5",
        "model_id": "claude-fable-5",
        "tokens_in": 76,
        "tokens_out": 1200,
        "exit_reason": "ok",
    }
    base.update(over)
    return base


def test_build_usage_row_flags_claude_archived_row_with_no_transcript_as_unfixable():
    """A claude/kimi row with no transcript to re-parse cannot be corrected --
    its stored tokens_in is the known-buggy floor value, and the row must say
    so rather than silently pass it off as a true total."""
    row = build_row = _row()
    urow = L.build_usage_row(row, "claude", usage_detail=None)

    assert urow["retrofit_status"] == "unfixable_floor_only"
    assert urow["tokens_in"] == 76  # unchanged -- we don't invent a number


def test_build_usage_row_marks_codex_archived_row_measured_even_without_transcript():
    """Codex's own accounting was never buggy, so its stored tokens_in is
    already the true cache-inclusive total -- no transcript needed to trust it."""
    row = _row(model="gpt-5.6-sol", model_id="gpt-5.6-sol", tokens_in=351378,
               tokens_out=1988)
    urow = L.build_usage_row(row, "codex", usage_detail=None)

    assert urow["retrofit_status"] == "measured"
    assert urow["tokens_in"] == 351378


def test_build_usage_row_uses_fresh_parse_when_transcript_available():
    row = _row(tokens_in=57, tokens_out=1751)  # the old, buggy stored value
    detail = L.parse_usage_detailed("claude", json.dumps({
        "type": "result", "num_turns": 11,
        "usage": {"input_tokens": 57, "cache_creation_input_tokens": 28063,
                  "cache_read_input_tokens": 221097, "output_tokens": 1751},
    }))

    urow = L.build_usage_row(row, "claude", usage_detail=detail)

    assert urow["retrofit_status"] == "measured"
    assert urow["tokens_in"] == 57 + 28063 + 221097
    assert urow["cache_read_tokens"] == 221097


def test_build_usage_row_marks_zero_token_mock_row_not_applicable():
    row = _row(tokens_in=0, tokens_out=0, exit_reason="mock")
    urow = L.build_usage_row(row, "claude", usage_detail=None)

    assert urow["retrofit_status"] == "not_applicable_zero_tokens"


def test_build_usage_row_records_scaffold_overhead_separately_from_task_work():
    row = _row(model="kimi-k3", model_id="kimi-k3", tokens_in=250000, tokens_out=900)
    urow = L.build_usage_row(row, "kimi", usage_detail=None)

    assert urow["scaffold_overhead_tokens"] == L.SCAFFOLD_FLOOR_TOKENS["kimi-k3"]
    assert urow["scaffold_overhead_tokens"] < urow["tokens_in"]


def test_build_usage_row_labels_billing_mode_honestly():
    kimi_row = L.build_usage_row(_row(model="kimi-k3", model_id="kimi-k3"),
                                  "kimi", usage_detail=None)
    claude_row = L.build_usage_row(_row(), "claude", usage_detail=None)

    assert kimi_row["billing_mode"] == "metered"
    assert claude_row["billing_mode"] == "subscription"
    assert claude_row["usd_estimate"] is None


# --------------------------------------------------------------------------- #
# append_usage_row -- append-only, never rewrites
# --------------------------------------------------------------------------- #
def test_append_usage_row_is_append_only(tmp_path):
    path = os.path.join(tmp_path, "usage.jsonl")
    L.append_usage_row(path, {"run_id": "a", "tokens_in": 1})
    L.append_usage_row(path, {"run_id": "b", "tokens_in": 2})

    with open(path) as f:
        rows = [json.loads(line) for line in f if line.strip()]

    assert [r["run_id"] for r in rows] == ["a", "b"]


def test_build_usage_row_resolves_alias_to_canonical_model_id_when_given():
    """Pre-registry archived rows (before model_id existed) stored only the
    alias in `model` (e.g. "sol" for gpt-5.6-sol). The ledger must key
    scaffold/pricing lookups off the CANONICAL id, not the alias, or 88 of the
    real archive's codex rows silently fall out of every canonical-id lookup."""
    row = {"run_id": "sweep1--sol--high--bare--t1-py-a--r1", "model": "sol",
           "tokens_in": 148816, "tokens_out": 1277, "exit_reason": "ok"}

    urow = L.build_usage_row(row, "codex", usage_detail=None, model_id="gpt-5.6-sol")

    assert urow["model_id"] == "gpt-5.6-sol"
    assert urow["scaffold_overhead_tokens"] == L.SCAFFOLD_FLOOR_TOKENS["gpt-5.6-sol"]


# --------------------------------------------------------------------------- #
# retrofit -- offline pass over an existing results.jsonl + transcripts/
# --------------------------------------------------------------------------- #
def test_retrofit_resolves_alias_only_rows_to_canonical_model_id(tmp_path):
    """retrofit() must resolve `run.resolve_model` itself for archived rows
    that predate the model_id field, not fall back to the raw alias."""
    results_path = os.path.join(tmp_path, "results.jsonl")
    transcripts_dir = os.path.join(tmp_path, "transcripts")
    usage_path = os.path.join(tmp_path, "usage.jsonl")
    os.makedirs(transcripts_dir)

    with open(results_path, "w") as f:
        f.write(json.dumps({
            "run_id": "sweep1--sol--high--bare--t1-py-a--r1", "model": "sol",
            "tokens_in": 148816, "tokens_out": 1277, "exit_reason": "ok",
        }) + "\n")

    L.retrofit(results_path, transcripts_dir, usage_path)

    with open(usage_path) as f:
        row = json.loads(f.readline())
    assert row["model_id"] == "gpt-5.6-sol"
    assert row["scaffold_overhead_tokens"] == L.SCAFFOLD_FLOOR_TOKENS["gpt-5.6-sol"]


def test_retrofit_writes_one_row_per_result_and_is_idempotent(tmp_path):
    results_path = os.path.join(tmp_path, "results.jsonl")
    transcripts_dir = os.path.join(tmp_path, "transcripts")
    usage_path = os.path.join(tmp_path, "usage.jsonl")
    os.makedirs(transcripts_dir)

    with_transcript = _row(run_id="withT")
    without_transcript = _row(run_id="withoutT")
    with open(results_path, "w") as f:
        f.write(json.dumps(with_transcript) + "\n")
        f.write(json.dumps(without_transcript) + "\n")

    with open(os.path.join(transcripts_dir, "withT.txt"), "w") as f:
        f.write(json.dumps({
            "type": "result", "num_turns": 1,
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5,
                      "cache_read_input_tokens": 3, "output_tokens": 2},
        }))

    summary = L.retrofit(results_path, transcripts_dir, usage_path)
    assert summary["written"] == 2

    with open(usage_path) as f:
        rows = {json.loads(line)["run_id"]: json.loads(line) for line in f if line.strip()}
    assert rows["withT"]["retrofit_status"] == "measured"
    assert rows["withT"]["tokens_in"] == 18
    assert rows["withoutT"]["retrofit_status"] == "unfixable_floor_only"

    # idempotent: re-running does not duplicate existing run_ids
    summary2 = L.retrofit(results_path, transcripts_dir, usage_path)
    assert summary2["written"] == 0
    assert summary2["skipped_existing"] == 2

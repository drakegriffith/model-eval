"""Tests for judge-panel metering -- ticket 20 item 3.

run.py's execute_run() appends a usage.jsonl row per worker run; judge.py
appended nothing, so the judge panel was unledgered and ticket 01's retired
70/20/10 split had to guess at it. These tests pin the contract: one usage.jsonl
row per judge CALL (not per judged run -- the panel has multiple heads), same
schema as a worker row, joinable the same way.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge as J  # noqa: E402
import usage_ledger as L  # noqa: E402


CLAUDE_OUT = json.dumps({
    "type": "result",
    "num_turns": 1,
    "session_id": "s-1",
    "usage": {"input_tokens": 40, "cache_creation_input_tokens": 1200,
              "cache_read_input_tokens": 9000, "output_tokens": 310},
    "modelUsage": {"claude-opus-4-8": {"inputTokens": 40, "outputTokens": 310}},
    "result": json.dumps({"correctness": {"score": 8, "why": "x"},
                          "simplicity": {"score": 7, "why": "x"},
                          "idiomatic": {"score": 7, "why": "x"},
                          "spec": {"score": 9, "why": "x"}}),
})

CODEX_OUT = "\n".join([
    json.dumps({"type": "turn.completed",
                "usage": {"input_tokens": 22000, "cached_input_tokens": 18000,
                          "output_tokens": 260}}),
    json.dumps({"type": "item.completed",
                "item": {"type": "agent_message",
                         "text": json.dumps({"correctness": {"score": 6, "why": "x"},
                                             "simplicity": {"score": 6, "why": "x"},
                                             "idiomatic": {"score": 6, "why": "x"},
                                             "spec": {"score": 6, "why": "x"}})}}),
])


# --------------------------------------------------------------------------- #
# build_usage_row -- the schema has to carry judge rows without forking
# --------------------------------------------------------------------------- #
def test_worker_rows_are_kind_worker_by_default():
    """Existing callers (run.py, retrofit) must keep emitting worker rows
    without passing anything new."""
    row = {"run_id": "sweep--sol--low--bare--t1-a--r1", "ts": "2026-07-27T00:00:00Z",
           "model": "sol", "tokens_in": 100, "tokens_out": 10}

    urow = L.build_usage_row(row, "codex", model_id="gpt-5.6-sol")

    assert urow["kind"] == "worker"
    assert urow["judged_run_id"] is None


def test_judge_row_carries_kind_and_the_run_it_judged():
    row = {"run_id": "judge-claude--sweep--sol--low--bare--t1-a--r1",
           "ts": "2026-07-27T00:00:00Z", "model": "claude-opus-4-8",
           "tokens_in": 0, "tokens_out": 0}
    detail = L.parse_usage_detailed("claude", CLAUDE_OUT)

    urow = L.build_usage_row(row, "claude", usage_detail=detail,
                             model_id="claude-opus-4-8", kind="judge",
                             judged_run_id="sweep--sol--low--bare--t1-a--r1")

    assert urow["kind"] == "judge"
    assert urow["judged_run_id"] == "sweep--sol--low--bare--t1-a--r1"
    assert urow["tokens_in"] == 40 + 1200 + 9000
    assert urow["retrofit_status"] == "measured"


def test_judge_row_has_exactly_the_worker_schema():
    """'Same schema, joinable the same way' -- a consumer must not need to know
    which kind it is holding to read a field."""
    worker = L.build_usage_row(
        {"run_id": "a", "ts": "t", "model": "sol", "tokens_in": 1, "tokens_out": 1},
        "codex", model_id="gpt-5.6-sol")
    judged = L.build_usage_row(
        {"run_id": "judge-codex--a", "ts": "t", "model": None,
         "tokens_in": 0, "tokens_out": 0},
        "codex", usage_detail=L.parse_usage_detailed("codex", CODEX_OUT),
        kind="judge", judged_run_id="a")

    assert set(worker.keys()) == set(judged.keys())


# --------------------------------------------------------------------------- #
# judge.py -- one ledger row per judge CALL
# --------------------------------------------------------------------------- #
def _judge_with_stubs(tmp_path, monkeypatch, claude_out=CLAUDE_OUT, codex_out=CODEX_OUT):
    scratch = os.path.join(tmp_path, ".scratch", "sweep--sol--low--bare--t1-a--r1")
    os.makedirs(scratch)
    monkeypatch.setattr(J, "get_diff", lambda s: "diff --git a/x b/x\n")
    monkeypatch.setattr(J, "read_prompt_for_run", lambda r, t: "do the thing")
    monkeypatch.setattr(J, "run_claude_judge", lambda p: claude_out)
    monkeypatch.setattr(J, "run_codex_judge", lambda p: codex_out)
    out = os.path.join(tmp_path, "results", "judgments.jsonl")
    usage = os.path.join(tmp_path, "results", "usage.jsonl")
    ok = J.judge_one("sweep--sol--low--bare--t1-a--r1",
                     os.path.join(tmp_path, ".scratch"),
                     os.path.join(tmp_path, "tasks"), out, mock=False,
                     usage_path=usage)
    rows = []
    if os.path.exists(usage):
        rows = [json.loads(x) for x in open(usage) if x.strip()]
    return ok, rows


def test_judge_one_appends_one_usage_row_per_head(tmp_path, monkeypatch):
    ok, rows = _judge_with_stubs(tmp_path, monkeypatch)

    assert ok
    assert len(rows) == 2
    assert {r["family"] for r in rows} == {"claude", "codex"}
    assert all(r["kind"] == "judge" for r in rows)


def test_judge_usage_rows_join_back_to_the_worker_run(tmp_path, monkeypatch):
    _, rows = _judge_with_stubs(tmp_path, monkeypatch)

    assert {r["judged_run_id"] for r in rows} == {"sweep--sol--low--bare--t1-a--r1"}
    # run_id stays unique so the ledger never collides with the worker row
    assert len({r["run_id"] for r in rows}) == 2
    assert "sweep--sol--low--bare--t1-a--r1" not in {r["run_id"] for r in rows}


def test_judge_usage_rows_carry_the_measured_tokens(tmp_path, monkeypatch):
    _, rows = _judge_with_stubs(tmp_path, monkeypatch)
    by = {r["family"]: r for r in rows}

    assert by["claude"]["tokens_in"] == 40 + 1200 + 9000
    assert by["claude"]["tokens_out"] == 310
    assert by["claude"]["model_id"] == "claude-opus-4-8"
    assert by["codex"]["tokens_in"] == 22000
    assert by["codex"]["cache_read_tokens"] == 18000


def test_unparseable_cli_output_ledgers_nothing_rather_than_zero(tmp_path, monkeypatch):
    """A judge call whose usage cannot be read is not a free call. Writing a
    0-token row would be a fabricated measurement -- ticket 20's standing rule."""
    _, rows = _judge_with_stubs(tmp_path, monkeypatch, claude_out="not json at all")
    fams = {r["family"] for r in rows}

    assert fams == {"codex"}


def test_mock_mode_ledgers_nothing(tmp_path, monkeypatch):
    """GAUNTLET_MOCK spends no tokens, so it must not manufacture ledger rows."""
    scratch = os.path.join(tmp_path, ".scratch", "sweep--sol--low--bare--t1-a--r1")
    os.makedirs(scratch)
    monkeypatch.setattr(J, "get_diff", lambda s: "")
    monkeypatch.setattr(J, "read_prompt_for_run", lambda r, t: "p")
    usage = os.path.join(tmp_path, "results", "usage.jsonl")
    J.judge_one("sweep--sol--low--bare--t1-a--r1", os.path.join(tmp_path, ".scratch"),
                os.path.join(tmp_path, "tasks"),
                os.path.join(tmp_path, "results", "judgments.jsonl"),
                mock=True, usage_path=usage)

    assert not os.path.exists(usage)

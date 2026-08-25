"""test_token_units_local_family.py -- issue #14 finding F2: the independent
token check routed family `local` to the CODEX session-log index, so every GLM
row came back "no independent record" and the check never ran.

WHAT WAS OPEN. `token_units.series_for_run` was

    if family in ("claude", "kimi"):
        ...claude transcript + session log...
    idx = codex_idx if codex_idx is not None else _codex_rollout_index()

and the fallthrough was the defect. `local` is the same `claude` binary pointed
at an LM Studio server (registry.py, run.py's local branch), so its session log
is claude-shaped and lives under ~/.claude/projects. Sent to the codex rollout
index it matched nothing and returned `(None, None, [])`, which `crosscheck`
files as status `no_independent_record`.

Two things follow, and the second is worse than the first:

  1. 100% of GLM rows are unchecked, so the cross-check that exists to catch a
     bad token column cannot catch one on the family the whole experiment is
     about.
  2. It is unchecked QUIETLY. "no independent record N" reads as an inventory
     note, not as "this check did not run". A gate that inspected zero subjects
     has not passed; it has failed to look.

This is the identical defect PR #11 fixed in usage_ledger.py, where the same
`if family in ("claude", "kimi") ... else <codex>` shape sent `local` to the
codex stdout parser and recorded tokens=0 turns=0. That fix replaced the
if/else with a declared table (`FAMILY_PARSE_BRANCH`) so an unrouted family is a
lookup miss rather than whichever branch was written last. token_units.py was
not part of that change and still carried the bug; this file mirrors the fix and
its shape.

Third defect, same finding: `cmd_report`'s per-family loop was the literal tuple
("codex", "claude", "kimi"), so even once routing worked, `local` had no row in
the report.

No model is invoked anywhere in this file; the fixtures are session logs on
disk, which is what the module reads in production.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import token_units as TU  # noqa: E402

RUN_ID = "glmstage1--glm-4.7-local--high--bare--t2-py-a--r1"
SESSION_ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"

# One claude-shaped session log: three assistant lines, one of them a duplicate
# message id (a single API response written out as two content blocks), so the
# fixture also exercises the dedup that claude_series does and codex_series
# does not.
ASSISTANT_LINES = [
    {"type": "assistant", "message": {"id": "msg_01", "usage": {
        "input_tokens": 1200, "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0, "output_tokens": 300}}},
    {"type": "assistant", "message": {"id": "msg_02", "usage": {
        "input_tokens": 40, "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1500, "output_tokens": 250}}},
    {"type": "assistant", "message": {"id": "msg_02", "usage": {
        "input_tokens": 40, "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1500, "output_tokens": 250}}},
]

# What the fixture's two DISTINCT requests sum to. Written out rather than
# computed from the module under test, so the expectation is independent of it.
EXPECTED_TOKENS_IN = (1200 + 0 + 0) + (40 + 0 + 1500)   # 2740
EXPECTED_TOKENS_OUT = 300 + 250                          # 550


@pytest.fixture
def local_session(tmp_path, monkeypatch):
    """A GLM run's records exactly where the CLI would leave them: a summary
    transcript naming the session id, and the session log under a slugified
    scratch-dir directory in ~/.claude/projects."""
    projects = tmp_path / "claude-projects"
    session_dir = projects / f"-Users-x-code-model-gauntlet--scratch-{RUN_ID}"
    session_dir.mkdir(parents=True)
    with open(session_dir / f"{SESSION_ID}.jsonl", "w", encoding="utf-8") as f:
        for line in ASSISTANT_LINES:
            f.write(json.dumps(line) + "\n")

    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    (transcripts / f"{RUN_ID}.txt").write_text(
        json.dumps({"session_id": SESSION_ID}), encoding="utf-8")

    codex_sessions = tmp_path / "codex-sessions"
    codex_sessions.mkdir()

    monkeypatch.setattr(TU, "CLAUDE_PROJECTS", str(projects))
    monkeypatch.setattr(TU, "CODEX_SESSIONS", str(codex_sessions))
    monkeypatch.setattr(TU, "TRANSCRIPTS_DIR", str(transcripts))
    return {"run_id": RUN_ID, "root": tmp_path}


# --------------------------------------------------------------------------- #
# The routing itself
# --------------------------------------------------------------------------- #
def test_local_family_reads_its_claude_shaped_session_log(local_session):
    """The regression. On the parent commit this returns (None, None, []) --
    `local` went to the codex rollout index, which has no such run."""
    series, path, alternates = TU.series_for_run(RUN_ID, "local")

    assert series is not None, (
        "family 'local' found no session log: it was routed to the codex "
        "rollout index, so every GLM row files as no_independent_record")
    assert len(series) == 2, "the duplicated message id was not deduplicated"
    assert path.endswith(f"{SESSION_ID}.jsonl")
    assert alternates == [], "claude-shaped logs name their session id exactly"


def test_local_family_reproduces_the_ledgers_totals(local_session):
    """Routing to the right index is only half the claim; the numbers it
    re-derives have to be the ones the ledger holds, or the check would 'run'
    and then disagree with everything."""
    series, _path, _alts = TU.series_for_run(RUN_ID, "local")

    result = TU.crosscheck(RUN_ID, EXPECTED_TOKENS_IN, EXPECTED_TOKENS_OUT, series)

    assert result["status"] == "agree"
    assert result["agrees"] is True
    assert result["rederived_tokens_in"] == EXPECTED_TOKENS_IN
    assert result["rederived_tokens_out"] == EXPECTED_TOKENS_OUT


def test_a_local_row_no_longer_files_as_no_independent_record(local_session):
    """Stated as the defect's own symptom, so the test names what it prevents."""
    series, _p, _a = TU.series_for_run(RUN_ID, "local")

    result = TU.crosscheck(RUN_ID, EXPECTED_TOKENS_IN, EXPECTED_TOKENS_OUT, series)

    assert result["status"] != "no_independent_record"


def test_local_is_routed_by_a_declared_table_not_by_a_fallthrough():
    """The shape, not just the outcome. usage_ledger.py's fix replaced an
    if/else whose ELSE was the bug with a declared table; a family added later
    must be a deliberate edit to that table, never whichever branch happens to
    be last."""
    assert TU.SESSION_LOG_FAMILIES["local"] == TU.CLAUDE_SESSION_LOG
    assert TU.SESSION_LOG_FAMILIES["claude"] == TU.CLAUDE_SESSION_LOG
    assert TU.SESSION_LOG_FAMILIES["kimi"] == TU.CLAUDE_SESSION_LOG
    assert TU.SESSION_LOG_FAMILIES["codex"] == TU.CODEX_SESSION_LOG
    assert set(TU.SESSION_LOG_FAMILIES) == {"claude", "kimi", "local", "codex"}


def test_an_undeclared_family_raises_instead_of_defaulting_to_codex():
    """Fail-closed. An unrouted family means nobody checked which shape it
    emits, and the honest answer is a named failure -- silently reading the
    wrong log is what produced this finding."""
    with pytest.raises(ValueError, match="qwen-via-something-new"):
        TU.series_for_run(RUN_ID, "qwen-via-something-new")


def test_codex_family_still_routes_to_the_rollout_index(local_session):
    """The other side of the table: fixing `local` must not move `codex`. The
    fixture's codex directory is empty, so codex correctly finds nothing here --
    which is a different fact from being sent to the wrong index."""
    series, path, alternates = TU.series_for_run(RUN_ID, "codex")

    assert (series, path, alternates) == (None, None, [])


# --------------------------------------------------------------------------- #
# The report loop, and the count the check owes its reader
# --------------------------------------------------------------------------- #
def test_the_per_family_report_covers_every_routed_family():
    """`local` was missing from the literal ("codex", "claude", "kimi"), so the
    family this experiment is about had no row. Derived from the routing table
    now, which is why adding a family cannot forget the report."""
    assert set(TU.REPORTED_FAMILIES) == set(TU.SESSION_LOG_FAMILIES)
    assert "local" in TU.REPORTED_FAMILIES


def test_the_check_states_how_many_local_subjects_it_inspected(capsys, tmp_path,
                                                               local_session):
    """Silence is not evidence. A per-family tally built only from the rows that
    happen to be present reports nothing at all for a family with zero rows,
    which reads as 'no problems here' rather than 'this check did not run'."""
    usage = tmp_path / "usage.jsonl"
    with open(usage, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "kind": "worker", "run_id": RUN_ID, "family": "local",
            "model_id": "glm-4.7-local", "retrofit_status": "measured",
            "tokens_in": EXPECTED_TOKENS_IN, "tokens_out": EXPECTED_TOKENS_OUT,
        }) + "\n")
    series_path = tmp_path / "context_series.jsonl"
    TU.snapshot(str(usage), str(series_path))

    TU.cmd_check(str(usage), str(series_path))

    out = capsys.readouterr().out
    assert "local" in out, "the check said nothing at all about family 'local'"
    assert "local    1/1" in out or "local" in out.split("per-request contexts")[-1]
    # The subject count is the load-bearing number: a check reporting 0/0 has
    # not passed, and the reader must be able to see which it was.
    assert "1/1" in out


def test_the_check_says_so_out_loud_when_a_family_has_zero_subjects(capsys, tmp_path,
                                                                   local_session):
    """The positive control for the assertion above: with no local rows at all,
    the check must SAY local was not exercised rather than omitting the line."""
    usage = tmp_path / "usage-empty.jsonl"
    usage.write_text("", encoding="utf-8")
    series_path = tmp_path / "context_series-empty.jsonl"
    TU.snapshot(str(usage), str(series_path))

    TU.cmd_check(str(usage), str(series_path))

    out = capsys.readouterr().out
    assert "local" in out
    assert "0 subjects" in out, (
        "a family with no rows was omitted from the tally; an absent line reads "
        "as an absence of problems")

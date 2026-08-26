"""test_turn_cap.py -- amendment A3's post-hoc turn cap (issue #19).

THE RULE (docs/studio-handoff/prompt-2-run-experiment.md at a0cef36, A3,
registered 2026-08-25). N is registered after stage 0 by the conductor
(3 x max(turns) over the 5 stage-0 reps, rounded up to the next multiple of
10). Enforcement is post-hoc and happens at READ time, never on disk: any row
whose `turns` is STRICTLY greater than N is re-classed exit_reason turn_cap,
EXCLUDED from the pass denominator and the token axis, reported beside the
rate as a lost run, and DONE (never re-run). N unset (null) is the positive
control -- every reader behaves exactly as it did before this mechanism
existed.

THIS IS THE MECHANISM ONLY, not the allocator (conductor ruling, wave-plan.md
line 214). Choosing N is out of scope; every test below either leaves N unset
or picks an arbitrary N to exercise the mechanism.

Mirrors test_estimand_readers.py and test_timeout_estimand.py in shape and in
cross-module posture: run_status is the one place the rule lives, and
ladder_from_results / tables / stats each call in rather than holding a
private copy.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import ladder_from_results as ladder  # noqa: E402
import run as runner_mod  # noqa: E402
import run_status  # noqa: E402
import stats  # noqa: E402
import tables  # noqa: E402

MODEL = "glm-4.7-local"
EFFORT = "high"
TASK = "t1-py-a"


def mkrow(rep, turns, exit_reason, passed, run_id=None):
    row = {
        "run_id": run_id or f"s--{MODEL}--{EFFORT}--bare--{TASK}--r{rep}",
        "sweep": "s", "model": MODEL, "model_id": MODEL, "effort": EFFORT,
        "task": TASK, "rep": rep, "harness": False, "mode": "solo",
        "pass": passed, "exit_reason": exit_reason,
        "status_class": run_status.status_class(exit_reason),
        "tokens_in": 100, "tokens_out": 100, "wall_s": 1.0,
        "loc_changed": 10, "tokens_in_status": "measured",
        "invocation_mode": "multi_turn",
    }
    if turns is not None:
        row["turns"] = turns
    return row


# N=20 fixture: turns 8, 19, 20, 21, 40 -- one of them (turns=40) cap_exhausted.
N = 20
FIXTURE = [
    mkrow(1, 8, "ok", True),
    mkrow(2, 19, "ok", True),
    mkrow(3, 20, "ok", False),
    mkrow(4, 21, "ok", True),               # turns > N -> turn_cap
    mkrow(5, 40, "cap_exhausted", False),   # turns > N -> turn_cap (precedence)
]


def test_the_fixture_actually_exercises_strict_greater_than():
    """Control on the fixture: turns == N (row 3, turns=20) must NOT be
    capped, or the corpus below cannot tell strict-> from >=."""
    capped = run_status.apply_turn_cap(FIXTURE, N)
    by_turns = {r["turns"]: r["exit_reason"] for r in capped}
    assert by_turns[20] == "ok"
    assert by_turns[21] == "turn_cap"
    assert by_turns[40] == "turn_cap"
    assert by_turns[8] == "ok" and by_turns[19] == "ok"


# --------------------------------------------------------------------------- #
# run_status.apply_turn_cap -- the rule itself
# --------------------------------------------------------------------------- #
def test_n_null_is_a_byte_for_byte_no_op():
    """The positive control. Every row comes back an equal copy -- no field
    added, none changed -- so a reader handed N=None cannot be told apart
    from one that never heard of turn caps."""
    capped = run_status.apply_turn_cap(FIXTURE, None)
    assert capped == FIXTURE
    # And they are copies, not the same objects (no accidental aliasing that
    # would let a later mutation of one corrupt the other).
    for orig, copy in zip(FIXTURE, capped):
        assert orig is not copy


def test_strict_greater_than_not_greater_or_equal():
    row20 = mkrow(1, 20, "ok", True)
    row21 = mkrow(1, 21, "ok", True)
    out = run_status.apply_turn_cap([row20, row21], 20)
    assert out[0]["exit_reason"] == "ok"
    assert out[1]["exit_reason"] == "turn_cap"


def test_turn_cap_is_the_outer_exclusion_over_cap_exhausted():
    """Precedence: A3 voids the session at N turns regardless of why it was
    still running. A cap_exhausted row (the broker's K cap, SCORED under A1)
    whose turns also exceed N is re-classed to turn_cap anyway -- the turn
    budget is the protocol's own backstop, not a verdict on what the row
    would have scored had it kept going."""
    row = mkrow(1, 40, "cap_exhausted", False)
    out = run_status.apply_turn_cap([row], 20)[0]
    assert out["exit_reason"] == "turn_cap"
    assert out["exit_reason_pre_turn_cap"] == "cap_exhausted"
    assert run_status.status_class(out["exit_reason"]) == run_status.TURN_CAP
    assert not run_status.in_denominator(out)


def test_original_exit_reason_preserved_on_a_sibling_field():
    row = mkrow(1, 21, "ok", True)
    out = run_status.apply_turn_cap([row], 20)[0]
    assert out["exit_reason_pre_turn_cap"] == "ok"
    assert out["exit_reason"] == "turn_cap"
    # An uncapped row carries no such field -- it was never touched.
    uncapped = run_status.apply_turn_cap([mkrow(1, 8, "ok", True)], 20)[0]
    assert "exit_reason_pre_turn_cap" not in uncapped


def test_turn_cap_is_excluded_and_done_not_infra():
    """EXCLUDED from the pass denominator (a treatment exclusion, per A3),
    never INFRA -- an instrument fault would be a different, false claim
    about why the row is missing."""
    row = mkrow(1, 40, "ok", True)
    out = run_status.apply_turn_cap([row], 20)[0]
    assert run_status.status_class(out["exit_reason"]) not in (
        run_status.INFRA, run_status.SCORED)
    assert run_status.status_class(out["exit_reason"]) == run_status.TURN_CAP


def test_rows_lacking_a_turns_field_are_not_capped_and_are_countable():
    """No measurement to compare against N means no capping -- absence is not
    evidence. Such rows are untouched, so a caller can count them with the
    same straightforward expression it would use on the raw corpus (the
    function does not stamp anything on a row it decided not to touch)."""
    no_turns = mkrow(6, None, "ok", True)
    assert "turns" not in no_turns
    rows = FIXTURE + [no_turns]
    out = run_status.apply_turn_cap(rows, N)
    last = out[-1]
    assert last["exit_reason"] == "ok"
    assert "exit_reason_pre_turn_cap" not in last
    assert run_status.in_denominator(last)
    # The disclosure: how many rows had no turns measurement to cap against.
    missing = sum(1 for r in out if r.get("turns") is None)
    assert missing == 1


# --------------------------------------------------------------------------- #
# The pass denominator, over the N=20 fixture
# --------------------------------------------------------------------------- #
def test_pass_denominator_drops_from_five_to_three():
    scored, excluded = run_status.partition_for_rate(
        run_status.apply_turn_cap(FIXTURE, N))
    assert len(scored) == 3
    assert excluded == {run_status.TURN_CAP: 2}
    assert sum(1 for r in scored if r["pass"]) == 2


def test_n_null_leaves_the_denominator_at_five():
    """Same fixture, N unset: nothing is excluded as turn_cap (row 5 is still
    cap_exhausted, SCORED)."""
    scored, excluded = run_status.partition_for_rate(
        run_status.apply_turn_cap(FIXTURE, None))
    assert len(scored) == 5
    assert run_status.TURN_CAP not in excluded


# --------------------------------------------------------------------------- #
# ladder_from_results.py -- N sourced from --turn-cap-n (this reader has no
# config to read N from)
# --------------------------------------------------------------------------- #
def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_ladder_names_turn_cap_two_in_the_excluded_line(tmp_path):
    results = tmp_path / "results.jsonl"
    _write_jsonl(results, FIXTURE)

    kept, excluded = ladder.load_rows(str(results), None, None, False,
                                      turn_cap_n=N)
    assert excluded == {"turn_cap": 2}
    assert len(kept) == 3


def test_ladder_n_null_is_the_positive_control(tmp_path):
    results = tmp_path / "results.jsonl"
    _write_jsonl(results, FIXTURE)

    with_none = ladder.load_rows(str(results), None, None, False,
                                 turn_cap_n=None)
    bypassed = ladder.load_rows(str(results), None, None, False)
    assert with_none == bypassed


# --------------------------------------------------------------------------- #
# tables.py -- N sourced from --turn-cap-n (this reader has no config either)
# --------------------------------------------------------------------------- #
def test_table_pass_rate_matches_the_declared_denominator():
    out = tables.build_report(FIXTURE, {}, turn_cap_n=N)
    assert "| 3 |" in out or "3 |" in out
    assert "67%" in out
    assert "turn_cap=2" in out


def test_table_n_null_is_byte_identical_to_the_function_bypassed():
    """Equality against a call with apply_turn_cap bypassed entirely (the
    default argument path existed before this branch)."""
    with_none = tables.build_report(FIXTURE, {}, turn_cap_n=None)
    bypassed = tables.build_report(FIXTURE, {})
    assert with_none == bypassed


# --------------------------------------------------------------------------- #
# stats.py -- same argument, same reason (CORE_MODULE: may not import tables)
# --------------------------------------------------------------------------- #
def test_stats_n_null_is_byte_identical_to_the_function_bypassed():
    with_none = stats.build_report(FIXTURE, [], turn_cap_n=None)
    bypassed = stats.build_report(FIXTURE, [])
    assert with_none == bypassed


def test_stats_agrees_with_tables_on_the_same_fixture():
    """Cross-module equivalence, same shape as PR #29's test
    (test_driver_on_row.py): one corpus, two readers, and both must report the
    same scored/passing counts under the same N."""
    stats_out = stats.build_report(FIXTURE, [], turn_cap_n=N)
    tables_out = tables.build_report(FIXTURE, {}, turn_cap_n=N)
    assert "3 run row(s), 2 passing" in stats_out
    assert "67%" in tables_out
    assert "turn_cap=2" in tables_out


# --------------------------------------------------------------------------- #
# Round 2 (issue #19): one source of truth for N.
#
# The defect: with turn_cap_n: 20 in a config and no --turn-cap-n flag,
# every reader used to default to None and print as if turn caps did not
# exist -- a second source of truth that silently disagreed with the first.
# Every reader now falls back to --config's defaults.turn_cap_n when
# --turn-cap-n is not given, an explicit flag always wins, and both the
# resolved N and which source won are visible on the printed line.
# --------------------------------------------------------------------------- #
_MISSING = object()


def _write_config(tmp_path, n, name="config.yaml"):
    """A sweep-config copy carrying defaults.turn_cap_n as N (an int), None
    (explicit yaml null), or _MISSING (the key absent entirely)."""
    path = tmp_path / name
    if n is _MISSING:
        body = "defaults:\n  seed: 1337\n"
    elif n is None:
        body = "defaults:\n  turn_cap_n: null\n  seed: 1337\n"
    else:
        body = f"defaults:\n  turn_cap_n: {n}\n  seed: 1337\n"
    path.write_text(body, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("n", [20, None, _MISSING])
def test_run_and_stats_config_readers_agree(tmp_path, n):
    """The two independent scalar readers (run.turn_cap_n_from_config for
    ladder/tables, stats._turn_cap_n_from_config duplicated across the
    CORE_MODULE boundary) must return the same answer for the same file, or
    the two sources-of-truth defect this round fixes reappears one level
    down."""
    path = _write_config(tmp_path, n)
    assert runner_mod.turn_cap_n_from_config(path) == stats._turn_cap_n_from_config(path)


def test_yaml_wins_when_no_flag_is_given(tmp_path):
    path = _write_config(tmp_path, 20)
    assert runner_mod.resolve_turn_cap_n(path, None) == (20, "yaml")
    assert stats._resolve_turn_cap_n(path, None) == (20, "yaml")


def test_flag_overrides_yaml(tmp_path):
    path = _write_config(tmp_path, 20)
    assert runner_mod.resolve_turn_cap_n(path, 30) == (30, "flag")
    assert stats._resolve_turn_cap_n(path, 30) == (30, "flag")


@pytest.mark.parametrize("n", [None, _MISSING])
def test_yaml_null_or_absent_is_unset(tmp_path, n):
    path = _write_config(tmp_path, n)
    assert runner_mod.resolve_turn_cap_n(path, None) == (None, "unset")
    assert stats._resolve_turn_cap_n(path, None) == (None, "unset")


def test_missing_config_file_is_unset():
    ghost = "/no/such/config/exists-for-real.yaml"
    assert runner_mod.resolve_turn_cap_n(ghost, None) == (None, "unset")
    assert stats._resolve_turn_cap_n(ghost, None) == (None, "unset")


# --------------------------------------------------------------------------- #
# The config-driven N actually caps, end to end, per reader
# --------------------------------------------------------------------------- #
def test_ladder_caps_from_config_alone_no_flag(tmp_path):
    """Extends the byte-identical positive control: this is the SAME fixture
    and the SAME N as the flag-driven test above, reached the other way --
    through --config, with --turn-cap-n never given -- and it must produce
    the identical excluded/kept split."""
    config = _write_config(tmp_path, 20)
    results = tmp_path / "results.jsonl"
    _write_jsonl(results, FIXTURE)

    n, source = runner_mod.resolve_turn_cap_n(config, None)
    assert (n, source) == (20, "yaml")
    kept, excluded = ladder.load_rows(str(results), None, None, False, turn_cap_n=n)
    assert excluded == {"turn_cap": 2}
    assert len(kept) == 3


def test_ladder_flag_30_overrides_yaml_20(tmp_path):
    """With N=30 instead of 20, only turns=40 exceeds the cap -- turns=21 no
    longer does -- which is what makes this differ observably from the yaml
    default and proves the flag, not the file, won."""
    config = _write_config(tmp_path, 20)
    results = tmp_path / "results.jsonl"
    _write_jsonl(results, FIXTURE)

    n, source = runner_mod.resolve_turn_cap_n(config, 30)
    assert (n, source) == (30, "flag")
    kept, excluded = ladder.load_rows(str(results), None, None, False, turn_cap_n=n)
    assert excluded == {"turn_cap": 1}
    assert len(kept) == 4


def test_tables_caps_from_config_alone_no_flag(tmp_path):
    config = _write_config(tmp_path, 20)
    n, source = runner_mod.resolve_turn_cap_n(config, None)
    out = tables.build_report(FIXTURE, {}, turn_cap_n=n, turn_cap_n_source=source)
    assert "turn_cap_n=20 (source=yaml)" in out
    assert "turn_cap=2" in out
    assert "67%" in out


def test_stats_caps_from_config_alone_no_flag(tmp_path):
    config = _write_config(tmp_path, 20)
    n, source = stats._resolve_turn_cap_n(config, None)
    out = stats.build_report(FIXTURE, [], turn_cap_n=n, turn_cap_n_source=source)
    assert "turn_cap_n=20 (source=yaml)" in out
    assert "3 run row(s), 2 passing" in out


# --------------------------------------------------------------------------- #
# yaml absent/null -> "unset", byte-identical to today (extends the N=null
# positive control to the config-driven path specifically)
# --------------------------------------------------------------------------- #
def test_tables_yaml_absent_is_unset_and_byte_identical(tmp_path):
    config = _write_config(tmp_path, _MISSING)
    n, source = runner_mod.resolve_turn_cap_n(config, None)
    assert (n, source) == (None, "unset")
    with_config = tables.build_report(FIXTURE, {}, turn_cap_n=n, turn_cap_n_source=source)
    bypassed = tables.build_report(FIXTURE, {})
    assert with_config == bypassed
    assert "turn_cap_n=unset" in with_config


def test_stats_yaml_null_is_unset_and_byte_identical(tmp_path):
    config = _write_config(tmp_path, None)
    n, source = stats._resolve_turn_cap_n(config, None)
    assert (n, source) == (None, "unset")
    with_config = stats.build_report(FIXTURE, [], turn_cap_n=n, turn_cap_n_source=source)
    bypassed = stats.build_report(FIXTURE, [])
    assert with_config == bypassed
    assert "turn_cap_n=unset" in with_config


def test_ladder_yaml_absent_is_unset_and_matches_no_config(tmp_path):
    config = _write_config(tmp_path, _MISSING)
    results = tmp_path / "results.jsonl"
    _write_jsonl(results, FIXTURE)

    n, source = runner_mod.resolve_turn_cap_n(config, None)
    assert (n, source) == (None, "unset")
    with_config = ladder.load_rows(str(results), None, None, False, turn_cap_n=n)
    bypassed = ladder.load_rows(str(results), None, None, False)
    assert with_config == bypassed


# --------------------------------------------------------------------------- #
# turns_missing -- rows with no `turns` field, disclosed when N is set
# --------------------------------------------------------------------------- #
FIXTURE_WITH_MISSING_TURNS = FIXTURE + [mkrow(6, None, "ok", True)]


def test_tables_prints_turns_missing_when_n_is_set():
    out = tables.build_report(FIXTURE_WITH_MISSING_TURNS, {}, turn_cap_n=N)
    assert "turns_missing=1" in out


def test_stats_prints_turns_missing_when_n_is_set():
    out = stats.build_report(FIXTURE_WITH_MISSING_TURNS, [], turn_cap_n=N)
    assert "turns_missing=1" in out


def test_tables_omits_turns_missing_when_n_is_unset():
    """Printed only when it means something: with no cap in force, whether a
    row carries `turns` is not yet a fact anyone needs on this line."""
    out = tables.build_report(FIXTURE_WITH_MISSING_TURNS, {})
    assert "turns_missing" not in out
    assert "turn_cap_n=unset" in out


def test_ladder_count_turns_missing(tmp_path):
    results = tmp_path / "results.jsonl"
    _write_jsonl(results, FIXTURE_WITH_MISSING_TURNS)
    assert ladder.count_turns_missing(str(results), None, None) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

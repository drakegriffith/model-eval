"""Regression tests for runner/run.py's instrument seams.

Seams under test:
  - existing_ids(results_path), the public function main() uses to build the
    resume set. See ticket: a row's presence in results.jsonl must not by
    itself mark its run_id "done" -- only a genuinely complete run should.
  - loc_changed(scratch), ticket 22 defect 1: the field must measure the model's
    work, not the model's git habit.
  - resolve_timeout_s(task, defaults), ticket 22 defect 2: the wall-clock cap a
    task runs under must be declared for that task's tier, never inherited by
    falling off the end of a boolean.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import (  # noqa: E402
    build_runs, existing_ids, parse_usage, parse_yaml, prepare_scratch,
    loc_changed, resolve_timeout_s,
)


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


# --------------------------------------------------------------------------- #
# resolve_timeout_s -- ticket 22 defect 2
# --------------------------------------------------------------------------- #
# The caps the sweeps actually shipped with: a short one for t1/t2 and a long one
# for t3. The defect is what any OTHER tier gets.
SHIPPED = {"timeout_t1_t2_s": 1200, "timeout_t3_s": 3600}


def _old_timeout_s(task, defaults):
    """run.py:1035 before the fix, verbatim. Present so the tests below can show
    the defect is live rather than assert against a remembered description of it.
    """
    tier3 = task.startswith("t3")
    return defaults.get("timeout_t3_s", 3600) if tier3 else defaults.get("timeout_t1_t2_s", 1200)


def test_unknown_tier_does_not_silently_receive_timeout_t1_t2_s():
    """Ticket 22 defect 2, the eval bar verbatim.

    t4 and t5 tasks exist and are nobody's t1/t2. Cap-terminated runs score as
    FAILURES under the pre-registration's estimand, so a cap sized for a 20-min
    task and handed to a multi-file one converts an instrument property into
    apparent task difficulty. Loud failure is the only safe default: it costs a
    config edit, and the alternative costs a result.
    """
    for task in ("t4-py-a", "t5-ts-a"):
        # Control arm: the fixture must actually reproduce the defect condition.
        # If the old expression stopped returning the short cap here, this test
        # would pass for a reason that has nothing to do with the fix. That
        # assertion is a test, not a comment.
        assert _old_timeout_s(task, SHIPPED) == 1200

        try:
            got = resolve_timeout_s(task, SHIPPED)
        except ValueError:
            continue
        assert False, f"{task} silently inherited a cap of {got}s"


def test_the_raised_error_names_the_task_and_the_missing_key():
    """A cap that has to be declared is only cheaper than a wrong cap if the
    message says which one to declare and for which task."""
    try:
        resolve_timeout_s("t6-py-a", SHIPPED)
    except ValueError as e:
        assert "t6-py-a" in str(e)
        assert "timeout_t6_s" in str(e)
    else:
        assert False, "an undeclared tier must raise"


def test_a_declared_tier_key_resolves():
    assert resolve_timeout_s("t4-py-a", dict(SHIPPED, timeout_t4_s=3600)) == 3600
    assert resolve_timeout_s("t5-ts-a", dict(SHIPPED, timeout_t5_s=3600)) == 3600


def test_a_declared_tier_key_beats_the_legacy_key():
    """timeout_t1_t2_s covers two tiers at once. A per-tier key must win over it,
    or t2 could never be capped separately from t1."""
    defaults = dict(SHIPPED, timeout_t2_s=2400)
    assert resolve_timeout_s("t2-py-b", defaults) == 2400
    assert resolve_timeout_s("t1-py-a", defaults) == 1200


def test_the_legacy_keys_still_resolve_t1_t2_and_t3():
    """Every archived row was produced under these two keys. The fix must not
    change what any already-run config resolves to -- a sweep that replays to a
    different cap is a sweep that no longer replays.
    """
    for task in ("t1-py-a", "t1-ts-b", "t2-py-a", "t2-ts-b"):
        assert resolve_timeout_s(task, SHIPPED) == 1200 == _old_timeout_s(task, SHIPPED)
    assert resolve_timeout_s("t3-a", SHIPPED) == 3600 == _old_timeout_s("t3-a", SHIPPED)


def test_timeout_default_s_is_the_last_resort_and_only_when_declared():
    """An explicit catch-all is a decision on the record. Its absence is what
    makes the raise above possible, so it stays opt-in.
    """
    assert resolve_timeout_s("t6-py-a", dict(SHIPPED, timeout_default_s=3600)) == 3600
    # ...and a per-tier key still wins over it.
    assert resolve_timeout_s(
        "t6-py-a", dict(SHIPPED, timeout_default_s=3600, timeout_t6_s=900)) == 900


def test_a_task_with_no_tier_prefix_is_not_guessed_at():
    """mock-task and friends parse to no tier at all. Same posture: the catch-all
    or nothing -- never the t1/t2 cap by accident of string matching.
    """
    try:
        resolve_timeout_s("mock-task", SHIPPED)
    except ValueError as e:
        assert "mock-task" in str(e)
    else:
        assert False, "an unparseable tier must raise"
    assert resolve_timeout_s("mock-task", dict(SHIPPED, timeout_default_s=60)) == 60


def test_every_shipped_config_resolves_a_cap_for_every_task_it_schedules():
    """The fleet-level version of the same assertion. A config file that cannot
    name a cap for a task it schedules is now a config bug, and this test is
    where it surfaces -- at zero cost, not 40 runs into a sweep.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    configs = [os.path.join(here, f) for f in sorted(os.listdir(here))
               if f.startswith("runs") and f.endswith(".yaml")]
    configs.append(os.path.join(here, "fixtures", "pilot.yaml"))
    assert len(configs) > 1, "config discovery found nothing to check"

    for path in configs:
        with open(path, encoding="utf-8") as f:
            cfg = parse_yaml(f.read())
        defaults = cfg.get("defaults", {}) or {}
        for run in build_runs(cfg):
            cap = resolve_timeout_s(run["task"], defaults)
            assert cap > 0, f"{os.path.basename(path)}: {run['task']} -> {cap}"

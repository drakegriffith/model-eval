"""test_run_id.py -- blocker 3 of studio-handoff findings.md (issue #8): the
run_id format was a comment, and readers parsed it by counting from the end.

judge.py carried the whole contract as a comment --

    # run_id = sweep--model--effort--harness--<task>--rEP
    parts = run_id.split("--")
    task = parts[-2] if len(parts) >= 2 else ""

-- so the format's only enforcement was that every writer happened to agree
with it. The stage-1 design adds segments (agent, harness_level). Appended at
the end, `parts[-2]` becomes the rep or the new segment, every run in the sweep
resolves the same wrong task directory, and the judge scores every diff against
"(task prompt unavailable)" without one error being raised. Inserted before the
last two, it works -- and nothing anywhere said which of those two things to do.

This file makes the format a parser with an anchor: the last segment IS the
rep, `r` followed by digits, so an id built the wrong way FAILS rather than
mis-parsing. That is the difference the blocker is about. A wrong answer that
raises costs one run; a wrong answer that parses costs a whole cell of data
that looks like data.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run_id as R  # noqa: E402
import judge  # noqa: E402
import run as runner  # noqa: E402

# A real id from the archived corpus (runner/results/results.jsonl). All 268
# archived run_ids are this shape; a parser that cannot read them would silently
# orphan the corpus, so this is the positive control on the whole file.
ARCHIVED = "sweep1--fable--high--bare--t1-py-a--r1"


# --------------------------------------------------------------------------- #
# The legacy shape still reads.
# --------------------------------------------------------------------------- #
def test_an_archived_run_id_parses_into_named_fields():
    p = R.parse_run_id(ARCHIVED)

    assert p["sweep"] == "sweep1"
    assert p["model"] == "fable"
    assert p["effort"] == "high"
    assert p["harness"] == "bare"
    assert p["task"] == "t1-py-a"
    assert p["rep"] == 1
    assert p["extra"] == ()


def test_build_round_trips_the_archived_shape():
    built = R.build_run_id(sweep="sweep1", model="fable", effort="high",
                           harness="bare", task="t1-py-a", rep=1)

    assert built == ARCHIVED


def test_the_runner_builds_ids_this_parser_accepts():
    """The construction site and the reader must agree, or the contract is
    still just a comment. Drives the REAL build_runs."""
    cfg = {"sweeps": [{"name": "sweep1", "tasks": ["t1-py-a"], "reps": [1, 2],
                       "harness_matrix": [False, True],
                       "configs": [{"model": "claude-haiku-4-5",
                                    "effort": "low"}]}]}

    runs = runner.build_runs(cfg)

    assert runs, "no runs built -- nothing was inspected"
    for r in runs:
        p = R.parse_run_id(r["run_id"])
        assert p["task"] == r["task"]
        assert p["rep"] == r["rep"]
        assert p["harness"] == ("harness" if r["harness"] else "bare")


# --------------------------------------------------------------------------- #
# The rule the blocker is about: new segments go BEFORE the last two.
# --------------------------------------------------------------------------- #
def test_a_new_segment_is_inserted_before_task_and_rep():
    extended = R.build_run_id(sweep="s1", model="glm-4.7-local", effort="high",
                              harness="bare", task="t1-py-a", rep=1,
                              extra=("agent=explore", "hl=2"))

    assert extended == "s1--glm-4.7-local--high--bare--agent=explore--hl=2--t1-py-a--r1"
    p = R.parse_run_id(extended)
    assert p["task"] == "t1-py-a"
    assert p["rep"] == 1
    assert p["extra"] == ("agent=explore", "hl=2")
    assert p["labels"] == {"agent": "explore", "hl": "2"}


def test_extending_a_run_id_does_not_move_task_or_rep():
    """The property that makes an extension safe, asserted directly against the
    unextended id rather than against a literal."""
    base = R.parse_run_id(R.build_run_id(
        sweep="s1", model="m", effort="high", harness="bare", task="t4-py-b",
        rep=3))
    ext = R.parse_run_id(R.build_run_id(
        sweep="s1", model="m", effort="high", harness="bare", task="t4-py-b",
        rep=3, extra=("agent=plan", "hl=4")))

    assert (ext["task"], ext["rep"]) == (base["task"], base["rep"])


def test_cells_differing_only_by_a_new_segment_are_distinct_and_readable():
    """The dedupe half. existing_ids() keys on the whole run_id string, so two
    cells that differ only by agent must produce two ids -- and each must still
    resolve its own task, or the judge scores both against one prompt."""
    ids = {R.build_run_id(sweep="s1", model="m", effort="high", harness="bare",
                          task="t2-py-a", rep=1, extra=(f"agent={a}",))
           for a in ("explore", "plan", "solo")}

    assert len(ids) == 3
    for rid in ids:
        assert R.parse_run_id(rid)["task"] == "t2-py-a"


def test_appending_a_segment_at_the_end_is_refused_not_mis_parsed():
    """THE regression. Before this module a reader took parts[-2] and quietly
    returned "r1" as the task name; the run went on, the judge read no prompt,
    and the row looked like data. The anchor makes the wrong construction loud.
    """
    wrong = ARCHIVED + "--agent=explore"

    # The silent failure this replaces, shown rather than described:
    assert wrong.split("--")[-2] == "r1"

    with pytest.raises(R.RunIdError) as exc:
        R.parse_run_id(wrong)
    assert "r<rep>" in str(exc.value)


def test_the_rep_segment_must_be_r_plus_digits():
    with pytest.raises(R.RunIdError):
        R.parse_run_id("s1--m--high--bare--t1-py-a--rep1")


def test_too_few_segments_is_refused():
    with pytest.raises(R.RunIdError):
        R.parse_run_id("s1--m--t1-py-a--r1")


def test_a_field_containing_the_delimiter_is_refused_at_build_time():
    """Fail where the id is made, not where it is read: a task named "a--b"
    would parse as two segments and silently shift everything after it."""
    with pytest.raises(R.RunIdError):
        R.build_run_id(sweep="s1", model="m", effort="high", harness="bare",
                       task="a--b", rep=1)


def test_an_empty_field_is_refused_at_build_time():
    with pytest.raises(R.RunIdError):
        R.build_run_id(sweep="s1", model="", effort="high", harness="bare",
                       task="t1-py-a", rep=1)


# --------------------------------------------------------------------------- #
# The judge ledger's own ids live in a different namespace.
# --------------------------------------------------------------------------- #
def test_a_judge_ledger_id_is_not_read_as_a_worker_id():
    """judge.py mints "judge-<head>--<judged_run_id>", which PREPENDS a segment
    -- so a worker parser reads sweep="judge-claude", model="sweep1", effort=
    the model, and so on down the line, while task and rep still look right.
    Every front field is wrong and nothing says so. Refused explicitly."""
    jid = R.build_judge_run_id("claude", ARCHIVED)

    assert jid == "judge-claude--" + ARCHIVED
    assert R.is_judge_run_id(jid)
    with pytest.raises(R.RunIdError):
        R.parse_run_id(jid)


def test_a_judge_id_names_the_run_it_judged():
    jid = R.build_judge_run_id("codex", ARCHIVED)

    assert R.parse_judge_run_id(jid) == {"head": "codex", "judged_run_id": ARCHIVED}
    assert R.parse_run_id(R.parse_judge_run_id(jid)["judged_run_id"])["task"] == "t1-py-a"


# --------------------------------------------------------------------------- #
# The consumer that carried the contract as a comment.
# --------------------------------------------------------------------------- #
@pytest.fixture
def tasks_dir(tmp_path):
    d = tmp_path / "tasks" / "t1-py-a"
    d.mkdir(parents=True)
    (d / "PROMPT.md").write_text("paginate the thing", encoding="utf-8")
    return str(tmp_path / "tasks")


def test_the_judge_reads_the_prompt_of_an_archived_run(tasks_dir):
    assert "paginate the thing" in judge.read_prompt_for_run(ARCHIVED, tasks_dir)


def test_the_judge_reads_the_prompt_of_an_extended_run(tasks_dir):
    """The blocker's payload: an id carrying agent/harness_level must still
    resolve its own task, or every judged cell scores against a prompt that was
    never read."""
    extended = R.build_run_id(sweep="sweep1", model="glm-4.7-local",
                              effort="high", harness="bare", task="t1-py-a",
                              rep=1, extra=("agent=explore", "hl=2"))

    assert "paginate the thing" in judge.read_prompt_for_run(extended, tasks_dir)


def test_the_judge_refuses_a_malformed_run_id(tasks_dir):
    """It used to return "(task prompt unavailable)" and grade anyway. Scoring a
    diff against a prompt nobody could find is not a judgement, and it lands in
    judgments.jsonl indistinguishable from one."""
    with pytest.raises(R.RunIdError):
        judge.read_prompt_for_run(ARCHIVED + "--agent=explore", tasks_dir)


def test_the_judge_ledger_row_is_built_through_the_shared_id_rule(tmp_path):
    """meter_judge_call mints the judge row's id; it must come from the one
    builder so the two namespaces cannot drift apart."""
    usage_path = str(tmp_path / "usage.jsonl")
    raw = json.dumps({"type": "result", "num_turns": 1,
                      "usage": {"input_tokens": 10, "output_tokens": 2}})

    judge.meter_judge_call(ARCHIVED, "claude", "claude", "claude-opus-4-8",
                           raw, usage_path)

    with open(usage_path, encoding="utf-8") as f:
        row = json.loads(f.readline())
    assert row["run_id"] == R.build_judge_run_id("claude", ARCHIVED)
    assert row["judged_run_id"] == ARCHIVED

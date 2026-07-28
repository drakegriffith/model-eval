"""Hidden acceptance suite for tasks/t5-py-b.

Never ships inside `base/`. Run by `verify.sh` with `--tb=no -rN`, so a
failure reports a count and a position in the progress line — never a test
name, a path, or an assertion. Everything asserted here is specified in
`PROMPT.md`; nothing is invented.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path.cwd() / "src"))

from relay import Abort, Done, drain, run_stage  # noqa: E402


def test_ac_01():
    # rule 1: a bare Done() is success with a None payload, NOT an abort
    def stage(_):
        raise Done()

    assert run_stage(stage, "seed", 3) == {"status": "done", "payload": None, "attempts": 1}


def test_ac_02():
    # rule 1: Done(None) is success too — only a RETURNED None aborts
    def stage(_):
        raise Done(None)

    assert run_stage(stage, "seed", 3)["status"] == "done"


def test_ac_03():
    # rule 4: an unrelated exception propagates unchanged, never a status
    def stage(_):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        run_stage(stage, "seed", 3)


def test_ac_04():
    # rule 4: and it propagates out of drain as well
    def ok(n):
        raise Done(n)

    def bad(_):
        raise KeyError("missing")

    with pytest.raises(KeyError):
        drain([ok, bad], 1, 3)


def test_ac_05():
    # rule 4: a subclass of Exception raised after a retry still propagates
    state = {"n": 0}

    def stage(arg):
        state["n"] += 1
        if state["n"] == 1:
            return "again"
        raise RuntimeError("late")

    with pytest.raises(RuntimeError):
        run_stage(stage, "seed", 4)


def test_ac_06():
    # rule 2: max_attempts counts invocations — 1 means exactly one call
    calls = []

    def stage(arg):
        calls.append(arg)
        return "hint"

    out = run_stage(stage, "seed", 1)
    assert out == {"status": "exhausted", "payload": "hint", "attempts": 1}
    assert calls == ["seed"]


def test_ac_07():
    # rule 3: a None returned on a later attempt aborts there, with its count
    def stage(arg):
        if arg == "seed":
            return "hint"
        return None

    assert run_stage(stage, "seed", 9) == {"status": "aborted", "payload": None, "attempts": 2}


def test_ac_08():
    # rule 5: retry hints never cross a stage boundary — only Done payloads do
    seen = []

    def first(n):
        if n == 1:
            return 99          # a hint, not a result
        raise Done("payload")

    def second(arg):
        seen.append(arg)
        raise Done(arg)

    out = drain([first, second], 1, 4)
    assert seen == ["payload"]
    assert out["payload"] == "payload"


def test_ac_09():
    # rule 6: attempts lists only the stages actually invoked
    def one(n):
        raise Done(n)

    def two(n):
        if n == 1:
            return 2
        raise Done(n)

    def three(_):  # pragma: no cover - must not run
        raise Done("nope")

    def stuck(n):
        return n

    out = drain([one, two, stuck, three], 1, 3)
    assert out["status"] == "exhausted"
    assert out["attempts"] == [1, 2, 3]
    assert out["stages"] == 3


def test_ac_10():
    # rule 6: an Abort inside drain stops it and reports the reason
    def one(n):
        raise Done(n)

    def two(_):
        raise Abort("dead letter")

    def three(_):  # pragma: no cover - must not run
        raise Done("nope")

    out = drain([one, two, three], 1, 3)
    assert out["status"] == "aborted"
    assert out["payload"] == "dead letter"
    assert out["stages"] == 2
    assert out["attempts"] == [1, 1]


def test_ac_11():
    # rule 6: whichever stops it FIRST in stage order wins
    def stuck(n):
        return n

    def aborts(_):  # pragma: no cover - must not run
        raise Abort("later")

    out = drain([stuck, aborts], 1, 2)
    assert out["status"] == "exhausted"
    assert out["stages"] == 1


def test_ac_12():
    # rule 6: an empty pipeline is done, and the payload is the seed
    assert drain([], "seed", 3) == {
        "status": "done", "payload": "seed", "attempts": [], "stages": 0}


def test_ac_13():
    # rule 6: a fully successful pipeline reports the LAST stage's payload
    def a(n):
        raise Done(n + 1)

    def b(n):
        raise Done(n * 10)

    out = drain([a, b], 1, 2)
    assert out == {"status": "done", "payload": 20, "attempts": [1, 1], "stages": 2}


def test_ac_14():
    # rule 1 + rule 5: a Done(None) mid-pipeline feeds None on, and is not an abort
    seen = []

    def a(_):
        raise Done(None)

    def b(arg):
        seen.append(arg)
        raise Done("end")

    out = drain([a, b], "seed", 2)
    assert seen == [None]
    assert out["status"] == "done"
    assert out["payload"] == "end"


def test_ac_15():
    # rule 5: drain does not mutate the stage list it was given
    def a(n):
        raise Done(n)

    stages = [a, a]
    drain(stages, 1, 2)
    assert stages == [a, a]


def test_ac_16():
    # rule 3: a returned None inside drain aborts the pipeline with payload None
    def a(n):
        raise Done(n)

    def b(_):
        return None

    def c(_):  # pragma: no cover - must not run
        raise Done("nope")

    out = drain([a, b, c], 1, 3)
    assert out["status"] == "aborted"
    assert out["payload"] is None
    assert out["stages"] == 2

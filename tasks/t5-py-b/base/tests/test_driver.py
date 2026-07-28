import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from relay import Abort, Done, drain, run_stage


def test_raising_done_is_success():
    def stage(_):
        raise Done("ok")

    assert run_stage(stage, "seed", 3) == {"status": "done", "payload": "ok", "attempts": 1}


def test_a_returned_value_is_a_retry_hint_fed_back_in():
    seen = []

    def stage(arg):
        seen.append(arg)
        if arg == "seed":
            return "hint"
        raise Done(arg)

    assert run_stage(stage, "seed", 3) == {"status": "done", "payload": "hint", "attempts": 2}
    assert seen == ["seed", "hint"]


def test_running_out_of_attempts_is_exhaustion_not_an_error():
    def stage(arg):
        return arg + 1

    assert run_stage(stage, 0, 2) == {"status": "exhausted", "payload": 2, "attempts": 2}


def test_a_returned_none_aborts():
    calls = []

    def stage(arg):
        calls.append(arg)
        return None

    assert run_stage(stage, "seed", 5) == {"status": "aborted", "payload": None, "attempts": 1}
    assert calls == ["seed"]


def test_abort_is_reported_not_re_raised():
    def stage(_):
        raise Abort("upstream gone")

    assert run_stage(stage, "seed", 3) == {
        "status": "aborted", "payload": "upstream gone", "attempts": 1}


def test_drain_passes_done_payloads_between_stages():
    def double(n):
        raise Done(n * 2)

    def stringify(n):
        raise Done(f"<{n}>")

    out = drain([double, stringify], 3, 4)
    assert out["status"] == "done"
    assert out["payload"] == "<6>"


def test_drain_stops_at_the_first_stage_that_does_not_finish():
    invoked = []

    def first(n):
        raise Done(n)

    def stuck(n):
        return n

    def never(n):  # pragma: no cover - must not run
        invoked.append(n)
        raise Done("should not happen")

    out = drain([first, stuck, never], 1, 2)
    assert out["status"] == "exhausted"
    assert invoked == []

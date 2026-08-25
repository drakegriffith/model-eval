"""test_lms_preflight.py -- the pre-flight refusal: does the LIVE serving stack
match the registry row this experiment will be reported under?

WHY THIS IS SEPARATE FROM THE GATE. serving_registry's gate compares a run's
DECLARED serving config against its row, deterministically, from files in version
control. That is the right shape for gating, and it cannot detect the one thing
that actually goes wrong here: the declaration and the row agree with each other
while the SERVER is in some third state. Nothing in this repo can see that
without looking at the server.

So there are two mechanisms and they answer different questions:

    gate      declared config vs registry row      every dispatch, deterministic
    preflight LIVE server       vs registry row    once, before a stage starts

The pre-registration makes the second one a human's decision, not the code's:

    Serving config for every run: LM Studio PARALLEL=1, context 131072 ...
    If LM Studio is not already in this config, stop and ask Drake to set it;
    do not change it yourself.

This is the check that makes "stop and ask Drake" mechanical instead of a thing
someone remembers. It READS `lms ps` and nothing else -- it never loads, unloads
or reconfigures a model, and one of the tests below asserts that by inspecting
the argv it would run.

THE FIXTURES ARE REAL. lms-ps-live-mismatch.txt is captured verbatim from
`~/.lmstudio/bin/lms ps` on the Mac Studio on 2026-08-25 and shows the state the
server is in TODAY: CONTEXT=65536, PARALLEL=4, against a row pinning 131072 and
1. lms-ps-target-state.txt is the same output with those two columns edited to
the target values -- constructed, and labelled as such, because the server has
never been in that state for anyone to capture.
"""
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
sys.path.insert(0, RUNNER_DIR)
import serving_registry as sr  # noqa: E402

LIVE_MISMATCH = os.path.join(FIXTURES, "lms-ps-live-mismatch.txt")
TARGET_STATE = os.path.join(FIXTURES, "lms-ps-target-state.txt")


def fixture(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# Parsing real `lms ps` output
# --------------------------------------------------------------------------- #
def test_the_live_capture_parses_into_the_two_numbers_that_matter():
    """Column-sliced on the header's own offsets rather than split on
    whitespace: SIZE is '158.74 GB', two tokens in one column, so a naive split
    shifts CONTEXT and PARALLEL one place left and silently reads the wrong
    numbers."""
    loaded = sr.parse_lms_ps(fixture(LIVE_MISMATCH))

    assert len(loaded) == 1
    assert loaded[0]["identifier"] == "glm-4.7"
    assert loaded[0]["size"] == "158.74 GB", (
        "the two-token SIZE column was split; every column after it is shifted")
    assert loaded[0]["context_length"] == 65536
    assert loaded[0]["parallel"] == 4


def test_the_target_state_capture_parses_too():
    loaded = sr.parse_lms_ps(fixture(TARGET_STATE))

    assert loaded[0]["context_length"] == 131072
    assert loaded[0]["parallel"] == 1


def test_output_with_no_header_is_refused_rather_than_read_as_empty():
    """An empty parse and an unparseable one must not look alike. 'No models
    loaded' is a fact; 'I could not read this' is a failure to look, and reading
    the second as the first is how a pre-flight passes on nothing."""
    with pytest.raises(sr.RegistryError, match="header"):
        sr.parse_lms_ps("lms: command not found\n")


def test_a_header_with_no_rows_is_an_empty_list_not_an_error():
    """The genuine 'nothing loaded' case, which IS parseable and IS empty."""
    header = fixture(LIVE_MISMATCH).splitlines()[1]

    assert sr.parse_lms_ps("\n" + header + "\n") == []


# --------------------------------------------------------------------------- #
# The refusal
# --------------------------------------------------------------------------- #
def test_todays_live_state_is_refused_naming_expected_and_observed():
    """The motivating case. This is the state the Studio is in right now, and
    dispatching stage 1 against it would produce 45 rows labelled with a serving
    config they were not produced under."""
    row = sr.find_row(sr.load_rows(), "glm-4.7", "claude-code")
    observed = sr.parse_lms_ps(fixture(LIVE_MISMATCH))[0]

    with pytest.raises(sr.PreflightMismatch) as exc:
        sr.check_live_serving(row, observed)

    message = str(exc.value)
    for expected_fragment in ("context_length", "131072", "65536",
                              "parallel", "4"):
        assert expected_fragment in message, f"message never says {expected_fragment}"
    # And it must say who fixes it, because the pre-registration makes that a
    # human's job and this code must not be read as offering to do it.
    assert "ask Drake" in message or "human" in message


def test_the_target_state_passes_and_says_what_it_inspected():
    """The positive control. Without it, a refusal that fires on everything --
    including a correct server -- would look identical to a working check."""
    row = sr.find_row(sr.load_rows(), "glm-4.7", "claude-code")
    observed = sr.parse_lms_ps(fixture(TARGET_STATE))[0]

    inspected = sr.check_live_serving(row, observed)

    assert inspected == ("context_length", "parallel"), (
        "a pre-flight that compared zero fields has not passed; it has failed "
        "to look, and the count is how a caller tells those apart")


def test_a_model_that_is_not_loaded_is_could_not_inspect_not_a_pass():
    """Silence is not evidence. A stopped LM Studio produces a clean, empty,
    parseable table, and treating that as 'nothing disagreed' is the exact shape
    of a gate that inspected zero subjects and reported success."""
    with pytest.raises(sr.PreflightUninspectable, match="not loaded"):
        sr.observed_for("glm-4.7", [])


def test_the_identifier_is_matched_rather_than_the_first_row_taken():
    """Several models can be loaded at once; taking row 0 would pre-flight
    whichever happened to be listed first."""
    loaded = [{"identifier": "qwen3-coder-next", "context_length": 8192,
               "parallel": 9, "size": "1 GB", "status": "IDLE"},
              {"identifier": "glm-4.7", "context_length": 131072,
               "parallel": 1, "size": "158.74 GB", "status": "IDLE"}]

    assert sr.observed_for("glm-4.7", loaded)["parallel"] == 1


# --------------------------------------------------------------------------- #
# It reads, and only reads
# --------------------------------------------------------------------------- #
def test_the_only_command_it_runs_is_ps():
    """The whole file's safety claim, asserted on the argv rather than trusted.
    `lms load`, `lms unload` and `lms server` all exist; the pre-registration
    says do not change the server, so this must be unable to."""
    argv = sr.lms_ps_command()

    assert argv[-1] == "ps"
    assert len(argv) == 2
    for forbidden in ("load", "unload", "set", "server", "--"):
        assert forbidden not in argv[1:], f"the pre-flight can invoke {forbidden!r}"


# --------------------------------------------------------------------------- #
# The CLI, and its distinct exit codes
# --------------------------------------------------------------------------- #
def run_cli(*args):
    return subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "serving_registry.py"),
         "preflight", *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)


def test_cli_exits_zero_on_the_target_state():
    proc = run_cli("--lms-output", TARGET_STATE)

    assert proc.returncode == 0, proc.stdout
    assert "131072" in proc.stdout


def test_cli_exits_with_the_refusal_code_on_todays_state():
    """A DISTINCT code, not 1 and not 2. 2 is 'the config was rejected' and a
    caller scripting the stage has to be able to tell 'your matrix is wrong'
    from 'go and change the server'."""
    proc = run_cli("--lms-output", LIVE_MISMATCH)

    assert proc.returncode == sr.EXIT_PREFLIGHT_MISMATCH
    assert proc.returncode == 3
    assert "65536" in proc.stdout and "131072" in proc.stdout


def test_cli_exits_with_the_uninspectable_code_when_nothing_is_loaded():
    """Could-not-inspect is not a pass, and it is not the same failure as a
    mismatch either."""
    header = fixture(LIVE_MISMATCH).splitlines()[1]
    empty = os.path.join(FIXTURES, "lms-ps-nothing-loaded.txt")
    with open(empty, "w", encoding="utf-8") as f:
        f.write("\n" + header + "\n\n")
    try:
        proc = run_cli("--lms-output", empty)

        assert proc.returncode == sr.EXIT_PREFLIGHT_UNINSPECTABLE
        assert proc.returncode == 4
        assert proc.returncode != 0
    finally:
        os.remove(empty)


def test_the_three_exit_codes_are_distinct():
    assert len({0, sr.EXIT_PREFLIGHT_MISMATCH,
                sr.EXIT_PREFLIGHT_UNINSPECTABLE}) == 3

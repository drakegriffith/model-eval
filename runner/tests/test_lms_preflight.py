"""test_lms_preflight.py -- the pre-flight refusal that makes "stop and ask
Drake" enforceable instead of aspirational.

WHY IT IS SEPARATE FROM THE GATE. The serving gate (issue #12) compares a run's
DECLARED serving config against its registry row: deterministic, no network, the
same verdict tomorrow. It cannot tell you whether the server is actually loaded
that way. The pre-registration's instruction --

    "Serving config for every run: LM Studio PARALLEL=1, context 131072,
     temperature 0, seed 42, max_tokens >= 8192 [...] If LM Studio is not
     already in this config, stop and ask Drake to set it; do not change it
     yourself."  (prompt-2-run-experiment.md:22-25)

-- is a different obligation with a different remedy: a HUMAN changes the
server. This module is how a stage refuses to start, and it never writes.

THE FIXTURES ARE REAL. lms-ps-mismatched-65536-4.txt is the byte-for-byte output
of `~/.lmstudio/bin/lms ps` on this Mac Studio on 2026-08-25, captured with the
model loaded at CONTEXT=65536 PARALLEL=4 -- the state the pre-registration says
to stop on. lms-ps-target-131072-1.txt is the same table at the target config.
Testing both directions is the point: a checker asserted only against the bad
state passes by refusing everything.

No model is invoked and no LM Studio state is touched anywhere in this file.
"""
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import lms_preflight as pf  # noqa: E402
import serving_registry as sr  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
MISMATCHED = os.path.join(FIXTURES, "lms-ps-mismatched-65536-4.txt")
TARGET = os.path.join(FIXTURES, "lms-ps-target-131072-1.txt")
# A different model loaded, at the target config. A separate fixture rather than
# a str.replace on TARGET, because the columns are read by OFFSET: substituting a
# name of a different length shifts every column after it and the test would be
# exercising the parser's error path instead of the not-loaded path.
OTHER_MODEL = os.path.join(FIXTURES, "lms-ps-other-model-loaded.txt")


def fixture(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# Parsing -- the SIZE column contains a space, so splitting on whitespace loses
# the alignment. The header's own column offsets are the only reliable frame.
# --------------------------------------------------------------------------- #
def test_the_live_table_parses_into_named_columns():
    rows = pf.parse_ps(fixture(MISMATCHED))

    assert len(rows) == 1
    row = rows[0]
    assert row["identifier"] == "glm-4.7"
    assert row["model"] == "glm-4.7"
    assert row["status"] == "IDLE"
    assert row["size"] == "158.74 GB", "the SIZE column's space broke the frame"
    assert row["context"] == 65536
    assert row["parallel"] == 4


def test_the_target_fixture_parses_to_the_target_numbers():
    row = pf.parse_ps(fixture(TARGET))[0]

    assert row["context"] == 131072
    assert row["parallel"] == 1


def test_output_with_no_header_is_a_could_not_inspect_not_an_empty_pass():
    """A table nobody could read is not a table with no rows. Returning [] here
    would let a broken `lms` binary look exactly like an idle server, and the
    caller would proceed."""
    with pytest.raises(pf.PreflightUninspectable):
        pf.parse_ps("lms: command not found\n")


def test_a_header_missing_a_column_this_module_reads_is_refused_by_name():
    with pytest.raises(pf.PreflightUninspectable, match="PARALLEL"):
        pf.parse_ps("IDENTIFIER    MODEL      CONTEXT\n"
                    "glm-4.7       glm-4.7    65536\n")


def test_a_non_numeric_context_is_refused_rather_than_coerced():
    text = fixture(MISMATCHED).replace("65536 ", "many  ")
    with pytest.raises(pf.PreflightUninspectable):
        pf.parse_ps(text)


# --------------------------------------------------------------------------- #
# The refusal, against the shipped registry row
# --------------------------------------------------------------------------- #
def test_the_live_mismatch_is_refused_and_names_expected_versus_observed():
    """THE motivating state. The message has to carry both numbers, because the
    human acting on it is being asked to change a server setting and needs to
    know which one and to what."""
    row = sr.find_row(sr.load_rows(), "glm-4.7", "claude-code")

    with pytest.raises(pf.PreflightMismatch) as exc:
        pf.check_live_serving(row, fixture(MISMATCHED))

    msg = str(exc.value)
    assert "context_length" in msg and "131072" in msg and "65536" in msg
    assert "parallel" in msg and "4" in msg
    assert "do not change it yourself" in msg


def test_the_target_state_passes_and_reports_what_it_inspected():
    """The other half of the A/B, and the count: a checker that inspected zero
    subjects has not passed."""
    row = sr.find_row(sr.load_rows(), "glm-4.7", "claude-code")

    inspected = pf.check_live_serving(row, fixture(TARGET))

    assert inspected == 2, "expected both pinned fields to be compared"


def test_a_model_that_is_not_loaded_is_its_own_refusal():
    """Distinct from a mismatch: nothing is misconfigured, the model simply is
    not there, and the fix is different."""
    row = sr.find_row(sr.load_rows(), "glm-4.7", "claude-code")

    with pytest.raises(pf.PreflightNotLoaded, match="qwen3-cn"):
        pf.check_live_serving(row, fixture(OTHER_MODEL))


def test_an_empty_table_is_not_loaded_rather_than_a_silent_pass():
    row = sr.find_row(sr.load_rows(), "glm-4.7", "claude-code")
    header = fixture(TARGET).splitlines()[1]

    with pytest.raises(pf.PreflightNotLoaded):
        pf.check_live_serving(row, header + "\n")


# --------------------------------------------------------------------------- #
# The CLI: distinct exit codes, and no writes ever
# --------------------------------------------------------------------------- #
def run_cli(*args):
    return subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "lms_preflight.py")] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)


def test_cli_exit_0_on_the_target_state():
    proc = run_cli("--ps-file", TARGET)

    assert proc.returncode == 0, proc.stdout
    assert "2 field(s)" in proc.stdout


def test_cli_exit_3_on_a_mismatch_and_says_which_field():
    proc = run_cli("--ps-file", MISMATCHED)

    assert proc.returncode == 3, proc.stdout
    assert "context_length" in proc.stdout
    assert "65536" in proc.stdout and "131072" in proc.stdout


def test_cli_exit_4_when_the_model_is_not_loaded():
    proc = run_cli("--ps-file", OTHER_MODEL)

    assert proc.returncode == 4, proc.stdout
    assert "not loaded" in proc.stdout


def test_cli_exit_5_when_the_table_cannot_be_read(tmp_path):
    """Could-not-inspect is NOT a pass and gets its own code, so a caller
    branching on the exit status cannot read it as one."""
    path = tmp_path / "ps.txt"
    path.write_text("zsh: command not found: lms\n", encoding="utf-8")

    proc = run_cli("--ps-file", str(path))

    assert proc.returncode == 5, proc.stdout


def test_every_exit_code_this_tool_uses_is_distinct():
    codes = [pf.EXIT_OK, pf.EXIT_MISMATCH, pf.EXIT_NOT_LOADED,
             pf.EXIT_UNINSPECTABLE]
    assert len(set(codes)) == len(codes)
    assert pf.EXIT_OK == 0


def test_the_module_never_names_a_command_that_changes_lm_studio_state():
    """Structural, and cheap. `lms ps` reads; `load`, `unload` and `set` write.
    The pre-registration says a human changes the server, so this tool must not
    contain the vocabulary to do it -- a behavioural test cannot prove absence,
    but the source can be enumerated."""
    with open(os.path.join(RUNNER_DIR, "lms_preflight.py"), encoding="utf-8") as f:
        source = f.read()

    assert pf.LMS_SUBCOMMAND == ("ps",)
    for forbidden in ('"load"', '"unload"', '"set"', "'load'", "'unload'"):
        assert forbidden not in source, f"{forbidden} appears in a read-only tool"


def test_the_lms_binary_path_is_the_documented_one():
    assert pf.LMS_BIN.endswith(".lmstudio/bin/lms")

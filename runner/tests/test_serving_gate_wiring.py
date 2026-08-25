"""test_serving_gate_wiring.py -- issue #12: the serving gate had zero invokers.

PR #10 landed runner/serving_registry.py -- a per-(model, driver) registry and a
pre-dispatch gate with three refusal cases -- and nothing called it. A run could
be dispatched under any serving config at all and the gate would not see it, so
the registry was documentation.

WHAT IS ASSERTED HERE, AND HOW. Every test in this file drives the RUNNER'S OWN
ENTRY POINT in a subprocess -- `python3 runner/run.py --config ...` -- and reads
its exit code and its stdout. A test that called check_dispatch directly would
have gone green on the parent commit, where the gate was already correct and
simply unreachable; the claim under test is reachability, so nothing here
imports the gate to call it.

No model is invoked anywhere in this file: every case is refused before the
first CLI call, or runs with --limit 0.

THE MOTIVATING CASE, first test below: LM Studio on this Mac Studio is loaded at
CONTEXT=65536 PARALLEL=4 while the registry row -- and the pre-registration --
say 131072 and 1. A sweep launched today against the declared config must stop
before it spends anything.
"""
import json
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(RUNNER_DIR)
RUN_PY = os.path.join(RUNNER_DIR, "run.py")
sys.path.insert(0, RUNNER_DIR)

# The panel's measured config, as shipped on the glm-4.7 rows of models.yaml.
GOOD_SERVING = """serving:
  parallel: 1
  context_length: 131072
  max_tokens: 8192
  temperature: 0
  seed: 42
"""


def write_config(tmp_path, sweeps, serving=GOOD_SERVING, name="runs-test.yaml"):
    path = tmp_path / name
    path.write_text(
        "defaults:\n"
        "  timeout_default_s: 600\n"
        "  seed: 1337\n"
        f"{serving}"
        "\n"
        "sweeps:\n" + sweeps, encoding="utf-8")
    return str(path)


def run_runner(config, tmp_path, *extra):
    env = dict(os.environ)
    # The broker's K check is a separate pre-dispatch gate with its own ticket;
    # switching it off keeps these assertions about the serving gate.
    env["GAUNTLET_NO_BROKER"] = "1"
    proc = subprocess.run(
        [sys.executable, RUN_PY, "--config", config,
         "--results", str(tmp_path / "results.jsonl"),
         "--scratch", str(tmp_path / "scratch")] + list(extra),
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=120)
    return proc


GLM_SWEEP = """  - name: stage1
    driver: claude-code
    reps: [1]
    tasks: [t2-py-a]
    configs:
      - {model: glm-4.7-local, effort: high}
"""


# --------------------------------------------------------------------------- #
# (a) the gate is CALLED, with a real requested config, from the real path
# --------------------------------------------------------------------------- #
def test_a_config_contradicting_its_registry_row_exits_2_before_the_first_cli_call(
        tmp_path):
    """THE motivating case. 65536/4 is what `lms ps` reports on this machine
    right now; 131072/1 is what the row and the pre-registration say."""
    config = write_config(tmp_path, GLM_SWEEP, serving="""serving:
  parallel: 4
  context_length: 65536
  max_tokens: 8192
  temperature: 0
  seed: 42
""")

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 2, proc.stdout
    assert "config rejected" in proc.stdout
    assert "context_length" in proc.stdout and "65536" in proc.stdout
    assert "parallel" in proc.stdout and "131072" in proc.stdout
    # No run was listed, so nothing reached dispatch.
    assert "[RUN ]" not in proc.stdout


def test_a_matching_config_passes_the_gate_and_says_how_many_runs_it_checked(
        tmp_path):
    """The other half of the A/B. A suite asserting only refusals would go
    green against a gate that refuses everything, which is not a wiring."""
    config = write_config(tmp_path, GLM_SWEEP)

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 0, proc.stdout
    assert "serving gate: 1/1" in proc.stdout, proc.stdout
    assert "[RUN ]" in proc.stdout


def test_the_gate_reports_zero_checked_rather_than_staying_silent(tmp_path):
    """A model with no registry row is not gated -- there is no serving config
    to contradict for a hosted subscription model. That is a legitimate zero and
    it is PRINTED, because an unreported zero is indistinguishable from the
    state this ticket exists to end: a gate nobody called."""
    config = write_config(tmp_path, """  - name: control
    driver: claude-code
    reps: [1]
    tasks: [t2-py-a]
    configs:
      - {model: claude-sonnet-5, effort: high}
""")

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 0, proc.stdout
    assert "serving gate: 0/1" in proc.stdout, proc.stdout


# --------------------------------------------------------------------------- #
# The gate must not be wirable into a no-op.
# --------------------------------------------------------------------------- #
def test_a_config_declaring_no_serving_block_is_refused_not_waved_through(tmp_path):
    """UninspectedConfig, reached through the runner. A gate that inspected zero
    fields has not agreed with the row; it has failed to look at it."""
    config = write_config(tmp_path, GLM_SWEEP, serving="")

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 2, proc.stdout
    assert "zero requested serving fields" in proc.stdout
    assert "serving:" in proc.stdout, "the refusal must name where to declare it"


def test_an_empty_serving_block_is_refused_for_the_same_reason(tmp_path):
    config = write_config(tmp_path, GLM_SWEEP, serving="serving:\n")

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 2, proc.stdout
    assert "zero requested serving fields" in proc.stdout


def test_a_missing_driver_is_an_error_and_is_never_defaulted(tmp_path):
    """findings.md forbids merging pi rows into claude-code: pi is a separately
    reported vehicle contrast, so `.get("driver", "claude-code")` would file
    every pi run under the wrong row. The absence is an error."""
    config = write_config(tmp_path, """  - name: stage1
    reps: [1]
    tasks: [t2-py-a]
    configs:
      - {model: glm-4.7-local, effort: high}
""")

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 2, proc.stdout
    assert "driver" in proc.stdout
    assert "claude-code" in proc.stdout and "pi" in proc.stdout, (
        "the refusal should name the choice rather than hint at one")


def test_an_unknown_driver_is_refused_rather_than_resolved_to_a_default(tmp_path):
    config = write_config(tmp_path, """  - name: stage1
    driver: ollama
    reps: [1]
    tasks: [t2-py-a]
    configs:
      - {model: glm-4.7-local, effort: high}
""")

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 2, proc.stdout
    assert "ollama" in proc.stdout


def test_a_non_numeric_floor_request_exits_2_rather_than_a_traceback(tmp_path):
    """`unknown` is this registry's own sentinel, shipped on `quant`, so a
    caller copying a row into a request reaches it without doing anything
    strange. Before PR #10's guard it raised TypeError, which is not a
    ValueError and so walked past run.py's handler and ended the sweep in a
    traceback. Pinned from the runner's side."""
    config = write_config(tmp_path, GLM_SWEEP, serving="""serving:
  parallel: 1
  context_length: 131072
  max_tokens: unknown
  temperature: 0
  seed: 42
""")

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 2, proc.stdout
    assert "Traceback" not in proc.stdout
    assert "max_tokens_floor" in proc.stdout


def test_a_cap_above_the_floor_is_accepted_because_a_floor_is_a_minimum(tmp_path):
    config = write_config(tmp_path, GLM_SWEEP, serving="""serving:
  parallel: 1
  context_length: 131072
  max_tokens: 32000
  temperature: 0
  seed: 42
""")

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 0, proc.stdout


def test_a_cap_below_the_floor_is_refused(tmp_path):
    config = write_config(tmp_path, GLM_SWEEP, serving="""serving:
  parallel: 1
  context_length: 131072
  max_tokens: 600
  temperature: 0
  seed: 42
""")

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 2, proc.stdout
    assert "below the row's floor" in proc.stdout


# --------------------------------------------------------------------------- #
# (c) StructurallyImpossible is caught BEFORE ValueError
# --------------------------------------------------------------------------- #
PI_L5_SWEEP = """  - name: dose
    driver: pi
    harness_level: 5
    reps: [1]
    tasks: [t2-py-a]
    configs:
      - {model: glm-4.7-local, effort: high}
"""


def test_a_structurally_impossible_cell_does_not_abort_the_sweep(tmp_path):
    """StructurallyImpossible subclasses RegistryError subclasses ValueError, so
    a naive insertion into the existing try block makes one inexpressible cell
    end the whole matrix with exit 2. A matrix containing pi x L5 is not an
    invalid config; it is a valid matrix containing a cell that does not
    exist."""
    config = write_config(tmp_path, PI_L5_SWEEP + """  - name: dose-cc
    driver: claude-code
    harness_level: 5
    reps: [1]
    tasks: [t2-py-a]
    configs:
      - {model: glm-4.7-local, effort: high}
""")

    proc = run_runner(config, tmp_path, "--limit", "0")

    assert proc.returncode == 0, proc.stdout
    assert "structurally-impossible" in proc.stdout
    assert "total=1" in proc.stdout, (
        f"the impossible cell should be dropped from the matrix:\n{proc.stdout}")


def test_a_structurally_impossible_cell_is_recorded_rather_than_scored_zero(tmp_path):
    """A 0 asserts that the model attempted the task and failed it. A cell that
    cannot exist did not fail, so it is written with its own status and a null
    pass, never a false one."""
    config = write_config(tmp_path, PI_L5_SWEEP)

    proc = run_runner(config, tmp_path, "--limit", "0")

    assert proc.returncode == 0, proc.stdout
    results = tmp_path / "results.jsonl"
    assert results.exists(), proc.stdout
    rows = [json.loads(l) for l in results.read_text().splitlines() if l.strip()]
    assert len(rows) == 1, rows
    assert rows[0]["exit_reason"] == "structurally_impossible"
    assert rows[0]["pass"] is None, "a cell that cannot exist did not fail"
    assert rows[0]["driver"] == "pi"
    assert rows[0]["harness_level"] == 5


def test_the_impossible_cell_is_recorded_once_across_repeated_invocations(tmp_path):
    """The runner is resume-friendly and gets re-invoked; a status row that
    accumulates a duplicate per invocation would inflate any count taken off
    the corpus."""
    config = write_config(tmp_path, PI_L5_SWEEP)

    run_runner(config, tmp_path, "--limit", "0")
    run_runner(config, tmp_path, "--limit", "0")

    rows = [l for l in (tmp_path / "results.jsonl").read_text().splitlines() if l.strip()]
    assert len(rows) == 1


def test_a_genuine_config_error_still_exits_2_alongside_an_impossible_cell(tmp_path):
    """The two must not be collapsed. Dropping the impossible cell may not
    swallow a real config bug in the same matrix."""
    config = write_config(tmp_path, PI_L5_SWEEP + """  - name: typo
    driver: claude-code
    reps: [1]
    tasks: [t2-py-a]
    configs:
      - {model: glm-4.8-local, effort: high}
""")

    proc = run_runner(config, tmp_path, "--dry-run")

    assert proc.returncode == 2, proc.stdout
    assert "unknown model" in proc.stdout
    assert "structurally-impossible" in proc.stdout


def test_an_impossible_cell_is_not_reported_as_an_invalid_config(tmp_path):
    config = write_config(tmp_path, PI_L5_SWEEP)

    proc = run_runner(config, tmp_path, "--limit", "0")

    assert "config rejected" not in proc.stdout, proc.stdout


# --------------------------------------------------------------------------- #
# The docstring recipe must be the one that actually runs.
# --------------------------------------------------------------------------- #
def test_the_module_docstring_no_longer_advertises_an_undefined_helper():
    """PR #10's own note suggested a four-argument call, three of whose
    arguments did not exist. Issue #12 (b) asks for the corrected recipe to
    replace it, because a wiring note that cannot be followed is worse than
    none: it reads as an existing API."""
    import serving_registry  # noqa: E402  (path set at import time above)

    doc = serving_registry.__doc__
    assert "requested_serving_from(cfg)" not in doc
    assert 'r.get("driver", "claude-code")' not in doc
    assert "serving_model_name" in doc


@pytest.mark.parametrize("term", ["serving:", "driver:"])
def test_the_docstring_names_where_the_two_inputs_come_from(term):
    import serving_registry  # noqa: E402

    assert term in serving_registry.__doc__


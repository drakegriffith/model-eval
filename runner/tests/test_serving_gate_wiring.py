"""test_serving_gate_wiring.py -- issue #12: check_dispatch is CALLED on the
runner's pre-dispatch path.

WHAT WAS OPEN. PR #10 landed runner/serving_registry.py -- a per-(model, driver)
registry and a three-case pre-dispatch gate -- complete, tested, and with zero
invokers. `grep -rn check_dispatch` outside the module and its own tests returned
nothing. Until something calls it the registry is documentation: a run can be
dispatched under any serving config at all and the gate never sees it.

HOW THIS FILE PROVES THE WIRING. Not by calling check_dispatch. A test that
calls the gate directly re-proves PR #10 and would have passed on the day the
gate had no callers, which is the exact failure being fixed. Every assertion
here goes through the runner's own entry point -- `run.main()` in-process, or
`python3 runner/run.py` as a subprocess -- so what is under test is the dispatch
path, not the library.

The four refusals and the one non-refusal, per issue #12's Done means:

  - a run whose declared serving config contradicts its registry row exits 2
    before the first CLI call;
  - a gated run with no `driver` key exits 2; the driver is NEVER defaulted to
    claude-code, because that silently files every pi row against the
    claude-code row and findings.md reports pi as a separate vehicle contrast;
  - a gated run with no serving block exits 2 (UninspectedConfig), so the gate
    cannot be wired into a no-op by passing {};
  - a non-numeric floor request exits 2 naming the field, rather than ending the
    sweep in a TypeError traceback;
  - a StructurallyImpossible cell is dropped with its own status and the rest of
    the sweep proceeds -- a matrix containing an inexpressible cell is not an
    invalid config.

No model is invoked anywhere in this file; every runner call is --dry-run.
"""
import json
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(RUNNER_DIR)
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402
import serving_registry  # noqa: E402

# The panel's measured config, which is what runner/models.yaml pins for
# glm-4.7. Restated here as literals rather than read off the row: a test that
# builds its expectation from the row it is checking against cannot fail when
# the row is wrong.
GOOD_SERVING = {"parallel": 1, "context_length": 131072, "max_tokens": 8192,
                "temperature": 0, "seed": 42}


def write_config(tmp_path, serving=GOOD_SERVING, driver="claude-code",
                 model="glm-4.7-local", harness_level=None, name="gate.yaml"):
    """A minimal runs config for one GLM cell."""
    lines = ["defaults:", "  timeout_t1_t2_s: 1200", "  seed: 1337", ""]
    if serving is not None:
        lines.append("serving:")
        for key, value in serving.items():
            lines.append(f"  {key}: {value}")
        lines.append("")
    lines += ["sweeps:", "  - name: gatetest"]
    if driver is not None:
        lines.append(f"    driver: {driver}")
    if harness_level is not None:
        lines.append(f"    harness_level: {harness_level}")
    lines += ["    harness: false", "    reps: [1]", "    tasks: [t2-py-a]",
              "    configs:", f"      - {{model: {model}, effort: high}}"]
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def run_runner(config, tmp_path, extra=()):
    """The runner's real command-line entry, as a subprocess."""
    proc = subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "run.py"),
         "--config", config, "--dry-run",
         "--results", str(tmp_path / "results.jsonl"),
         "--scratch", str(tmp_path / "scratch"), *extra],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        timeout=120)
    return proc


# --------------------------------------------------------------------------- #
# The gate is on the dispatch path at all
# --------------------------------------------------------------------------- #
def test_check_dispatch_is_called_by_the_runners_own_entry(tmp_path, monkeypatch):
    """The zero-invokers regression, stated as a positive assertion. The spy
    replaces the gate and records what the RUNNER passed it, so this fails if
    main() stops calling it -- which is the state PR #10 shipped in."""
    calls = []

    def spy(rows, model, driver, requested_serving, harness_level=None,
            capability=None):
        calls.append({"model": model, "driver": driver,
                      "requested": dict(requested_serving),
                      "harness_level": harness_level})
        return serving_registry.find_row(rows, model, driver)

    monkeypatch.setattr(serving_registry, "check_dispatch", spy)
    monkeypatch.setattr(sys, "argv", [
        "run.py", "--config", write_config(tmp_path), "--dry-run",
        "--results", str(tmp_path / "results.jsonl"),
        "--scratch", str(tmp_path / "scratch")])

    runner.main()

    assert len(calls) == 1, (
        f"the gate was called {len(calls)} times on a 1-run matrix; PR #10 "
        f"shipped it with zero invokers and that is the regression this pins")
    assert calls[0]["model"] == "glm-4.7"
    assert calls[0]["driver"] == "claude-code"
    # Built from NUMBERS, not from row fields copied verbatim (issue #12 f).
    assert calls[0]["requested"] == GOOD_SERVING
    for value in calls[0]["requested"].values():
        assert isinstance(value, (int, float)), f"{value!r} is not a number"


def test_the_gate_actually_inspected_fields_rather_than_passing_empty(tmp_path,
                                                                     monkeypatch):
    """check_run_config returns the number of fields it compared precisely so a
    caller can tell a pass from a no-op. Assert on that count, through the
    runner: a gate that inspected zero fields has not agreed with the row."""
    inspected = []
    real = serving_registry.check_run_config

    def counting(row, requested):
        n = real(row, requested)
        inspected.append(n)
        return n

    monkeypatch.setattr(serving_registry, "check_run_config", counting)
    monkeypatch.setattr(sys, "argv", [
        "run.py", "--config", write_config(tmp_path), "--dry-run",
        "--results", str(tmp_path / "results.jsonl"),
        "--scratch", str(tmp_path / "scratch")])

    runner.main()

    assert inspected == [len(GOOD_SERVING)], (
        f"the gate compared {inspected} fields; zero would mean it never looked")


def test_the_runner_states_how_many_runs_it_gated(tmp_path):
    """Silence is not evidence. The zero-invokers defect was invisible because
    nothing in the runner's output mentioned the gate at all, so the count is
    printed and this test is what keeps it printed."""
    proc = run_runner(write_config(tmp_path), tmp_path)

    assert proc.returncode == 0, proc.stdout
    assert "serving gate" in proc.stdout
    assert "gated=1" in proc.stdout, proc.stdout


# --------------------------------------------------------------------------- #
# Refusal (a): the declared config contradicts the row
# --------------------------------------------------------------------------- #
def test_a_contradicting_serving_config_exits_2_before_the_first_cli_call(tmp_path):
    """PARALLEL=4 is the config the server is actually in today; the row pins 1.
    Under PARALLEL=4 a neighbour's prefill starved a decode to 0.05 tok/s, so
    this is the difference the gate exists to refuse."""
    config = write_config(tmp_path, serving=dict(GOOD_SERVING, parallel=4))

    proc = run_runner(config, tmp_path)

    assert proc.returncode == 2, proc.stdout
    assert "config rejected" in proc.stdout
    assert "parallel" in proc.stdout
    assert "run requests 4" in proc.stdout


def test_a_context_length_below_the_row_is_refused(tmp_path):
    """65536 is the other half of the live mismatch: loaded context was a choice,
    and a row produced at 65536 is not comparable with one produced at 131072."""
    config = write_config(tmp_path, serving=dict(GOOD_SERVING, context_length=65536))

    proc = run_runner(config, tmp_path)

    assert proc.returncode == 2
    assert "context_length" in proc.stdout


def test_a_max_tokens_below_the_rows_floor_is_refused(tmp_path):
    """The floor is 8192 because at a 600-token cap 5/6 reasoning probes returned
    empty content -- a run under the floor measures the cap, not the model."""
    config = write_config(tmp_path, serving=dict(GOOD_SERVING, max_tokens=600))

    proc = run_runner(config, tmp_path)

    assert proc.returncode == 2
    assert "max_tokens_floor" in proc.stdout


def test_a_cap_above_the_floor_is_accepted(tmp_path):
    """The floor is compared by ORDERING, not equality: the safest request a
    caller can make is a cap well above the floor, and refusing it would teach
    callers to send the boundary value."""
    config = write_config(tmp_path, serving=dict(GOOD_SERVING, max_tokens=16384))

    proc = run_runner(config, tmp_path)

    assert proc.returncode == 0, proc.stdout


# --------------------------------------------------------------------------- #
# The driver key: an error, never a default
# --------------------------------------------------------------------------- #
def test_a_gated_run_without_a_driver_key_is_an_error(tmp_path):
    """`.get("driver", "claude-code")` -- the default in PR #10's own docstring
    recipe -- silently files every pi row against the claude-code row. findings.md
    reports pi as a separately-reported vehicle contrast, so that default merges
    two arms the pre-registration forbids merging."""
    config = write_config(tmp_path, driver=None)

    proc = run_runner(config, tmp_path)

    assert proc.returncode == 2, proc.stdout
    assert "declares no driver" in proc.stdout
    assert "NOT defaulted" in proc.stdout, (
        "the refusal must say the driver is not defaulted, so the next reader "
        "does not add the default back as an obvious convenience")
    # And nothing was dispatched under an assumed driver.
    assert "gated=1" not in proc.stdout


def test_an_unknown_driver_is_refused_by_name(tmp_path):
    config = write_config(tmp_path, driver="not-a-driver")

    proc = run_runner(config, tmp_path)

    assert proc.returncode == 2
    assert "not-a-driver" in proc.stdout


def test_the_pi_driver_resolves_to_the_pi_row_not_the_claude_code_row(tmp_path,
                                                                     monkeypatch):
    """The positive control for the test above: `driver: pi` must reach the pi
    row. Otherwise "no default" could be satisfied by refusing pi outright."""
    calls = []
    real = serving_registry.check_dispatch
    monkeypatch.setattr(serving_registry, "check_dispatch",
                        lambda rows, m, d, rs, **kw: (calls.append((m, d)),
                                                      real(rows, m, d, rs, **kw))[1])
    monkeypatch.setattr(sys, "argv", [
        "run.py", "--config", write_config(tmp_path, driver="pi"), "--dry-run",
        "--results", str(tmp_path / "results.jsonl"),
        "--scratch", str(tmp_path / "scratch")])

    runner.main()

    assert calls == [("glm-4.7", "pi")]


# --------------------------------------------------------------------------- #
# Refusal: the gate must not be wirable into a no-op
# --------------------------------------------------------------------------- #
def test_a_gated_run_with_no_serving_block_is_refused(tmp_path):
    """UninspectedConfig. PR #10 added it precisely so a wiring that passes {}
    to make things compile fails loudly instead of passing silently."""
    config = write_config(tmp_path, serving=None)

    proc = run_runner(config, tmp_path)

    assert proc.returncode == 2, proc.stdout
    assert "serving" in proc.stdout
    assert "zero requested serving fields" in proc.stdout or "declare" in proc.stdout


def test_a_non_numeric_floor_request_exits_2_rather_than_traceback(tmp_path):
    """issue #12 (f). `unknown` is this registry's own sentinel, shipped on
    `quant`, so a caller copying a row into a request reaches it without doing
    anything strange. Before PR #10's guard this raised TypeError -- not a
    ValueError -- and walked straight past the runner's handler."""
    config = write_config(tmp_path, serving=dict(GOOD_SERVING, max_tokens="unknown"))

    proc = run_runner(config, tmp_path)

    assert proc.returncode == 2, proc.stdout
    assert "Traceback" not in proc.stdout, proc.stdout
    assert "max_tokens_floor" in proc.stdout
    assert "unknown" in proc.stdout


# --------------------------------------------------------------------------- #
# StructurallyImpossible: dropped, not fatal, and not scored 0
# --------------------------------------------------------------------------- #
def test_an_inexpressible_cell_does_not_abort_the_sweep(tmp_path):
    """issue #12 (c). StructurallyImpossible subclasses RegistryError subclasses
    ValueError, so a naive insertion into the existing `except ValueError` makes
    ONE inexpressible cell exit 2 for the whole matrix. A matrix containing
    pi x L5 is not an invalid config; it is a valid matrix containing cells that
    do not exist."""
    config = write_config(tmp_path, driver="pi", harness_level=5)

    proc = run_runner(config, tmp_path)

    assert proc.returncode == 0, proc.stdout
    assert "config rejected" not in proc.stdout
    assert "structurally" in proc.stdout.lower()


def test_an_inexpressible_cell_is_recorded_rather_than_silently_dropped(tmp_path):
    """A cell that cannot exist must leave a trace: dropping it quietly turns
    'this driver cannot do that' into 'nobody ran it', which are different facts
    and only one of them is reportable."""
    config = write_config(tmp_path, driver="pi", harness_level=5)
    results = tmp_path / "results.jsonl"

    proc = run_runner(config, tmp_path)
    assert proc.returncode == 0, proc.stdout

    rows = [json.loads(line) for line in
            results.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1, f"expected one recorded cell, got {rows}"
    assert rows[0]["exit_reason"] == "structurally_impossible"
    # NEVER False: a 0 asserts the model attempted the task and failed it.
    assert rows[0]["pass"] is None


def test_the_expressible_cells_of_the_same_matrix_still_dispatch(tmp_path):
    """The half that matters: dropping the impossible cell must leave the rest of
    the sweep alone."""
    lines = ["defaults:", "  timeout_t1_t2_s: 1200", "  seed: 1337", "",
             "serving:"]
    for key, value in GOOD_SERVING.items():
        lines.append(f"  {key}: {value}")
    lines += ["", "sweeps:",
              "  - name: possible", "    driver: claude-code",
              "    harness_level: 5", "    harness: false", "    reps: [1]",
              "    tasks: [t2-py-a]", "    configs:",
              "      - {model: glm-4.7-local, effort: high}",
              "  - name: impossible", "    driver: pi",
              "    harness_level: 5", "    harness: false", "    reps: [1]",
              "    tasks: [t2-py-a]", "    configs:",
              "      - {model: glm-4.7-local, effort: high}"]
    config = tmp_path / "mixed.yaml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")

    proc = run_runner(str(config), tmp_path)

    assert proc.returncode == 0, proc.stdout
    assert "possible--" in proc.stdout
    assert "structurally_impossible=1" in proc.stdout, proc.stdout


# --------------------------------------------------------------------------- #
# Back-compat: models with no registry row
# --------------------------------------------------------------------------- #
def test_a_model_with_no_registry_row_is_not_gated_but_is_counted(tmp_path):
    """The serving registry holds glm-4.7 only. A fable run has no row and the
    panel never pinned one, so inventing one would manufacture a measurement.
    It is skipped -- and COUNTED, so 'the gate ran on nothing' can never again be
    indistinguishable from 'the gate ran'."""
    lines = ["defaults:", "  timeout_t1_t2_s: 1200", "  seed: 1337", "",
             "sweeps:", "  - name: legacy", "    harness: false",
             "    reps: [1]", "    tasks: [t2-py-a]", "    configs:",
             "      - {model: fable, effort: high}"]
    config = tmp_path / "legacy.yaml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")

    proc = run_runner(str(config), tmp_path)

    assert proc.returncode == 0, proc.stdout
    assert "gated=0" in proc.stdout
    assert "ungated=1" in proc.stdout


def test_every_shipped_runs_config_still_passes_validation(tmp_path):
    """The regression that would bite hardest: eight runs*.yaml files predate the
    registry and none declares a serving block."""
    inspected = []
    for name in sorted(os.listdir(RUNNER_DIR)):
        if not (name.startswith("runs") and name.endswith(".yaml")):
            continue
        inspected.append(name)
        proc = run_runner(os.path.join(RUNNER_DIR, name), tmp_path)
        assert proc.returncode == 0, f"{name} was rejected:\n{proc.stdout}"
    assert len(inspected) >= 8, f"inspected only {inspected}"


# --------------------------------------------------------------------------- #
# The docstring recipe (issue #12 b, e)
# --------------------------------------------------------------------------- #
def test_the_modules_wiring_recipe_names_only_things_that_exist():
    """PR #10's docstring suggested a four-argument call in which three arguments
    did not exist -- `requested_serving_from()` was illustrative pseudocode that
    read as an existing helper, run dicts carried no `driver` key, and no config
    carried a serving block. A recipe a reader cannot run is worse than none."""
    doc = serving_registry.__doc__
    # The RECIPE is the indented code, not the prose around it. The prose is
    # allowed -- and expected -- to name the rejected default in order to say why
    # it is rejected; what must not survive is a reader copying a line that does
    # not work.
    code = "\n".join(line for line in doc.splitlines()
                     if line.startswith(" " * 8) and line.strip())

    assert "requested_serving_from" not in code, (
        "the pseudocode recipe is still in the docstring's code block")
    assert '.get("driver", "claude-code")' not in code, (
        "the code block still shows the default that merges pi into claude-code")
    assert "check_dispatch" in code, "the code block no longer shows the call"
    # Everything the corrected recipe names must actually exist.
    inspected = []
    for name in ("serving_config_from", "require_driver", "serving_model_name",
                 "models_with_rows"):
        assert name in code, f"the recipe does not name {name}"
        inspected.append(name)
        assert hasattr(runner, name) or hasattr(serving_registry, name), (
            f"the recipe names {name}, which does not exist -- which is the "
            f"exact defect issue #12 (b) recorded about the first draft")
    assert len(inspected) == 4
    # And the prose still records WHY the default is refused, so the next reader
    # does not helpfully add it back.
    assert "vehicle contrast" in doc


@pytest.mark.parametrize("model_id,expected", [
    ("glm-4.7-local", "glm-4.7"),
    ("qwen3-coder-next-local", "qwen3-coder-next"),
    ("claude-sonnet-5", "claude-sonnet-5"),
])
def test_the_row_name_seam_is_explicit(model_id, expected):
    """runner/registry.py calls it `glm-4.7-local`; runner/models.yaml calls the
    same thing `glm-4.7`. That convention was implicit in one test's
    `row["model"] + "-local"`; the gate needs the inverse, so it is a named
    function with its own test rather than a suffix strip buried in main()."""
    assert serving_registry.serving_model_name(model_id) == expected

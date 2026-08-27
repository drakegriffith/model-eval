"""test_registry_validate_gaps.py -- issue #12's second cosmetic item:
`validate` printed OK over a row that satisfies none of the evidence rules.

WHAT WAS OPEN. The panel's auto-assert rule 4 requires a reasoning-token probe
ON ADD. `record_reasoning_probe` exists and is tested, but `new_row` takes no
probe argument, so the rule is only satisfiable in a second, easily-forgotten
call -- and the shipped registry already shows the consequence. The claude-code
row carries the panel's probe (600-token cap, 5/6 empty, 2026-08-25); the pi row
carries `null`. Neither row has a noise probe.

The asymmetry is HONEST -- findings.md never says which driver the probe ran
under, and copying it across would manufacture a measurement. What was wrong is
that nothing marked the difference as a gap rather than a choice:

    $ python3 runner/serving_registry.py validate
    rows inspected: 2
    OK

"OK" over a row nobody has probed is the silence-is-not-evidence failure in one
word. A reader asking "is this registry ready to report against?" was told yes.

WHAT IS ASSERTED HERE. Gaps are named, counted, and separated from
inconsistencies, because they are different things with different fixes:

    problem   the registry contradicts itself or runner/registry.py -> exit 1
    gap       a row is missing evidence a rule requires               -> named,
              counted, and never summarised as a bare "OK"

A gap does not fail the command -- an unprobed row is a truthful record of what
nobody has measured, not a broken one -- but it can no longer hide behind a
one-word pass.
"""
import os
import subprocess
import sys
import types

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import serving_registry as sr  # noqa: E402


def validate(path=None, capsys=None):
    args = types.SimpleNamespace(path=path or sr.REGISTRY_PATH)
    code = sr.cmd_validate(args)
    return code, capsys.readouterr().out


def test_the_shipped_registry_names_its_unprobed_row(capsys):
    """The motivating case, against the real registry rather than a fixture."""
    code, out = validate(capsys=capsys)

    assert "glm-4.7" in out and "pi" in out
    assert "reasoning" in out.lower(), (
        f"validate never mentions the missing reasoning probe:\n{out}")


def test_validate_never_summarises_a_gap_as_a_bare_ok(capsys):
    """The exact string that was wrong. A one-word pass over unmeasured rows is
    what made this a finding rather than a preference."""
    _code, out = validate(capsys=capsys)

    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]

    assert "OK" not in lines, (
        f"validate still prints a bare OK over rows carrying evidence gaps:\n{out}")


def test_the_gap_count_is_stated_not_just_the_inspected_count(capsys):
    """Counting subjects is half of it; a reader also needs to know how many of
    them cleared the bar.

    "2" was the row census when this was written; the subject is "the printed
    inspected-count matches the file's actual row count" and, separately, "the
    printed gap count matches the file's actual gaps" -- both re-derived here
    from the registry rather than hardcoded, so a third row (or a row that
    closes a gap) does not silently detach the pin from what it protects."""
    rows = sr.load_rows()
    expected_gaps = sum(
        (1 if row.get("reasoning_probe") is None else 0)
        + (1 if row.get("noise_probe") is None else 0)
        for row in rows
    )
    _code, out = validate(capsys=capsys)

    assert f"rows inspected: {len(rows)}" in out
    assert f"{expected_gaps} evidence gap(s)" in out, (
        f"expected {expected_gaps} evidence gap(s) stated in:\n{out}")


def test_a_gap_does_not_fail_the_command(capsys):
    """An unprobed row is a truthful record of what nobody measured, not a broken
    one. Failing on it would push the next person to invent a probe value to get
    a green -- which is the manufactured measurement this registry exists to
    prevent."""
    code, _out = validate(capsys=capsys)

    assert code == 0


def test_a_real_inconsistency_still_fails(tmp_path, capsys):
    """The control: separating gaps from problems must not soften problems."""
    bad = tmp_path / "models.yaml"
    bad.write_text(
        "models:\n"
        "  - model: not-a-real-model\n"
        "    driver: claude-code\n"
        "    serving:\n"
        "      parallel: 1\n"
        "      context_length: 131072\n"
        "      max_tokens_floor: 8192\n"
        "      temperature: 0\n"
        "      seed: 42\n"
        "      quant: unknown\n"
        "    capabilities:\n"
        "      subagents: true\n"
        "      hooks: true\n"
        "      tools: unknown\n"
        "    max_harness_level: 5\n"
        "    deterministic_loops: false\n"
        "    noise_probe: null\n"
        "    reasoning_probe: null\n"
        "    permission:\n"
        "      mode: default\n"
        "      authorization: null\n"
        "      authorized_date: null\n"
        "    timeout:\n"
        "      prefill_tok_s_min: 57.0\n"
        "      prefill_tok_s_max: 71.0\n"
        "      basis: \"x\"\n"
        "    notes: \"x\"\n", encoding="utf-8")

    code, out = validate(str(bad), capsys=capsys)

    assert code == 1
    assert "not-a-real-model" in out


def test_an_empty_registry_is_still_unenforced_not_ok(tmp_path, capsys):
    """The other control, and the reason this command prints counts at all: a
    validator that inspected zero rows has not passed."""
    # A path with no file behind it: load_rows treats that as an empty registry
    # rather than crashing, on the argument that the gate's own refusal for an
    # absent row already says the right thing. This asserts the OTHER half --
    # that an empty registry does not then report itself as passing.
    code, out = validate(str(tmp_path / "no-such-registry.yaml"), capsys=capsys)

    assert code == 2
    assert "UNENFORCED" in out


def test_the_cli_reports_the_gaps_too():
    """Through the real entry point, since that is where an operator meets it."""
    proc = subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "serving_registry.py"), "validate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)

    assert proc.returncode == 0, proc.stdout
    assert "reasoning" in proc.stdout.lower()
    assert proc.stdout.strip().splitlines()[-1].strip() != "OK"

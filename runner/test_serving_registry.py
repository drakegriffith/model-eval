"""Interface tests for runner/serving_registry.py (issue #8, stage 1).

The seams under test are the module's four public promises, and nothing below
them:

  1. the on-disk registry file round-trips through the reader/writer pair, and
     the reader REFUSES anything outside the subset it understands rather than
     guessing at it;
  2. `new_row` applies the seven auto-assert rules from the 2026-08-25 panel
     (docs/studio-handoff/findings.md, "Auto-assert rules") -- two of which no
     caller may override;
  3. the pre-dispatch gate refuses all three of its refusal cases;
  4. the shipped rows in runner/models.yaml are the rows the panel measured.

No test reaches into a private helper. The parser is tested through
parse_registry_yaml/dump_registry_yaml and the gate through the check_*
functions, because those are what run.py and the CLI call.

Every loop over the registry asserts how many subjects it inspected, matching
runner/test_registry.py: a loop over an accidentally-empty row list passes while
proving nothing, and that failure is byte-identical to a real pass.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serving_registry as sr  # noqa: E402


# --------------------------------------------------------------------------- #
# Seam 1 -- the registry file reader/writer pair
# --------------------------------------------------------------------------- #
SAMPLE = """\
# a comment line
models:
  - model: glm-4.7
    driver: claude-code
    serving:
      parallel: 1
      context_length: 131072
      temperature: 0
      quant: unknown
    capabilities:
      subagents: true
      hooks: true
      tools: unknown
    deterministic_loops: false
    noise_probe: null
"""


def test_parse_reads_the_subset_the_writer_emits():
    """Scalars keep their types; nesting and list-of-maps survive the trip."""
    doc = sr.parse_registry_yaml(SAMPLE)
    assert list(doc) == ["models"]
    rows = doc["models"]
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "glm-4.7"
    assert row["driver"] == "claude-code"
    assert row["serving"]["parallel"] == 1
    assert row["serving"]["context_length"] == 131072
    assert row["serving"]["temperature"] == 0
    assert row["serving"]["quant"] == "unknown"
    assert row["capabilities"]["subagents"] is True
    assert row["deterministic_loops"] is False
    assert row["noise_probe"] is None


def test_dump_then_parse_is_the_identity_on_a_registry_document():
    """Round trip, because the file's format is defined as what the writer writes."""
    doc = sr.parse_registry_yaml(SAMPLE)
    assert sr.parse_registry_yaml(sr.dump_registry_yaml(doc)) == doc


def test_a_quoted_string_survives_its_punctuation():
    """The bypassPermissions authorization sentence is one long quoted line with
    commas and a colon in it; losing its tail would silently weaken the record."""
    text = 'models:\n  - note: "a sentence: with, punctuation."\n'
    assert sr.parse_registry_yaml(text)["models"][0]["note"] == (
        "a sentence: with, punctuation.")


def test_an_unquoted_value_containing_a_hash_is_refused_not_truncated():
    """The authorization sentence is the worst possible thing to truncate: what
    survives still parses, still looks like a complete authorization, and now
    says something the human did not type. The stripper cannot tell a comment
    from a sentence containing '#', so it refuses instead of guessing."""
    text = 'models:\n  - authorization: I approve run #3 and nothing else\n'
    with pytest.raises(sr.RegistryError) as e:
        sr.parse_registry_yaml(text)
    msg = str(e.value)
    assert "line 2" in msg
    assert "quote" in msg.lower()


def test_a_quoted_value_keeps_its_hash():
    """Quoting is the escape hatch the refusal points at, so it has to work."""
    text = 'models:\n  - authorization: "I approve run #3 and nothing else"\n'
    assert sr.parse_registry_yaml(text)["models"][0]["authorization"] == (
        "I approve run #3 and nothing else")


def test_a_comment_after_a_quoted_value_is_still_a_comment():
    text = 'models:\n  - model: "glm-4.7"  # the row under test\n'
    assert sr.parse_registry_yaml(text)["models"][0]["model"] == "glm-4.7"


def test_a_comment_on_a_key_with_no_value_is_still_a_comment():
    text = 'models:\n  - serving:  # measured 2026-08-25\n      parallel: 1\n'
    assert sr.parse_registry_yaml(text)["models"][0]["serving"]["parallel"] == 1


def test_the_shipped_authorization_is_quoted_in_the_file():
    """Belt and braces: the refusal above protects future hand edits, and this
    protects the record that exists now. An unquoted authorization is one typed
    '#' away from being silently rewritten."""
    with open(sr.REGISTRY_PATH, "r", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip().startswith("authorization:")]
    assert len(lines) == 2, f"expected 2 authorization lines, found {len(lines)}"
    quoted = [ln for ln in lines
              if ln.split(":", 1)[1].strip() in ("null",)
              or ln.split(":", 1)[1].strip().startswith('"')]
    assert len(quoted) == 2, f"unquoted authorization value in models.yaml: {lines}"


@pytest.mark.parametrize("bad,why", [
    ("models:\n\t- model: x\n", "tab indentation"),
    ("models:\n  - model: [a, b]\n", "flow sequence"),
    ("models:\n  - model: {a: b}\n", "flow mapping"),
    ("models:\n  - model: |\n      block\n", "block scalar"),
    ("models:\n  - model: &anchor x\n", "anchor"),
    ("models:\n  bare line without a colon\n", "no key"),
])
def test_parse_refuses_constructs_outside_its_subset(bad, why):
    """A subset parser that GUESSES at unsupported YAML is worse than no parser:
    the misreading is silent and the row still looks plausible. Refuse loudly,
    naming the line, so a hand edit that strays cannot be mistaken for a pass."""
    with pytest.raises(sr.RegistryError) as e:
        sr.parse_registry_yaml(bad)
    assert "line" in str(e.value), f"{why}: error must name the line number"


# --------------------------------------------------------------------------- #
# Seam 2 -- the seven auto-asserts applied when a model is added
# --------------------------------------------------------------------------- #
FULL_SERVING = {"parallel": 1, "context_length": 131072, "max_tokens_floor": 8192,
                "temperature": 0, "seed": 42, "quant": "unknown"}


def test_new_row_asserts_deterministic_loops_false():
    """Auto-assert 1. llama.cpp batch physics: temperature 0 + seed 42 gave 2/3
    distinct sequential outputs on this stack. Never optional at add time."""
    row = sr.new_row("glm-4.7", "claude-code", FULL_SERVING, prefill_tok_s=57)
    assert row["deterministic_loops"] is False


def test_new_row_refuses_a_caller_that_asserts_determinism_by_hand():
    """The one override the panel forbade: determinism flips to true only from a
    recorded 5/5-identical sequential probe on THIS serving config, which is
    record_noise_probe's job, not a keyword argument's."""
    with pytest.raises(sr.RegistryError):
        sr.new_row("glm-4.7", "claude-code", FULL_SERVING, prefill_tok_s=57,
                   deterministic_loops=True)


def test_new_row_stamps_the_pi_capability_manifest():
    """Auto-assert 2, the second never-optional one: pi has no hooks and no
    subagents, so those are facts of the driver, not choices of the adder."""
    row = sr.new_row("glm-4.7", "pi", FULL_SERVING, prefill_tok_s=57)
    assert row["capabilities"]["subagents"] is False
    assert row["capabilities"]["hooks"] is False
    assert row["capabilities"]["tools"] == 7


def test_new_row_refuses_a_caller_that_grants_pi_a_capability_it_lacks():
    with pytest.raises(sr.RegistryError):
        sr.new_row("glm-4.7", "pi", FULL_SERVING, prefill_tok_s=57,
                   capabilities={"hooks": True})


def test_new_row_requires_every_pinned_serving_field():
    """Auto-assert 3. A row missing a serving field cannot be compared against
    any other row, so an incomplete row is refused at the door rather than
    written and discovered at analysis time."""
    inspected = []
    for field in sr.SERVING_FIELDS:
        partial = {k: v for k, v in FULL_SERVING.items() if k != field}
        with pytest.raises(sr.RegistryError) as e:
            sr.new_row("glm-4.7", "claude-code", partial, prefill_tok_s=57)
        assert field in str(e.value)
        inspected.append(field)
    assert len(inspected) == 6, f"expected 6 pinned serving fields, saw {inspected}"


def test_new_row_leaves_the_noise_probe_absent():
    """Auto-assert 5. Absent is the honest default -- and absent is what makes
    the row refuse cross-model comparison until someone runs the probe."""
    row = sr.new_row("glm-4.7", "claude-code", FULL_SERVING, prefill_tok_s=57)
    assert row["noise_probe"] is None


def test_bypass_permissions_requires_its_authorizing_sentence_and_date():
    """Auto-assert 6. A bypassPermissions row without the typed authorization is
    a claim that a human approved something, with nothing behind it."""
    with pytest.raises(sr.RegistryError) as e:
        sr.new_row("glm-4.7", "claude-code", FULL_SERVING, prefill_tok_s=57,
                   permission_mode="bypassPermissions")
    assert "authoriz" in str(e.value).lower()

    row = sr.new_row("glm-4.7", "claude-code", FULL_SERVING, prefill_tok_s=57,
                     permission_mode="bypassPermissions",
                     permission_authorization="Drake typed this on 2026-08-25.",
                     permission_authorized_date="2026-08-25")
    assert row["permission"]["mode"] == "bypassPermissions"
    assert row["permission"]["authorized_date"] == "2026-08-25"


def test_new_row_requires_a_measured_prefill_rate():
    """Auto-assert 7. The turn cap is derived from measured prefill x prompt
    size; a row with no measured rate has no honest timeout basis at all."""
    with pytest.raises(sr.RegistryError):
        sr.new_row("glm-4.7", "claude-code", FULL_SERVING)


def test_turn_cap_is_derived_from_the_slowest_measured_prefill():
    """Rule 7 stores a basis, not a constant: prompt size varies per arm, so the
    cap is a function of it. 61440 tokens at the slow end of the measured
    57-71 tok/s band is ~1078 s of prefill, which is the 1077 s the panel
    actually clocked for a 61k prefill -- an independent check on the formula."""
    row = sr.new_row("glm-4.7", "claude-code", FULL_SERVING, prefill_tok_s=57)
    cap = sr.derive_turn_cap_s(row, prompt_tokens=61440, safety_factor=1.0)
    assert 1000 < cap < 1150


# --------------------------------------------------------------------------- #
# Seam 3 -- the pre-dispatch gate, all three refusal cases
# --------------------------------------------------------------------------- #
def _row(driver="claude-code", **over):
    kw = {"prefill_tok_s": 57}
    kw.update(over)
    return sr.new_row("glm-4.7", driver, dict(FULL_SERVING), **kw)


def test_refusal_a_requested_config_differs_from_the_registry_row():
    """(a) The live server is PARALLEL=4/context 65536 today; the row pins
    PARALLEL=1/context 131072. Dispatching anyway would label rows with a
    serving config they were not produced under."""
    row = _row()
    with pytest.raises(sr.RegistryError) as e:
        sr.check_run_config(row, {"parallel": 4})
    msg = str(e.value)
    assert "parallel" in msg and "4" in msg and "1" in msg


def test_refusal_a_passes_when_the_request_matches_the_row():
    """The positive control for (a): a gate that refuses everything proves
    nothing about the case it is supposed to catch."""
    sr.check_run_config(_row(), dict(FULL_SERVING))


@pytest.mark.parametrize("requested", [{}, None])
def test_refusal_a_refuses_a_request_that_asserts_nothing(requested):
    """A gate that inspected zero fields did not pass -- it failed to run.

    The original version returned quietly here, so `check_dispatch(rows, model,
    driver, {})` was a green light that had compared nothing. That is the exact
    shape of the silent pass this gate exists to prevent: the caller believes the
    serving config was checked, and the row gets labelled with a config nobody
    confirmed.
    """
    with pytest.raises(sr.UninspectedConfig) as e:
        sr.check_run_config(_row(), requested)
    assert "zero" in str(e.value)


def test_uninspected_config_is_its_own_type_under_registry_error():
    """Distinct type so a caller can tell "your config is wrong" from "you did
    not give me a config", which have different fixes."""
    assert issubclass(sr.UninspectedConfig, sr.RegistryError)


def test_check_dispatch_refuses_an_empty_requested_config():
    """Same hole, reached through the entry point the runner actually calls."""
    with pytest.raises(sr.UninspectedConfig):
        sr.check_dispatch(sr.load_rows(), "glm-4.7", "claude-code", {})


def test_check_run_config_reports_how_many_fields_it_inspected():
    """The positive control for the control: a passing check says what it looked
    at, so "passed" and "looked at nothing" cannot read the same."""
    assert sr.check_run_config(_row(), {"parallel": 1}) == 1
    assert sr.check_run_config(_row(), dict(FULL_SERVING)) == 6


@pytest.mark.parametrize("key", ["max_tokens", "max_tokens_floor"])
def test_a_request_above_the_floor_is_not_a_mismatch(key):
    """A floor is a minimum, not an equality. 8192 is the value below which GLM
    returns empty content (5/6 probes empty at a 600-token cap); a run asking for
    16384 is further from that failure, not in conflict with the row. Comparing
    it with == refused the safest possible request."""
    assert sr.check_run_config(_row(), {key: 16384}) == 1
    assert sr.check_run_config(_row(), {key: 8192}) == 1


@pytest.mark.parametrize("key", ["max_tokens", "max_tokens_floor"])
def test_a_request_below_the_floor_is_refused(key):
    """600 is the cap the panel measured returning empty content. The refusal
    names the floor and the reason, because the fix is to raise the cap."""
    with pytest.raises(sr.RegistryError) as e:
        sr.check_run_config(_row(), {key: 600})
    msg = str(e.value)
    assert "600" in msg and "8192" in msg
    assert "floor" in msg.lower()


def test_the_floor_is_still_an_equality_between_rows():
    """Floor semantics belong to (a), not to (b). Two rows that disagree on the
    floor were produced under different serving configs and are not comparable,
    whichever floor is higher."""
    a, b = _row(), _row()
    for r in (a, b):
        r["serving"]["quant"] = "Q4_K_M"
        sr.record_noise_probe(r, flip_rate=0.0, date="2026-08-25", identical=5, of=5)
    b["serving"]["max_tokens_floor"] = 16384
    with pytest.raises(sr.RegistryError) as e:
        sr.check_comparable(a, b)
    assert "max_tokens_floor" in str(e.value)


def test_refusal_b_cross_model_comparison_across_different_serving_configs():
    """(b) Auto-assert 3: comparisons are valid only between rows with identical
    serving config."""
    a = _row()
    b = _row()
    b["serving"]["context_length"] = 65536
    with pytest.raises(sr.RegistryError) as e:
        sr.check_comparable(a, b)
    assert "context_length" in str(e.value)


def test_refusal_b_also_refuses_rows_that_never_ran_a_noise_probe():
    """Auto-assert 5 has teeth only here. Two rows with identical serving config
    and no measured noise floor produce a delta nobody can size."""
    a, b = _row(), _row()
    assert a["noise_probe"] is None
    with pytest.raises(sr.RegistryError) as e:
        sr.check_comparable(a, b)
    assert "noise" in str(e.value).lower()


def test_refusal_b_refuses_an_unknown_serving_field_rather_than_matching_it():
    """`unknown` == `unknown` is not equality, it is two absences. Treating it as
    a match is the "could not determine" result wearing a pass."""
    a, b = _row(), _row()
    for r in (a, b):
        sr.record_noise_probe(r, flip_rate=0.0, date="2026-08-25", identical=5, of=5)
    assert a["serving"]["quant"] == "unknown"
    with pytest.raises(sr.RegistryError) as e:
        sr.check_comparable(a, b)
    assert "quant" in str(e.value)


def test_refusal_b_passes_for_two_fully_pinned_probed_rows():
    """Positive control for (b)."""
    a, b = _row(), _row()
    for r in (a, b):
        r["serving"]["quant"] = "Q4_K_M"
        sr.record_noise_probe(r, flip_rate=0.0, date="2026-08-25", identical=5, of=5)
    sr.check_comparable(a, b)


def test_refusal_c_a_cell_the_driver_cannot_express():
    """(c) pi has no hooks and no subagents. The refusal carries its own
    exception type so a caller cannot fold it into the fail bucket: a cell that
    does not exist is not a cell the model lost."""
    row = _row(driver="pi")
    with pytest.raises(sr.StructurallyImpossible) as e:
        sr.check_cell_expressible(row, capability="hooks")
    assert "structurally-impossible" in str(e.value)


def test_refusal_c_covers_harness_levels_above_the_driver_ceiling():
    row = _row(driver="pi")
    with pytest.raises(sr.StructurallyImpossible):
        sr.check_cell_expressible(row, harness_level=4)


def test_refusal_c_passes_for_a_cell_the_driver_does_express():
    """Positive control for (c): pi does take --skill and --prompt-template, so
    the low harness levels are real cells and must not be refused."""
    sr.check_cell_expressible(_row(driver="pi"), harness_level=2)
    sr.check_cell_expressible(_row(), capability="hooks")


def test_structurally_impossible_is_never_a_score_of_zero():
    """The type is the contract: StructurallyImpossible is a ValueError so that
    fail-closed callers still stop, but it is its OWN type so a caller that
    scores cells can branch on it instead of writing a 0."""
    assert issubclass(sr.StructurallyImpossible, sr.RegistryError)
    assert issubclass(sr.RegistryError, ValueError)


def test_check_dispatch_is_the_single_entry_point_run_py_calls():
    """The gate run.py wires in is one call, not three: a wiring that has to
    remember to call three checks is a wiring that will call two."""
    rows = sr.load_rows()
    with pytest.raises(sr.RegistryError):
        sr.check_dispatch(rows, "glm-4.7", "claude-code", {"parallel": 4})
    sr.check_dispatch(rows, "glm-4.7", "claude-code", {"parallel": 1})


def test_check_dispatch_refuses_a_pair_that_is_not_in_the_registry():
    """Fail closed on an unknown (model, driver), for registry.py's own reason:
    a mis-resolved row silently changes what the run is labelled with."""
    with pytest.raises(sr.RegistryError):
        sr.check_dispatch(sr.load_rows(), "glm-4.7", "no-such-driver", {})


# --------------------------------------------------------------------------- #
# Seam 4 -- the shipped rows are the rows the panel measured
# --------------------------------------------------------------------------- #
def test_the_registry_ships_both_glm_rows():
    rows = sr.load_rows()
    keys = sorted(sr.row_key(r) for r in rows)
    assert keys == [("glm-4.7", "claude-code"), ("glm-4.7", "pi")], keys


def test_every_shipped_row_names_a_model_the_model_registry_knows():
    """The anti-duplication bind. runner/registry.py stays the single roster;
    this file adds a per-driver serving LAYER over it. If the two ever drift,
    this test is what says so."""
    import registry
    inspected = []
    for row in sr.load_rows():
        registry.resolve_model(row["model"] + "-local")
        inspected.append(row["model"])
    assert len(inspected) == 2, f"expected 2 rows, inspected {inspected}"


def test_the_shipped_rows_carry_the_panels_measured_serving_config():
    rows = sr.load_rows()
    inspected = []
    for row in rows:
        s = row["serving"]
        assert s["parallel"] == 1
        assert s["context_length"] == 131072
        assert s["max_tokens_floor"] == 8192
        assert s["temperature"] == 0
        assert s["seed"] == 42
        assert row["deterministic_loops"] is False
        assert row["timeout"]["prefill_tok_s_min"] == 57
        assert row["timeout"]["prefill_tok_s_max"] == 71
        inspected.append(sr.row_key(row))
    assert len(inspected) == 2, f"expected 2 rows, inspected {inspected}"


def test_the_claude_code_row_carries_the_typed_authorization():
    row = sr.find_row(sr.load_rows(), "glm-4.7", "claude-code")
    assert row["permission"]["mode"] == "bypassPermissions"
    assert row["permission"]["authorized_date"] == "2026-08-25"
    assert "GLM" in row["permission"]["authorization"]


def test_the_pi_row_records_its_structural_ceiling():
    row = sr.find_row(sr.load_rows(), "glm-4.7", "pi")
    assert row["capabilities"]["subagents"] is False
    assert row["capabilities"]["hooks"] is False
    assert row["max_harness_level"] == 2

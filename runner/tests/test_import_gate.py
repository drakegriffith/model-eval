"""test_import_gate.py -- proves runner/import_gate.py detects things (ticket 33).

The gate ratchets a property the repo already holds, so its real-tree run is
expected to pass and that pass proves almost nothing. What these tests are for is
the opposite: a control arm that must FAIL, in each direction the gate claims to
cover. Per the conclusive-ab-fixture rule the control is written and asserted
first -- a clean temp tree must pass, or every subsequent failure could be the
fixture rather than the gate.

Every negative control is injected into a tmp_path tree. The real runner/ tree is
never mutated.
"""
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import import_gate  # noqa: E402

# --------------------------------------------------------------------------- #
# THE DECISION RULE -- COPY 2 OF 2
# --------------------------------------------------------------------------- #
# DO NOT DRY THIS. This tuple is duplicated from runner/import_gate.py on
# purpose, and the duplication IS the control (harness #5: the checker must not
# read its decision rule from the artifact it is checking). The gate's own copy
# is editable by any worker touching the core; this copy is what turns "delete
# stats.py from the list so my new dependency passes" into a red test. A tidy-up
# pass that replaces this literal with `import_gate.CORE_MODULES` deletes the
# control and leaves the suite green -- reject that pass; this comment is the
# standing reason to reject it.
#
# Pricing is deliberately absent: it is not yet a module (runner/tables.py:29-30
# are placeholder prices). Adding it later is a deliberate edit here, in the
# gate, and in the new module's own declaration.
#
# corpus_gates.py joined 2026-07-30 (ticket 31 AC#3) because stats.py imports it
# and the core may only import the stdlib and the core. Restated here rather than
# read from the gate, same as every other name on this line.
#
# usage_ledger.py joined 2026-07-30 (ticket 37), once its paths stopped being
# derived from its own location on disk. Typed out here by hand, not copied from
# the gate: this copy is only worth having if it was written independently.
#
# spend_cap.py joined 2026-07-30 (ticket 40). The three-place cost was paid
# deliberately: the module's own declaration, the gate's literal, and this line.
# A cap that could quietly leave the core is a cap the product could quietly
# stop passing through.
EXPECTED_CORE_MODULES = ("corpus_gates.py", "effort_verdict.py", "registry.py",
                         "spend_cap.py", "stats.py", "token_units.py",
                         "usage_ledger.py")


def write_core_tree(root, modules, extra=None):
    """A stand-in runner/ directory: each named module declares itself core and
    imports only the stdlib. `extra` adds files verbatim."""
    root.mkdir(parents=True, exist_ok=True)
    for name, body in modules.items():
        (root / name).write_text(body, encoding="utf-8")
    for name, body in (extra or {}).items():
        (root / name).write_text(body, encoding="utf-8")
    return str(root)


CLEAN_CORE = "CORE_MODULE = True\nimport json\nimport os\n"


@pytest.fixture
def clean_tree(tmp_path):
    return write_core_tree(tmp_path / "runner", {name: CLEAN_CORE
                                                 for name in EXPECTED_CORE_MODULES})


# --------------------------------------------------------------------------- #
# Control arm: the fixture must be capable of passing.
# --------------------------------------------------------------------------- #

def test_control_arm_clean_temp_tree_passes(clean_tree):
    """Written first. If a clean stand-in tree does not pass, every negative
    control below proves nothing about the gate."""
    d = import_gate.check_core_is_stdlib_only(clean_tree, EXPECTED_CORE_MODULES)
    assert d["violations"] == []
    assert d["status"] == import_gate.PASS
    assert d["subjects"] == sorted(EXPECTED_CORE_MODULES)


# --------------------------------------------------------------------------- #
# Harness #5: the two copies of the rule must agree.
# --------------------------------------------------------------------------- #

def test_gates_literal_matches_this_tests_independent_copy():
    """The point of the duplication. A worker who edits the gate's core list to
    make their own module stop being checked fails here."""
    assert tuple(import_gate.CORE_MODULES) == EXPECTED_CORE_MODULES


def test_pricing_is_not_in_the_core_list():
    """Absent by fact, not oversight -- pricing is not yet a module. Locked so
    that adding it is a deliberate three-place edit rather than a drift."""
    assert not any("pricing" in name for name in import_gate.CORE_MODULES)


# --------------------------------------------------------------------------- #
# Negative controls, half A: core -> stdlib only.
# --------------------------------------------------------------------------- #

def test_fails_when_a_core_module_gains_a_third_party_import(tmp_path):
    root = write_core_tree(tmp_path / "runner",
                           {name: CLEAN_CORE for name in EXPECTED_CORE_MODULES})
    (tmp_path / "runner" / "stats.py").write_text(
        "CORE_MODULE = True\nimport json\nimport requests\n", encoding="utf-8")
    d = import_gate.check_core_is_stdlib_only(root, EXPECTED_CORE_MODULES)
    assert d["status"] == import_gate.FAIL
    assert any("requests" in v and "NON-STDLIB" in v for v in d["violations"]), d["violations"]


def test_fails_when_a_core_module_is_dropped_from_the_literal(clean_tree):
    """The harness #5 attack, run end to end: the module still declares itself
    core on disk, so shrinking the literal cannot hide it."""
    shrunk = tuple(n for n in EXPECTED_CORE_MODULES if n != "stats.py")
    d = import_gate.check_core_is_stdlib_only(clean_tree, shrunk)
    assert d["status"] == import_gate.FAIL
    assert any("stats.py" in v and "ABSENT FROM THE LITERAL" in v
               for v in d["violations"]), d["violations"]
    assert "BROKEN" in d["note"]


def test_fails_when_the_literal_names_a_module_missing_from_disk(tmp_path):
    """The other direction of the set equality, with its own distinct message."""
    present = {n: CLEAN_CORE for n in EXPECTED_CORE_MODULES if n != "stats.py"}
    root = write_core_tree(tmp_path / "runner", present)
    d = import_gate.check_core_is_stdlib_only(root, EXPECTED_CORE_MODULES)
    assert d["status"] == import_gate.FAIL
    assert any("stats.py" in v and "MISSING FROM DISK" in v
               for v in d["violations"]), d["violations"]


def test_fails_when_a_core_module_stops_declaring_itself(clean_tree):
    """Deleting the declaration is not a way to leave the core quietly. Distinct
    from MISSING FROM DISK: the file is there, it just stopped being checkable."""
    path = os.path.join(clean_tree, "stats.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write("import json\n")
    d = import_gate.check_core_is_stdlib_only(clean_tree, EXPECTED_CORE_MODULES)
    assert d["status"] == import_gate.FAIL
    assert any("stats.py" in v and "does not declare" in v
               for v in d["violations"]), d["violations"]


def test_fails_when_a_core_module_imports_a_non_core_instrument_module(tmp_path):
    """The one-way rule, not just the stdlib rule: core may not reach back into
    the instrument even though run.py is not third-party."""
    root = write_core_tree(
        tmp_path / "runner",
        {name: CLEAN_CORE for name in EXPECTED_CORE_MODULES},
        extra={"run.py": "x = 1\n"})
    (tmp_path / "runner" / "stats.py").write_text(
        "CORE_MODULE = True\nimport run\n", encoding="utf-8")
    d = import_gate.check_core_is_stdlib_only(root, EXPECTED_CORE_MODULES)
    assert d["status"] == import_gate.FAIL
    assert any("NON-CORE module of this instrument" in v
               for v in d["violations"]), d["violations"]


def test_a_core_module_may_import_another_core_module(clean_tree):
    """Intra-core edges are legal; the rule is about leaving the core."""
    with open(os.path.join(clean_tree, "stats.py"), "w", encoding="utf-8") as f:
        f.write("CORE_MODULE = True\nimport registry\n")
    d = import_gate.check_core_is_stdlib_only(clean_tree, EXPECTED_CORE_MODULES)
    assert d["status"] == import_gate.PASS, d["violations"]


def test_declaration_must_be_module_level_and_literally_true(tmp_path):
    """A declaration a reader cannot verify by eye does not count."""
    cases = {
        "nested.py": "def f():\n    CORE_MODULE = True\n",
        "computed.py": "CORE_MODULE = bool(1)\n",
        "false.py": "CORE_MODULE = False\n",
    }
    root = write_core_tree(tmp_path / "runner", {}, extra=cases)
    assert import_gate.declared_core_files(root) == set()


# --------------------------------------------------------------------------- #
# Half B: product -> core only. Two subjects since ticket 38.
# --------------------------------------------------------------------------- #

def test_product_direction_is_enforced_over_counted_subjects_on_the_real_tree():
    """AC#3 of ticket 33 asserted the opposite of this: product/ did not exist,
    so the direction had nothing to inspect and had to report UNENFORCED rather
    than a pass over an empty set. Ticket 38 created product/, and this
    assertion is rephrased to the new fact rather than relaxed -- the property
    pinned is the same one, that the status follows the subject count. Deleting
    product/ returns the status to UNENFORCED silently; this test is what makes
    that a red run instead."""
    d = import_gate.check_product_depends_on_core_only()
    assert os.path.isdir(import_gate.PRODUCT_DIR)
    assert d["subjects"], "product/ exists but the gate found no .py file in it"
    assert d["status"] == import_gate.PASS, d["violations"]
    assert d["status"] != import_gate.UNENFORCED


def test_an_empty_subject_set_can_never_be_a_pass():
    """Structural, at the single point where status is decided."""
    assert import_gate.verdict([], []) == import_gate.UNENFORCED
    assert import_gate.verdict(["a.py"], []) == import_gate.PASS
    assert import_gate.verdict([], ["boom"]) == import_gate.FAIL


def test_product_direction_enforces_once_a_product_exists(tmp_path):
    """The gate must keep working when the directory appears -- both the pass and
    the fail, so the UNENFORCED report is a statement about subjects and not a
    stub. Third-party imports in the product are legal; the instrument is not."""
    runner = write_core_tree(tmp_path / "runner",
                             {n: CLEAN_CORE for n in EXPECTED_CORE_MODULES},
                             extra={"run.py": "x = 1\n"})
    product = tmp_path / "product"
    product.mkdir()
    (product / "app.py").write_text("import requests\nimport stats\n", encoding="utf-8")
    ok = import_gate.check_product_depends_on_core_only(
        str(product), runner, EXPECTED_CORE_MODULES)
    assert ok["status"] == import_gate.PASS, ok["violations"]
    assert ok["subjects"] == ["app.py"]

    (product / "app.py").write_text("import run\n", encoding="utf-8")
    bad = import_gate.check_product_depends_on_core_only(
        str(product), runner, EXPECTED_CORE_MODULES)
    assert bad["status"] == import_gate.FAIL
    assert any("may depend on the core only" in v
               for v in bad["violations"]), bad["violations"]


# --------------------------------------------------------------------------- #
# The real tree, and what the gate is required to say out loud.
# --------------------------------------------------------------------------- #

def test_real_tree_holds_the_core_direction():
    d = import_gate.check_core_is_stdlib_only()
    assert d["violations"] == [], d["violations"]
    assert d["status"] == import_gate.PASS
    assert d["subjects"] == sorted(EXPECTED_CORE_MODULES)
    assert "HOLDS" in d["note"]


def test_report_prints_the_count_and_every_inspected_name():
    """AC#2. A count alone cannot distinguish inspecting all subjects from
    inspecting one of them, so the names are part of the output -- now asserted
    for BOTH directions, since ticket 38 gave the product half subjects. The
    `0 files inspected` line was the expected output here until that ticket; it
    is now the regression this test forbids."""
    directions = import_gate.run_gate()
    text = import_gate.report(directions)
    assert f"{len(EXPECTED_CORE_MODULES)} files inspected" in text
    for name in EXPECTED_CORE_MODULES:
        assert name in text

    product = [d for d in directions if d["name"] == "product -> core only"]
    assert len(product) == 1
    subjects = product[0]["subjects"]
    assert len(subjects) >= 1
    assert f"{len(subjects)} files inspected" in text
    for name in subjects:
        assert name in text
    assert "product -> core only: PASS" in text
    assert "0 files inspected" not in text


def test_summary_cannot_print_a_bare_pass_while_anything_is_unenforced():
    """The property this test has always pinned: an unenforced direction is
    named in the same breath as the pass and never absorbed into a bare PASS.
    Until ticket 38 the real tree supplied the unenforced direction itself; with
    both directions now carrying subjects, the property is exercised on a
    constructed third direction. Rephrased, not dropped -- the summary must
    still behave this way the next time a direction is added before it has
    anything to inspect."""
    unenforced = {"name": "a future direction", "status": import_gate.UNENFORCED,
                  "subjects": [], "violations": [], "note": ""}
    line = import_gate.summary(import_gate.run_gate() + [unenforced])
    assert line != import_gate.PASS
    assert not line.startswith("PASS (all")
    assert "UNENFORCED" in line
    assert "a future direction" in line


def test_summary_reports_the_real_tree_as_fully_enforced():
    """The other half of what ticket 38 changed. With product/ on disk no
    direction is unenforced, and the summary states the count it is claiming
    over rather than a bare PASS."""
    line = import_gate.summary(import_gate.run_gate())
    assert line == "PASS (all 2 directions enforced)"
    assert import_gate.UNENFORCED not in line


def test_report_refuses_to_overclaim():
    """AC#7 and AC#6: the output states its scope, names the portability defects
    a green run does not clear, and says why pricing is absent.

    Ticket 37 repaired one of the two F3 sites, so this assertion is rephrased to
    the new fact rather than relaxed: usage_ledger must now be ABSENT from the
    known-broken list and the ladder must still be present. Deleting the whole
    list would also have satisfied the old test's spirit while making the report
    claim a repair that never happened -- pinning both directions is what stops
    that. The repair itself is pinned by test_usage_ledger_portability.py.
    """
    text = import_gate.report(import_gate.run_gate())
    assert "import statements only" in text
    assert "NOT evidence" in text
    assert "ladder_from_results.py:40-41" in text
    assert "usage_ledger.py:30-34" not in text
    assert not any("usage_ledger" in site
                   for site in import_gate.KNOWN_BROKEN_PORTABILITY)
    assert len(import_gate.KNOWN_BROKEN_PORTABILITY) == 1
    assert "pricing is absent" in text
    assert "tables.py:29-30" in text


def test_main_exits_zero_on_the_real_tree(capsys):
    assert import_gate.main([]) == 0
    assert "GATE:" in capsys.readouterr().out

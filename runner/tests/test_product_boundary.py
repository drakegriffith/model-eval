"""test_product_boundary.py -- proves the product half of the one-way dependency
rule holds over real, counted subjects (ticket 38, slice S7).

Division of labour with test_import_gate.py: that file proves the GATE can
detect things, in temporary trees. This file is about the REAL product directory
-- that it exists, that the gate's subjects are the files actually on disk, that
those files reach the core the way the gate can see, and that the entry point
runs from a working directory that is not the repo root.

Every assertion is a positive one with a count attached. The failure this slice
exists to close is a scan that inspected nothing and printed like a scan that
inspected everything, so "no violations" is never the whole of an assertion here;
"no violations over N>=1 files, and N is the number of files on disk" is.

The real tree is never mutated. The negative control copies product/ into
tmp_path and injects the illegal import there.
"""
import ast
import os
import shutil
import subprocess
import sys

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")

sys.path.insert(0, RUNNER_DIR)
import import_gate  # noqa: E402
import registry  # noqa: E402

PRODUCT_PACKAGE = "gauntlet_playground"


def product_py_files():
    """Every .py file under product/, found by this test's own walk rather than
    by asking the gate. The gate's subject list is checked AGAINST this."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(PRODUCT_DIR):
        for name in sorted(filenames):
            if name.endswith(".py"):
                found.append(os.path.relpath(os.path.join(dirpath, name), PRODUCT_DIR))
    return sorted(found)


def parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def core_stems():
    """The core, read from the on-disk `CORE_MODULE = True` declarations rather
    than from either copy of the literal list. Both literals are pinned against
    each other in test_import_gate.py; this file deliberately depends on neither,
    so a worker who edits both still cannot make this test agree with them."""
    return {name[:-3] for name in import_gate.declared_core_files(RUNNER_DIR)}


def non_core_stems():
    """Instrument modules the product may not import, computed here rather than
    read back from the gate."""
    return {n[:-3] for n in os.listdir(RUNNER_DIR) if n.endswith(".py")} - core_stems()


# --------------------------------------------------------------------------- #
# The directory, the manifest, and the subject count.
# --------------------------------------------------------------------------- #

def test_product_directory_exists_with_its_own_manifest():
    """AC#1's first half. The manifest is what makes product/ a unit rather than
    a folder that happens to sit next to the instrument."""
    assert os.path.isdir(PRODUCT_DIR)
    assert os.path.isfile(os.path.join(PRODUCT_DIR, "pyproject.toml"))
    assert os.path.isfile(os.path.join(PRODUCT_DIR, PRODUCT_PACKAGE, "__init__.py"))
    assert os.path.isfile(os.path.join(PRODUCT_DIR, PRODUCT_PACKAGE, "__main__.py"))


def test_the_gate_inspects_every_product_file_on_disk():
    """AC#2. Not "the gate reported PASS" -- the gate reported PASS over exactly
    the files an independent walk finds. A gate that silently skipped one would
    still print a pass and a plausible count."""
    on_disk = product_py_files()
    assert len(on_disk) >= 1
    d = import_gate.check_product_depends_on_core_only()
    assert d["subjects"] == on_disk
    assert d["status"] == import_gate.PASS, d["violations"]


def test_the_gates_own_run_reports_the_product_direction_as_passing():
    """The direction as `main()` sees it, since that is what a human runs."""
    directions = import_gate.run_gate()
    product = [d for d in directions if d["name"] == "product -> core only"]
    assert len(product) == 1
    assert product[0]["status"] == import_gate.PASS, product[0]["violations"]
    assert len(product[0]["subjects"]) == len(product_py_files())


# --------------------------------------------------------------------------- #
# What the product reaches, asserted in both directions.
# --------------------------------------------------------------------------- #

def test_the_product_actually_imports_the_core():
    """AC#1's second half, and the assertion the gate cannot make. Half B passes
    over a product that imports nothing at all, which is exactly as informative
    as passing over no files. This pins the positive fact: at least one core
    module is reached, by bare name, from product code."""
    reached = set()
    scanned = 0
    for rel in product_py_files():
        scanned += 1
        reached |= import_gate.top_level_imports(parse(os.path.join(PRODUCT_DIR, rel)))
    assert scanned >= 1
    assert reached & core_stems(), (
        f"no product file imports a core module; imports found: {sorted(reached)}")


def test_no_product_file_imports_a_non_core_instrument_module():
    """The rule itself, recomputed here instead of read back from the gate."""
    forbidden = non_core_stems()
    assert forbidden, "no non-core instrument modules found -- the rule has no teeth"
    for rel in product_py_files():
        imports = import_gate.top_level_imports(parse(os.path.join(PRODUCT_DIR, rel)))
        assert not (imports & forbidden), f"{rel} imports {sorted(imports & forbidden)}"


def test_the_product_imports_the_core_by_bare_name_not_through_runner():
    """Why this matters enough to test: the gate reasons in top-level import
    names, and `runner` is not a module of the instrument. An illegal edge
    written as `from runner import run` would reach the gate as the name
    `runner` and pass unnoticed. Product code that imports bare names is product
    code the gate can actually see."""
    for rel in product_py_files():
        imports = import_gate.top_level_imports(parse(os.path.join(PRODUCT_DIR, rel)))
        assert "runner" not in imports, f"{rel} imports the instrument as a package"


def test_no_product_file_manipulates_sys_path():
    """AC#1. sys.path manipulation inside the product is the F3 defect pattern
    ladder_from_results.py:40-41 already carries once; this keeps it at once.
    Counted, because a scan of zero files raises no complaint either."""
    scanned = 0
    for rel in product_py_files():
        scanned += 1
        for node in ast.walk(parse(os.path.join(PRODUCT_DIR, rel))):
            if isinstance(node, ast.Attribute) and node.attr == "path":
                assert not (isinstance(node.value, ast.Name)
                            and node.value.id == "sys"), f"{rel} touches sys.path"
    assert scanned >= 1


def test_no_core_module_imports_the_product():
    """AC#6. The dependency is one-way and the direction is asserted, not
    assumed. Half A would flag such an import as a non-stdlib package, but under
    a message about third-party dependencies -- this states the actual rule and
    counts the core modules it inspected."""
    product_names = {name for name in os.listdir(PRODUCT_DIR)
                     if os.path.isdir(os.path.join(PRODUCT_DIR, name))
                     and not name.startswith("__")}
    product_names |= {n[:-3] for n in os.listdir(PRODUCT_DIR) if n.endswith(".py")}
    assert product_names, "no product top-level names to check against"

    inspected = sorted(import_gate.declared_core_files(RUNNER_DIR))
    assert len(inspected) >= 1
    for name in inspected:
        imports = import_gate.top_level_imports(parse(os.path.join(RUNNER_DIR, name)))
        assert not (imports & product_names), f"{name} imports the product"


# --------------------------------------------------------------------------- #
# Negative control on a copy of the real product, not a stand-in.
# --------------------------------------------------------------------------- #

def test_an_illegal_import_in_the_real_product_tree_fails_the_gate(tmp_path):
    """AC#3, kept. The one-off demonstration was run against the real tree and
    reverted; this is the permanent form, run against a COPY of the real product
    so that the subject being poisoned is the shipped code and not a fixture.
    The copy passes first -- otherwise the FAIL below could be the copy."""
    copied = tmp_path / "product"
    shutil.copytree(PRODUCT_DIR, copied)
    before = import_gate.check_product_depends_on_core_only(str(copied), RUNNER_DIR)
    assert before["status"] == import_gate.PASS, before["violations"]
    assert before["subjects"] == product_py_files()

    (copied / PRODUCT_PACKAGE / "illegal.py").write_text("import run\n", encoding="utf-8")
    after = import_gate.check_product_depends_on_core_only(str(copied), RUNNER_DIR)
    assert after["status"] == import_gate.FAIL
    assert len(after["subjects"]) == len(before["subjects"]) + 1
    assert any("illegal.py" in v and "may depend on the core only" in v
               for v in after["violations"]), after["violations"]


# --------------------------------------------------------------------------- #
# AC#4: the entry point runs, from somewhere that is not the repo root.
# --------------------------------------------------------------------------- #

def run_entry_point(cwd, pythonpath):
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath
    env.pop("PYTHONHOME", None)
    return subprocess.run([sys.executable, "-m", PRODUCT_PACKAGE],
                          cwd=str(cwd), env=env, capture_output=True, text=True)


def test_entry_point_runs_from_a_working_directory_that_is_not_the_repo_root(tmp_path):
    """AC#4. Run as a subprocess rather than imported, because the claim is
    about an invocation: a fresh interpreter, a foreign cwd, and the core
    supplied by the environment exactly as ../pyproject.toml documents. The
    printed figures are checked against registry.py rather than eyeballed."""
    proc = run_entry_point(tmp_path, os.pathsep.join([PRODUCT_DIR, RUNNER_DIR]))
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout

    reported_cwd = [ln.split(": ", 1)[1] for ln in out.splitlines()
                    if ln.startswith("working directory: ")]
    assert len(reported_cwd) == 1
    assert os.path.realpath(reported_cwd[0]) == os.path.realpath(str(tmp_path))
    assert not reported_cwd[0].startswith(REPO_ROOT)
    assert os.path.join(RUNNER_DIR, "registry.py") in out
    assert f"declares {len(registry.MODELS)} models" in out
    assert f"{len({s['family'] for s in registry.MODELS.values()})} families" in out
    for mid in sorted(m for m in registry.MODELS if registry.is_metered(m)):
        assert mid in out


def test_entry_point_fails_loudly_when_the_core_is_not_on_the_path(tmp_path):
    """The control for the test above: if the entry point printed its report
    without the core, the pass up there would prove nothing about reachability.
    It must fail, non-zero, and say how to supply the core."""
    proc = run_entry_point(tmp_path, PRODUCT_DIR)
    assert proc.returncode != 0
    assert "core is not on the import path" in proc.stderr
    assert "PYTHONPATH" in proc.stderr


def test_the_entry_point_module_is_the_console_script_target():
    """The manifest names `gauntlet_playground.__main__:main`. If that function
    is renamed the installed script breaks with no test failing, so the name is
    pinned here against the manifest text."""
    with open(os.path.join(PRODUCT_DIR, "pyproject.toml"), "r", encoding="utf-8") as f:
        manifest = f.read()
    assert 'gauntlet-playground = "gauntlet_playground.__main__:main"' in manifest

    sys.path.insert(0, PRODUCT_DIR)
    try:
        import gauntlet_playground.__main__ as entry
    finally:
        sys.path.remove(PRODUCT_DIR)
    assert callable(entry.main)
    assert entry.main() == 0

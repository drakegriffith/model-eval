#!/usr/bin/env python3
"""import_gate.py -- ratchets the one-way dependency rule of option B'
(projects/frontier-benchmark/14b-repo-boundary-doctrine-consult.md §2(c) as
rewritten, §8; ticket 33).

The invariant, in two halves:

    A. The core stays stdlib-only. A core module may import the standard
       library and other core modules, and nothing else -- no third-party
       package, and no non-core module of this instrument.
    B. The product depends on the core only. A file under product/ may reach
       the core and the standard library, never the instrument's non-core
       modules (run.py, judge.py, broker.py, ...).

This gate is a RATCHET, not a migration target. A scan of every import in the
four core modules yields zero non-stdlib names today -- half A is already true.
Nothing is expected to fail on first run, which makes the quality of the
assertion the whole deliverable: a ratchet that cannot detect its own blind
spots is decoration. Half B has zero subjects (product/ does not exist) and is
reported UNENFORCED rather than passed; see `verdict()`.

WHAT THIS GATE DOES NOT PROVE. It reads import statements and nothing else.
Passing it is not evidence the core is extractable, because the core's
PORTABILITY is separately known-broken at two sites the 14b consult recorded as
F3: runner/ladder_from_results.py:40-41 manipulates sys.path, and
runner/usage_ledger.py:30-34 hardcodes repo paths. Repairing those is separate
work; this gate names them so that a green run stops implying they are absent.

HOW SUBJECTS ARE FOUND, and why it is not the literal list. Membership in the
core is declared on disk by a module-level `CORE_MODULE = True` in each core
module, read via `ast` without importing anything. The declaration exists
because import counting alone cannot see its own blind spot: registry.py is
import-free by design, so "scanned it and found no bad imports" and "never
opened it" produce byte-identical evidence. Comparing the declared set against
the literal list below by SET EQUALITY closes both directions -- a core module
that quietly leaves the literal is caught by its declaration, and a literal
entry with no file on disk is caught by its absence.
"""
import argparse
import ast
import os
import sys

RUNNER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")

# --------------------------------------------------------------------------- #
# THE DECISION RULE -- COPY 1 OF 2
# --------------------------------------------------------------------------- #
# DO NOT DRY THIS. The identical tuple is duplicated in
# runner/tests/test_import_gate.py, and the duplication IS the control
# (harness #5: a gate must not read its decision rule from a file the worker
# under inspection can edit). If this list were the only copy, a worker adding a
# third-party dependency to stats.py could pass the gate by deleting "stats.py"
# from this line. The test's independent copy is what makes that edit fail. A
# later tidy-up pass that re-unifies these two literals into one shared constant
# removes the control while leaving every test green -- reject that pass; this
# comment is the standing reason to reject it.
#
# Membership is B''s core as of 2026-07-29: the modules extracted or already
# leaf. PRICING IS ABSENT BY FACT, NOT OVERSIGHT -- the consult's fifth core
# surface is not yet a module (runner/tables.py:29-30 are self-labelled
# placeholder prices). When the pricing ticket lands, adding it here is a
# deliberate edit in BOTH copies plus a `CORE_MODULE = True` in the new module.
# That three-place cost is the intended friction, not a defect to design away.
CORE_MODULES = ("effort_verdict.py", "registry.py", "stats.py", "token_units.py")

# Statuses. UNENFORCED is deliberately not a kind of PASS: it is the answer for a
# direction whose subject set is empty, which is a result requiring a decision
# rather than a silent default.
PASS = "PASS"
FAIL = "FAIL"
UNENFORCED = "UNENFORCED"

CORE_DECLARATION = "CORE_MODULE"

# The portability defects a green run must not be read as clearing (14b F3).
KNOWN_BROKEN_PORTABILITY = (
    "runner/ladder_from_results.py:40-41 -- manipulates sys.path",
    "runner/usage_ledger.py:30-34 -- hardcodes repo paths",
)


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def declares_core(tree):
    """True if a module-level `CORE_MODULE = True` appears in `tree`.

    Module level only, and only the literal True. A declaration nested in a
    function or set to a computed value is not a declaration a reader can
    verify by eye, so it does not count.
    """
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        for target in targets:
            if isinstance(target, ast.Name) and target.id == CORE_DECLARATION:
                return isinstance(node.value, ast.Constant) and node.value.value is True
    return False


def top_level_imports(tree):
    """Top-level module names imported anywhere in `tree`.

    `import a.b` and `from a.b import c` both yield "a". A relative import
    (`from . import x`, level > 0) yields "x" -- the sibling name -- since that
    is the module being depended on.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and not node.module:
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif node.module:
                names.add(node.module.split(".")[0])
    return names


def declared_core_files(runner_dir):
    """Filenames in `runner_dir` carrying the on-disk core declaration."""
    found = set()
    for name in sorted(os.listdir(runner_dir)):
        if not name.endswith(".py"):
            continue
        if declares_core(_parse(os.path.join(runner_dir, name))):
            found.add(name)
    return found


def local_module_stems(runner_dir):
    """Import names that resolve to a .py file in this instrument."""
    return {n[:-3] for n in os.listdir(runner_dir) if n.endswith(".py")}


def verdict(subjects, violations):
    """The only place a status is decided, so that PASS cannot be reached with an
    empty subject set. A direction with nothing to inspect is UNENFORCED."""
    if violations:
        return FAIL
    if not subjects:
        return UNENFORCED
    return PASS


def check_core_is_stdlib_only(runner_dir=RUNNER_DIR, core_literal=CORE_MODULES):
    """Half A. Subjects are the declared core modules; the declared set must
    equal `core_literal` exactly, and every import must be stdlib or intra-core.
    """
    literal = set(core_literal)
    declared = declared_core_files(runner_dir)
    on_disk_literal = {n for n in literal if os.path.exists(os.path.join(runner_dir, n))}
    local = local_module_stems(runner_dir)
    core_stems = {n[:-3] for n in literal}

    violations = []
    # Set equality, both directions, distinct messages. Neither is a special case
    # of the other: the first is a module escaping the rule, the second is the
    # rule naming a module that is not there to be checked.
    for name in sorted(declared - literal):
        violations.append(
            f"{name}: declares {CORE_DECLARATION} = True on disk but is ABSENT FROM THE "
            f"LITERAL core list in import_gate.py -- add it to both copies of the rule, "
            f"or remove the declaration")
    for name in sorted(literal - declared):
        if name in on_disk_literal:
            violations.append(
                f"{name}: named in the LITERAL core list but does not declare "
                f"{CORE_DECLARATION} = True -- the gate cannot confirm it inspected this "
                f"module rather than skipped it")
        else:
            violations.append(
                f"{name}: named in the LITERAL core list but MISSING FROM DISK at "
                f"{runner_dir} -- a rule entry with no subject")

    # Import check over the intersection: the modules that are both claimed and
    # confirmed. Modules in dispute above are already failing.
    subjects = sorted(declared & literal)
    for name in subjects:
        imports = top_level_imports(_parse(os.path.join(runner_dir, name)))
        for imported in sorted(imports):
            if imported in sys.stdlib_module_names:
                continue
            if imported in core_stems:
                continue
            if imported in local:
                violations.append(
                    f"{name}: imports `{imported}`, a NON-CORE module of this instrument "
                    f"-- the core may not depend on the instrument")
            else:
                violations.append(
                    f"{name}: imports `{imported}`, a NON-STDLIB package -- the core is "
                    f"stdlib-only")

    return {
        "name": "core -> stdlib only",
        "status": verdict(subjects, violations),
        "subjects": subjects,
        "violations": violations,
        "note": (f"declared on disk: {len(declared)}; named in the literal: {len(literal)}; "
                 f"set equality {'HOLDS' if declared == literal else 'BROKEN'}"),
    }


def check_product_depends_on_core_only(product_dir=PRODUCT_DIR,
                                       runner_dir=RUNNER_DIR,
                                       core_literal=CORE_MODULES):
    """Half B. Subjects are .py files under product/. Each may import the stdlib,
    the core, and third-party packages -- never a non-core instrument module.

    product/ does not exist today, so this returns UNENFORCED over zero
    subjects. That is the honest answer: an import scan living inside the
    instrument cannot see a product that is not there, and nothing currently
    stops a future product from importing run.py directly.
    """
    core_stems = {n[:-3] for n in core_literal}
    local = local_module_stems(runner_dir)
    non_core = local - core_stems

    subjects, violations = [], []
    for dirpath, _dirnames, filenames in os.walk(product_dir):
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            subjects.append(os.path.relpath(path, product_dir))
            for imported in sorted(top_level_imports(_parse(path))):
                if imported in non_core:
                    violations.append(
                        f"{os.path.relpath(path, product_dir)}: imports `{imported}`, a "
                        f"NON-CORE module of the instrument -- the product may depend on "
                        f"the core only")

    status = verdict(sorted(subjects), violations)
    note = (f"{product_dir} does not exist -- the product half of the invariant has no "
            f"subjects and is not being enforced by anything"
            if not os.path.isdir(product_dir) else
            f"scanned {product_dir}")
    return {
        "name": "product -> core only",
        "status": status,
        "subjects": sorted(subjects),
        "violations": violations,
        "note": note,
    }


def report(directions):
    """Render every direction with its status, its subject COUNT and the subject
    NAMES. The names are printed because a count alone cannot distinguish a gate
    that inspected all of its subjects from one that inspected some of them.
    """
    lines = ["import gate -- one-way dependency ratchet (ticket 33, option B')"]
    for d in directions:
        lines.append("")
        lines.append(f"  {d['name']}: {d['status']}")
        lines.append(f"    {len(d['subjects'])} files inspected: "
                     f"{', '.join(d['subjects']) if d['subjects'] else '(none)'}")
        lines.append(f"    {d['note']}")
        for v in d["violations"]:
            lines.append(f"    VIOLATION {v}")

    lines.append("")
    lines.append("  scope: import statements only. Passing this gate is NOT evidence the")
    lines.append("  core is portable or extractable. Known-broken, unaffected by this gate:")
    for site in KNOWN_BROKEN_PORTABILITY:
        lines.append(f"    - {site}")
    lines.append("  pricing is absent from the core list by fact, not oversight: it is not")
    lines.append("  yet a module (runner/tables.py:29-30 are placeholder prices).")
    lines.append("")
    lines.append(f"GATE: {summary(directions)}")
    return "\n".join(lines)


def summary(directions):
    """One line. A bare "PASS" is unreachable while any direction is UNENFORCED --
    the unenforced directions are named in the same breath as the pass."""
    if any(d["status"] == FAIL for d in directions):
        failed = ", ".join(d["name"] for d in directions if d["status"] == FAIL)
        return f"FAIL ({failed})"
    unenforced = [d["name"] for d in directions if d["status"] == UNENFORCED]
    enforced = [d["name"] for d in directions if d["status"] == PASS]
    if unenforced:
        return (f"PASS over {len(enforced)} of {len(directions)} directions; "
                f"{len(unenforced)} UNENFORCED: {', '.join(unenforced)}")
    return f"PASS (all {len(directions)} directions enforced)"


def run_gate():
    return [check_core_is_stdlib_only(), check_product_depends_on_core_only()]


def main(argv=None):
    argparse.ArgumentParser(
        description="Check the one-way dependency rule: core stays stdlib-only, "
                    "product depends on core only.").parse_args(argv)
    directions = run_gate()
    print(report(directions))
    return 1 if any(d["status"] == FAIL for d in directions) else 0


if __name__ == "__main__":
    raise SystemExit(main())

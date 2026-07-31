"""gauntlet_playground.__main__ -- the product's entry point (ticket 38, slice S7).

This module was the whole of the product's code when it landed; executor.py
(ticket 40) and surface.py/render.py (ticket 44) have since joined it. Its job
is still not to do product work -- those modules own that. Its job is to make the
product half of the one-way dependency rule ENFORCEABLE. runner/import_gate.py's
half B inspects .py files under product/; until this file existed it had zero
subjects and reported UNENFORCED, and a boundary with no subjects prints
byte-identically to one that holds.

WHAT IT PRINTS, and why those facts. Every line below is checkable against
runner/registry.py by eye, and against the module itself by
runner/tests/test_product_boundary.py. The load-bearing line is the resolved
path: it shows the product reached the REAL core module of this repo rather than
some stub that happened to answer to the same name.

WHAT IT MAY IMPORT. The stdlib, third-party packages, and the core
(corpus_gates, effort_verdict, registry, stats, token_units) -- by BARE MODULE
NAME, never `runner.registry`. That is not a style preference. The gate reasons
in top-level import names, so an illegal edge written as `from runner import run`
would reach it as the name `runner`, which is not a module of the instrument and
would pass unnoticed. Importing the way the gate can see is what makes the gate's
PASS mean anything.

WHAT IT MUST NOT DO. Touch sys.path. The core is placed on the import path by
whoever invokes this (see ../pyproject.toml for the contract); a product that
installs its own path is a product that cannot be moved out of this repo, and it
would be a second instance of the F3 defect runner/ladder_from_results.py:40-41
already carries once.
"""
import os

try:
    import registry
except ModuleNotFoundError as exc:
    raise SystemExit(
        f"gauntlet-playground: the core is not on the import path ({exc}).\n"
        f"This entry point does not put it there, deliberately. Supply it:\n"
        f"    PYTHONPATH=<repo>/product:<repo>/runner python3 -m gauntlet_playground")


def lines(cwd=None):
    """The report as a list of strings, so tests can assert on the facts rather
    than on the formatting.

    `cwd` is a parameter and not a call to os.getcwd() inside the loop so that
    the printed working directory is the one the caller means -- the entry point
    passes the real one, and a test can pin it.
    """
    families = {}
    for mid, spec in registry.MODELS.items():
        families.setdefault(spec["family"], []).append(mid)
    breakdown = ", ".join(f"{fam}={len(ids)}"
                          for fam, ids in sorted(families.items()))
    metered = sorted(mid for mid in registry.MODELS if registry.is_metered(mid))

    sol_id, sol_spec = registry.resolve_model("sol")
    try:
        registry.check_effort("gpt-5.5", "max")
        rejection = "NOT REJECTED -- registry.check_effort accepted an undeclared tier"
    except ValueError as exc:
        rejection = str(exc)

    return [
        "gauntlet-playground -- the product surface of model-gauntlet",
        f"working directory: {cwd if cwd is not None else os.getcwd()}",
        f"core reached by a normal import: registry <- {os.path.abspath(registry.__file__)}",
        f"registry declares {len(registry.MODELS)} models across "
        f"{len(families)} families: {breakdown}",
        f"metered ids (a run of these costs real money): {', '.join(metered)}",
        f"alias resolution: 'sol' -> {sol_id!r}, family {sol_spec['family']!r}",
        f"effort rejected before spend: {rejection}",
        "boundary: this file imports the stdlib and the core only, and does not "
        "touch sys.path",
    ]


def main(argv=None):
    """Print the report. No arguments today -- `argv` exists so the console
    script entry point in ../pyproject.toml has the signature it will need."""
    print("\n".join(lines(os.getcwd())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# product/ — the model-gauntlet product surface

Repo shape **B′** (`14b-repo-boundary-doctrine-consult` §8): the instrument's core is a set of
modules under `runner/`, the product is this directory with its own manifest, both in one repo,
with one import gate policing the edge between them.

## Run it

The core is supplied on the import path by the caller, never by product code:

```
PYTHONPATH="$PWD/product:$PWD/runner" python3 -m gauntlet_playground
```

Run it from anywhere — the paths above are absolute once expanded, and the entry point prints the
working directory it ran in so you can see it was not the repo root.

## The rule this directory exists to make enforceable

A file under `product/` may import the standard library, third-party packages, and the **core**
modules — `corpus_gates`, `effort_verdict`, `registry`, `stats`, `token_units` — by bare module
name. It may not import a non-core module of the instrument (`run`, `judge`, `broker`, `tables`, …),
and it may not touch `sys.path`.

Enforced by `runner/import_gate.py` half B, which inspects every `.py` file here and reports the
count and the names, and by `runner/tests/test_product_boundary.py`. Before this directory existed
that half had zero subjects and reported `UNENFORCED` — which is the same output a held boundary
would produce, and the reason this slice was filed first among the product slices.

Check it:

```
python3 runner/import_gate.py
```

"""gauntlet_playground -- the model-gauntlet product, packaged as a unit.

The package is deliberately empty of behaviour. Everything it does today lives in
__main__.py, and everything it will do (tickets 39/40/41/43/44/45) is bound by
one rule, stated here because this is the file a reader opens first:

    A file under product/ may import the standard library, third-party packages,
    and the CORE modules of the instrument -- corpus_gates, effort_verdict,
    registry, stats, token_units -- by bare module name. It may never import a
    non-core module of the instrument (run, judge, broker, tables, ...), and it
    may never manipulate sys.path.

That rule is not enforced here. It is enforced by runner/import_gate.py half B,
which inspects every .py file under this directory, and by
runner/tests/test_product_boundary.py. Deleting this package does not make the
product half of the boundary pass quietly -- it makes the gate report UNENFORCED
over zero subjects again, and the test that counts subjects fails.

No __version__ here on purpose: the version lives in ../pyproject.toml and is
declared once, so the two cannot drift apart.
"""

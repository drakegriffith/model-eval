"""Hidden acceptance suite for tasks/t5-py-a.

Never ships inside `base/`. Run by `verify.sh` with `--tb=no -rN`, so a
failure reports a count and a position in the progress line — never a test
name, a path, or an assertion. Everything asserted here is specified in
`PROMPT.md`; nothing is invented.

The store handed to `apply_ops` is `_refstore.LegacyStore`, not the vendored
copy under `base/src/`, per PROMPT.md section 1.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path.cwd() / "src"))

from _refstore import LegacyStore  # noqa: E402
from vaultstore import apply_ops  # noqa: E402


def test_ac_01():
    # rule 8: rejected keys are echoed verbatim, not normalised
    store = LegacyStore()
    rep = apply_ops(store, [{"op": "set", "key": "A" * 17, "value": "v"}])
    assert rep["rejected"] == ["A" * 17]


def test_ac_02():
    # rule 1: an all-rejected batch must not call commit() at all
    store = LegacyStore()
    rep = apply_ops(store, [
        {"op": "set", "key": "k" * 20, "value": "v"},
        {"op": "set", "key": "ok", "value": [1, 2]},
    ])
    assert rep["applied"] == 0
    assert "commit" not in store.names()
    assert rep["rejected"] == ["k" * 20, "ok"]


def test_ac_03():
    # rule 7: commit truncates str values to 32 chars, so final must be read back
    store = LegacyStore()
    long_value = "z" * 40
    rep = apply_ops(store, [{"op": "set", "key": "blob", "value": long_value}])
    assert rep["final"] == {"blob": "z" * 32}


def test_ac_04():
    # rule 7 + rule 8: commit folds keys, the report echoes the op's spelling
    store = LegacyStore()
    rep = apply_ops(store, [{"op": "set", "key": "REGION", "value": "us-east"}])
    assert rep["final"] == {"REGION": "us-east"}
    assert store.fetch("region") == (True, "us-east")


def test_ac_05():
    # rule 4: set then delete in the same batch skips the delete, key stays present
    store = LegacyStore()
    rep = apply_ops(store, [
        {"op": "set", "key": "tmp", "value": "v"},
        {"op": "delete", "key": "tmp"},
    ])
    assert rep["skipped"] == ["tmp"]
    assert rep["removed"] == {}
    assert rep["final"] == {"tmp": "v"}


def test_ac_06():
    # rule 3 + rule 6: a real removal reports the old value and drops out of final
    store = LegacyStore({"region": "us-east", "tier": 3})
    rep = apply_ops(store, [{"op": "delete", "key": "region"}])
    assert rep["removed"] == {"region": "us-east"}
    assert rep["final"] == {}
    assert store.fetch("region") == (False, None)


def test_ac_07():
    # rule 5: abort discards the ops that came before the bad one
    store = LegacyStore({"tier": 3})
    rep = apply_ops(store, [
        {"op": "set", "key": "region", "value": "eu-west"},
        {"op": "delete", "key": "tier"},
        {"op": "rename", "key": "tier"},
    ])
    assert rep == {"applied": 0, "rejected": [], "skipped": [],
                   "removed": {}, "final": {}, "aborted": True}
    assert store.fetch("tier") == (True, 3)
    assert store.fetch("region") == (False, None)


def test_ac_08():
    # rule 5: flush exactly once on abort, and commit never
    store = LegacyStore({"tier": 3})
    apply_ops(store, [{"op": "set", "key": "a", "value": 1}, {"op": "wipe", "key": "a"}])
    assert store.names().count("flush") == 1
    assert "commit" not in store.names()


def test_ac_09():
    # rule 5: flush is never called on a batch that does not abort
    store = LegacyStore({"tier": 3})
    apply_ops(store, [
        {"op": "set", "key": "region", "value": "eu-west"},
        {"op": "delete", "key": "tier"},
        {"op": "delete", "key": "ghost"},
    ])
    assert "flush" not in store.names()
    assert store.names().count("commit") == 1


def test_ac_10():
    # rule 2: bools and floats are rejected values, and rejection is not an abort
    store = LegacyStore()
    rep = apply_ops(store, [
        {"op": "set", "key": "flag", "value": True},
        {"op": "set", "key": "ratio", "value": 1.5},
        {"op": "set", "key": "count", "value": 7},
    ])
    assert rep["rejected"] == ["flag", "ratio"]
    assert rep["applied"] == 1
    assert rep["final"] == {"count": 7}


def test_ac_11():
    # rule 8: op order preserved, duplicates kept, in both lists
    store = LegacyStore()
    rep = apply_ops(store, [
        {"op": "delete", "key": "gone"},
        {"op": "set", "key": "q" * 18, "value": 1},
        {"op": "delete", "key": "gone"},
        {"op": "set", "key": "q" * 18, "value": 2},
    ])
    assert rep["skipped"] == ["gone", "gone"]
    assert rep["rejected"] == ["q" * 18, "q" * 18]


def test_ac_12():
    # rule 6: final is first-appearance ordered and omits keys fetch did not find
    store = LegacyStore({"old": "v"})
    rep = apply_ops(store, [
        {"op": "set", "key": "b", "value": 2},
        {"op": "delete", "key": "old"},
        {"op": "set", "key": "a", "value": 1},
        {"op": "set", "key": "b", "value": 3},
    ])
    assert rep["final"] == {"b": 3, "a": 1}
    assert list(rep["final"].keys()) == ["b", "a"]


def test_ac_13():
    # rule 1: an empty op list commits nothing and reports an empty report
    store = LegacyStore({"tier": 3})
    rep = apply_ops(store, [])
    assert rep["applied"] == 0
    assert rep["aborted"] is False
    assert rep["final"] == {}
    assert store.names() == []


def test_ac_14():
    # rule 1: applied is commit()'s own return and counts kills as well as puts
    store = LegacyStore({"gone": 1})
    rep = apply_ops(store, [
        {"op": "set", "key": "a", "value": 1},
        {"op": "set", "key": "b", "value": 2},
        {"op": "delete", "key": "gone"},
    ])
    assert rep["applied"] == 3


def test_ac_15():
    # rule 6: ops never mutated
    store = LegacyStore()
    ops = [{"op": "set", "key": "a", "value": 1}]
    apply_ops(store, ops)
    assert ops == [{"op": "set", "key": "a", "value": 1}]

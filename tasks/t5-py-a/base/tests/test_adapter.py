import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vaultstore import LegacyStore, apply_ops


def test_sets_are_staged_and_committed_once():
    store = LegacyStore()
    rep = apply_ops(store, [
        {"op": "set", "key": "region", "value": "us-east"},
        {"op": "set", "key": "tier", "value": 3},
    ])
    assert rep["applied"] == 2
    assert [c[0] for c in store.calls].count("commit") == 1


def test_a_rejected_put_does_not_abort_the_batch():
    store = LegacyStore()
    rep = apply_ops(store, [
        {"op": "set", "key": "x" * 20, "value": "too-long-a-key"},
        {"op": "set", "key": "tier", "value": 3},
    ])
    assert rep["rejected"] == ["x" * 20]
    assert rep["applied"] == 1


def test_delete_goes_through_append_and_reports_the_removed_value():
    store = LegacyStore({"region": "us-east", "tier": 3})
    rep = apply_ops(store, [{"op": "delete", "key": "region"}])
    assert rep["removed"] == {"region": "us-east"}
    assert rep["skipped"] == []
    assert rep["applied"] == 1


def test_delete_of_an_absent_key_is_a_skip():
    store = LegacyStore({"tier": 3})
    rep = apply_ops(store, [
        {"op": "delete", "key": "nope"},
        {"op": "set", "key": "region", "value": "eu-west"},
    ])
    assert rep["skipped"] == ["nope"]
    assert rep["removed"] == {}
    assert rep["applied"] == 1


def test_an_unknown_op_aborts_and_flushes():
    store = LegacyStore({"tier": 3})
    rep = apply_ops(store, [
        {"op": "set", "key": "region", "value": "eu-west"},
        {"op": "purge", "key": "tier"},
    ])
    assert rep["aborted"] is True
    assert rep["applied"] == 0
    assert rep["final"] == {}
    assert [c[0] for c in store.calls].count("flush") == 1
    assert "commit" not in [c[0] for c in store.calls]


def test_final_is_read_back_after_the_commit():
    store = LegacyStore()
    rep = apply_ops(store, [
        {"op": "set", "key": "region", "value": "us-east"},
        {"op": "set", "key": "tier", "value": 3},
    ])
    assert rep["final"] == {"region": "us-east", "tier": 3}

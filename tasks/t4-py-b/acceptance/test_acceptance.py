"""Hidden acceptance suite for tasks/t4-py-b.

Never ships inside `base/`. Run by `verify.sh` with `--tb=no -rN`, so a
failure reports a count and a position in the progress line — never a test
name, a path, or an assertion. Everything asserted here is specified in
`PROMPT.md`; nothing is invented.
"""

import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path.cwd() / "src"))

from layercfg import resolve  # noqa: E402


def test_ac_01():
    # None deletes a top-level key outright, it does not set it to None
    out = resolve([{"a": 1, "b": 2}, {"a": None}])
    assert out == {"b": 2}
    assert "a" not in out


def test_ac_02():
    # deleting a key that was never present is a no-op, not an error
    assert resolve([{"a": 1}, {"zzz": None}]) == {"a": 1}


def test_ac_03():
    # a deleted key can be re-introduced by a still-later layer
    out = resolve([{"a": 1}, {"a": None}, {"a": 9}])
    assert out == {"a": 9}


def test_ac_04():
    # lock freezes every descendant, not just the keys present when it was set
    out = resolve([
        {"db": {"__lock__": True, "creds": {"user": "svc"}}},
        {"db": {"creds": {"user": "root", "pass": "x"}}},
    ])
    assert out == {"db": {"creds": {"user": "svc"}}}


def test_ac_05():
    # lock beats delete
    out = resolve([{"db": {"__lock__": True, "host": "h"}}, {"db": None}])
    assert out == {"db": {"host": "h"}}


def test_ac_06():
    # lock beats append
    out = resolve([
        {"db": {"__lock__": True, "args": ["--a"]}},
        {"db": {"args+": ["--b"]}},
    ])
    assert out == {"db": {"args": ["--a"]}}


def test_ac_07():
    # a `+` key with nothing inherited creates the list
    out = resolve([{"x": 1}, {"args+": ["--b"]}])
    assert out == {"x": 1, "args": ["--b"]}
    assert "args+" not in out


def test_ac_08():
    # `+` appends onto an already-appended list across three layers
    out = resolve([{"args": ["a"]}, {"args+": ["b"]}, {"args+": ["c"]}])
    assert out == {"args": ["a", "b", "c"]}


def test_ac_09():
    # the marker is stripped at every depth, and a lock set deeper down
    # only freezes its own subtree
    out = resolve([
        {"outer": {"inner": {"__lock__": True, "k": 1}, "sibling": 2}},
        {"outer": {"inner": {"k": 99}, "sibling": 3}},
    ])
    assert out == {"outer": {"inner": {"k": 1}, "sibling": 3}}
    assert "__lock__" not in str(out)


def test_ac_10():
    # a dict value replaces a scalar and then merges normally
    out = resolve([{"db": "sqlite"}, {"db": {"host": "h"}}, {"db": {"port": 1}}])
    assert out == {"db": {"host": "h", "port": 1}}


def test_ac_11():
    # the input layers are never mutated, and the output never aliases them
    layers = [{"args": ["a"], "db": {"host": "h"}}, {"args+": ["b"]}]
    before = copy.deepcopy(layers)
    out = resolve(layers)
    assert layers == before
    out["args"].append("mutated")
    out["db"]["host"] = "mutated"
    assert layers == before


def test_ac_12():
    # `__lock__: False` is not a lock, and the marker is still stripped
    out = resolve([{"db": {"__lock__": False, "host": "h"}}, {"db": {"host": "h2"}}])
    assert out == {"db": {"host": "h2"}}

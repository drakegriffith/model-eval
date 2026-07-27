import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from layercfg import resolve


def test_later_layer_overrides_scalar():
    assert resolve([{"replicas": 1}, {"replicas": 3}]) == {"replicas": 3}


def test_dicts_merge_recursively():
    out = resolve([
        {"db": {"host": "localhost", "port": 5432}},
        {"db": {"port": 6543}},
    ])
    assert out == {"db": {"host": "localhost", "port": 6543}}


def test_lists_replace_rather_than_concatenate():
    out = resolve([{"args": ["--a", "--b"]}, {"args": ["--c"]}])
    assert out == {"args": ["--c"]}


def test_plus_key_appends_to_inherited_list():
    out = resolve([{"args": ["--a"]}, {"args+": ["--b"]}])
    assert out == {"args": ["--a", "--b"]}
    assert "args+" not in out


def test_none_deletes_the_key():
    out = resolve([{"db": {"host": "localhost", "port": 5432}}, {"db": {"port": None}}])
    assert out == {"db": {"host": "localhost"}}


def test_lock_marker_is_stripped_and_blocks_a_later_write():
    out = resolve([
        {"db": {"__lock__": True, "host": "prod.internal"}},
        {"db": {"host": "attacker.example"}},
    ])
    assert out == {"db": {"host": "prod.internal"}}

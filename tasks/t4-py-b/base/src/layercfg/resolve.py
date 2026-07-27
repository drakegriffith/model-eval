"""Resolve a stack of configuration layers into one effective config.

Layers are applied left to right: `layers[0]` is the base, each later layer
is an override on top of everything resolved so far. The result is a fresh
dict — the input layers are never mutated.

Three of the layer rules are implemented here. The rest are specified in
`../PROMPT.md` and are not implemented yet:

  IMPLEMENTED
  1. Later wins.       A scalar in a later layer replaces the earlier value.
  2. Dicts merge.      Two dict values at the same key merge recursively.
                       LISTS DO NOT MERGE — a list replaces a list wholesale.
  3. `key+` appends.   A key written `name+` in a layer appends its list to
                       whatever list was inherited under `name`. The output
                       key is always `name`; `name+` never appears in the
                       result.

  NOT IMPLEMENTED YET
  4. `None` deletes.
  5. `__lock__` freezes a subtree.
  6. Precedence between 3, 4 and 5.
"""

from copy import deepcopy

LOCK_MARKER = "__lock__"


def resolve(layers: list[dict]) -> dict:
    """Fold `layers` left to right into one effective config dict."""
    out: dict = {}
    for layer in layers:
        _merge(out, layer)
    return out


def _merge(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if key.endswith("+"):
            name = key[:-1]
            inherited = dst.get(name)
            base = list(inherited) if isinstance(inherited, list) else []
            dst[name] = base + deepcopy(list(value))
            continue

        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge(dst[key], value)
            continue

        dst[key] = deepcopy(value)

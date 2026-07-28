"""Reference VAULT-7 store for the hidden suite.

Byte-independent of `base/src/vaultstore/legacy.py` on purpose: the graders
construct THIS store and hand it to the candidate's `apply_ops`, so rewriting
the vendored copy to be less perverse cannot change the grade. Semantics are
identical to the vendored file and to PROMPT.md section 3.
"""

MAX_KEY_LEN = 16
MAX_VALUE_LEN = 32


class VaultError(Exception):
    pass


class AlreadyCommitted(VaultError):
    pass


class EmptyCommit(VaultError):
    pass


class LegacyStore:
    def __init__(self, initial=None):
        self._committed = dict(initial or {})
        self._staged = []
        self._committed_once = False
        self.calls = []

    def put(self, key, value):
        self.calls.append(("put", key, value))
        if not isinstance(key, str) or len(key) > MAX_KEY_LEN:
            return 1
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return 1
        self._staged.append(("put", key, value))
        return 0

    def append(self, key):
        self.calls.append(("append", key))
        norm = key.lower() if isinstance(key, str) else key
        if norm not in self._committed:
            return None
        self._staged.append(("kill", norm))
        return self._committed[norm]

    def flush(self):
        self.calls.append(("flush",))
        dropped, self._staged = self._staged, []
        return dropped

    def commit(self):
        self.calls.append(("commit",))
        if self._committed_once:
            raise AlreadyCommitted("already committed")
        if not self._staged:
            raise EmptyCommit("nothing staged")
        self._committed_once = True
        applied = 0
        for op in self._staged:
            if op[0] == "put":
                _, key, value = op
                stored = value[:MAX_VALUE_LEN] if isinstance(value, str) else value
                self._committed[key.lower()] = stored
            else:
                self._committed.pop(op[1], None)
            applied += 1
        self._staged = []
        return applied

    def fetch(self, key):
        self.calls.append(("fetch", key))
        norm = key.lower() if isinstance(key, str) else key
        if norm in self._committed:
            return (True, self._committed[norm])
        return (False, None)

    def names(self):
        """Method names called on this store, in order. Test helper only."""
        return [c[0] for c in self.calls]

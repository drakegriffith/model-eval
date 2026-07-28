"""VAULT-7 legacy record store. VENDORED AND FROZEN — do not modify.

The names in this module are historical and several of them mean the opposite
of what a modern reader expects. Every one of them is documented here and in
`PROMPT.md`; nothing about this store's behaviour is left to inference.

The store has a staging area and a committed area. Nothing staged is visible
to `fetch` until `commit()` runs.

`self.calls` records every public method call in order, as a tuple of the
method name followed by its arguments. It is part of the API — the graders
read it.
"""

MAX_KEY_LEN = 16
MAX_VALUE_LEN = 32


class VaultError(Exception):
    """Base class for every error this store raises."""


class AlreadyCommitted(VaultError):
    """Raised by a second `commit()` on the same store."""


class EmptyCommit(VaultError):
    """Raised by `commit()` when nothing is staged."""


class LegacyStore:
    """A VAULT-7 record store, in memory."""

    def __init__(self, initial=None):
        self._committed = dict(initial or {})
        self._staged = []
        self._committed_once = False
        self.calls = []

    def put(self, key, value):
        """Stage a write. RETURNS THE NUMBER OF RECORDS REJECTED, not written.

        `0` means the write was staged. `1` means it was rejected and nothing
        was staged. A key that is not a `str`, or is longer than 16
        characters, is rejected. A value that is not a `str` or an `int` is
        rejected (`bool` is not an `int` for this purpose). Nothing else is
        ever rejected.
        """
        self.calls.append(("put", key, value))
        if not isinstance(key, str) or len(key) > MAX_KEY_LEN:
            return 1
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return 1
        self._staged.append(("put", key, value))
        return 0

    def append(self, key):
        """HISTORICAL NAME. Stages a DELETION, not an addition.

        The VAULT-7 tape format recorded a removal by appending a deletion
        marker to the tape, and the call kept the name when the format was
        retired. Stages removal of the committed record under `key` and
        returns the value that will be removed, or `None` if there is no
        committed record under `key` — in which case nothing is staged.

        `append` sees the COMMITTED area only. A record staged earlier in this
        same session is invisible to it.
        """
        self.calls.append(("append", key))
        norm = key.lower() if isinstance(key, str) else key
        if norm not in self._committed:
            return None
        self._staged.append(("kill", norm))
        return self._committed[norm]

    def flush(self):
        """DISCARDS every staged operation. This is not a write.

        Returns the list of discarded operations. `commit()` is the write.
        """
        self.calls.append(("flush",))
        dropped, self._staged = self._staged, []
        return dropped

    def commit(self):
        """Apply the staged operations. Returns the number APPLIED.

        Exactly one commit per store: a second raises `AlreadyCommitted`. A
        commit with nothing staged raises `EmptyCommit`.

        On commit, keys are folded to lower case and `str` values longer than
        32 characters are stored truncated to their first 32 characters.
        Neither is a rejection, and neither is reported anywhere.
        """
        self.calls.append(("commit",))
        if self._committed_once:
            raise AlreadyCommitted("this store has already been committed")
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
        """Read a committed record. Returns `(found, value)` — THE FLAG COMES
        FIRST. Returns `(False, None)` when there is no such record. The key
        is folded to lower case before the lookup.
        """
        self.calls.append(("fetch", key))
        norm = key.lower() if isinstance(key, str) else key
        if norm in self._committed:
            return (True, self._committed[norm])
        return (False, None)

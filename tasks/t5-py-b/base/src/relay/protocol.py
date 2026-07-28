"""The relay's house control-flow protocol. FROZEN — do not modify.

This codebase inverts the usual convention, and it does so deliberately. It
grew out of a generator-based pipeline where a stage signalled completion the
way `StopIteration` does, and every stage in the tree is written against it:

    RAISING  Done(payload)  means the stage SUCCEEDED.
    RETURNING a value       means the stage is NOT FINISHED, and the value is
                            a retry hint to feed straight back into it.
    RETURNING None          means abort the whole pipeline.
    RAISING  Abort(reason)  means abort the whole pipeline.

`Done()` with no argument carries a payload of `None` and is still a success.
A `None` payload is a legitimate result — the only `None` that means "stop"
is a *returned* one.

Every other exception is somebody else's problem and must propagate
untouched.
"""


class Done(Exception):
    """Raised by a stage that has FINISHED SUCCESSFULLY."""

    def __init__(self, payload=None):
        super().__init__(payload)
        self.payload = payload


class Abort(Exception):
    """Raised by a stage to stop the whole pipeline."""

    def __init__(self, reason=None):
        super().__init__(reason)
        self.reason = reason

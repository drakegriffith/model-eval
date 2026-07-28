"""Drive stages written against the relay's house protocol.

The protocol is in `protocol.py` and in `../../PROMPT.md`. In short: a stage
RAISES `Done` to succeed and RETURNS a retry hint when it is not finished.

Two of the six rules are implemented here:

  IMPLEMENTED
  1. Raising `Done` is success; a returned value is a retry hint that goes
     straight back into the same stage.
  2. `max_attempts` counts TOTAL invocations, not retries, and running out is
     exhaustion rather than an error.

  NOT IMPLEMENTED YET
  3. A returned `None` aborts the whole pipeline.
  4. Everything that is not `Done` or `Abort` propagates unchanged; `Abort`
     is reported rather than re-raised.
  5. `Done` payloads are the only data that crosses a stage boundary.
  6. `drain` — the first non-`done` stage stops the pipeline.
"""

from .protocol import Abort, Done


def run_stage(fn, seed, max_attempts):
    """Run one stage under the house protocol. See PROMPT.md section 4."""
    arg = seed
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        try:
            arg = fn(arg)
        except Done as d:
            return {"status": "done", "payload": d.payload, "attempts": attempts}
    return {"status": "exhausted", "payload": arg, "attempts": attempts}


def drain(stages, seed, max_attempts):
    """Run `stages` in order under the house protocol. Not implemented yet."""
    raise NotImplementedError("drain is rule 5 and rule 6; see PROMPT.md")

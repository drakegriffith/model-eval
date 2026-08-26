#!/usr/bin/env python3
"""local_endpoint.py -- the one definition of the studio/local-family serving
endpoint, imported by both run.py (which dispatches against it) and
probe_endpoints.py (which certifies it).

Issue #5. Before this module existed, run.py and probe_endpoints.py each
carried a byte-identical copy of LOCAL_BASE_URL / LOCAL_PLACEHOLDER_TOKEN, with
nothing pinning the two copies equal. Two literals that happen to agree today
can drift silently tomorrow: an edit to one copy (a new default port, a renamed
env var) leaves the other unchanged, and the probe would then certify one
endpoint while a run dispatched against a different one, with no red test
anywhere to say so. runner/tests/test_local_family.py pins that this cannot
happen again by grepping runner/*.py for the literal and failing if a second
definition site reappears.

Not CORE_MODULE. This module reads the environment (get_local_base_url), and
the core contract enforced by runner/import_gate.py's EXPECTED_CORE_MODULES --
see the "No I/O, no environment reads, no network" limitation documented in
registry.py and serving_registry.py -- exists precisely to keep environment
reads out of the core. This module sits beside run.py and probe_endpoints.py
as an ordinary instrument module, not the core, so it is free to read os.environ.

get_local_base_url() is a function rather than a module-level constant on
purpose: run.py's own tests (test_local_family.py) reload run.py after
monkeypatching MODEL_EVAL_LOCAL_BASE_URL and expect the new value to take
effect. A function re-reads the environment on every call; a constant computed
once at this module's import time would not change on reload, since Python
does not re-run an already-imported module's top-level code just because a
second module re-imports it.
"""
import os

# LM Studio does not check this value -- there is no account behind it -- but the
# claude binary refuses to start against a custom ANTHROPIC_BASE_URL with an empty
# ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN, so a non-empty placeholder is required to
# get past the CLI's own auth precondition, not LM Studio's.
LOCAL_PLACEHOLDER_TOKEN = "sk-local-lmstudio-unused"


def get_local_base_url():
    """The studio/local-family endpoint: MODEL_EVAL_LOCAL_BASE_URL if set, else
    LM Studio's default loopback port. Overridable because the port (1234) is a
    local dev convention, not a fact about the instrument -- a different port or
    a remote box shouldn't need a code change."""
    return os.environ.get("MODEL_EVAL_LOCAL_BASE_URL", "http://localhost:1234")

"""test_local_base_url_pin.py -- issue #5: LOCAL_BASE_URL / LOCAL_PLACEHOLDER_TOKEN
must be defined in exactly one place: runner/local_endpoint.py.

Before local_endpoint.py existed, run.py and probe_endpoints.py each carried a
byte-identical copy of the studio/local-family endpoint literal, with nothing
pinning the two copies equal. Two literals that happen to agree today can drift
silently tomorrow -- an edit to one copy left unmade in the other means a probe
certifies one endpoint while a run dispatches against a different one, and
nothing before this test would have said so.

Round 2 (verifier controls, each reverted after asserting): the round-1 version
of this test matched only the exact string that local_endpoint.py itself
contains, so it missed every equivalent restatement of the same fact --
- a DRIFTED default: os.environ.get("MODEL_EVAL_LOCAL_BASE_URL", "http://localhost:9999")
  in probe_endpoints.py. This is the exact failure mode issue #5 describes (the
  probe silently certifying a different endpoint than the runner dispatches
  to), and the round-1 test did not catch it because it only matched the
  ORIGINAL default value, not the fact of a second default-carrying read.
- the os.getenv(...) spelling of the same read.
- TOK = "sk-local-lmstudio-unused" -- the placeholder token literal reused
  under a different name.
This version widens the pin from "the exact original string" to three
STRUCTURAL properties, each checked outside local_endpoint.py:
  (a) no other os.environ.get / os.getenv / os.environ[...] read of
      MODEL_EVAL_LOCAL_BASE_URL that carries a default value (a read with no
      default cannot silently disagree with local_endpoint.py's default, since
      it has none of its own to drift -- see the explicit exception below);
  (b) no other http://localhost: or 127.0.0.1: URL literal;
  (c) no other "sk-local-lmstudio-unused" literal, regardless of what name it
      is assigned to.
None of these are line-number assertions: a docstring edit to local_endpoint.py
that shifts every line below it must not turn this test red, so what is pinned
is the FILE the one remaining occurrence lives in and the COUNT (1), never a
line number.

THE ONE LEGITIMATE EXCEPTION to (a): run.py's invocation_provenance reads
MODEL_EVAL_LOCAL_BASE_URL a second time, with NO default argument
(os.environ.get("MODEL_EVAL_LOCAL_BASE_URL") -- one positional arg only),
purely to label a run's endpoint as an override vs the default. That read has
no default value to drift from local_endpoint.py's, so it is a different fact
(was the env var SET) rather than a second definition of WHAT THE DEFAULT IS,
and rule (a) is written to allow exactly the no-default form and nothing wider.

This is a REPO-WIDE grep over runner/*.py (non-recursive: runner/tests/ is not
included, since test fixtures and docstrings there legitimately quote these
values as descriptive text or as exercised inputs to is_loopback_endpoint,
not as a second definition of the default).

Positive controls (run by hand, not committed; see PR body for the paste of
each before/after pytest summary line):
  1. append `_D = os.environ.get("MODEL_EVAL_LOCAL_BASE_URL", "http://localhost:9999")`
     to probe_endpoints.py -> test_no_default_carrying_env_read_outside_local_endpoint
     and test_no_loopback_url_literal_outside_local_endpoint must both fail.
  2. append `_D = os.getenv("MODEL_EVAL_LOCAL_BASE_URL", "http://localhost:1234")`
     to probe_endpoints.py -> the same two tests must fail.
  3. append `TOK = "sk-local-lmstudio-unused"` to probe_endpoints.py ->
     test_no_placeholder_token_literal_outside_local_endpoint must fail.
Revert each before moving on; the real tree must return to all-green between
controls, or a later control cannot be trusted to prove anything about the gate
rather than about leftover injection from the one before it.
"""
import glob
import os
import re

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_ENDPOINT_BASENAME = "local_endpoint.py"

# (a) An os.environ.get / os.getenv / os.environ[...] read of the env var that
# CARRIES A DEFAULT (a second positional arg, or a bracket subscript, which has
# no way to omit one). The no-default single-arg form is deliberately not
# matched -- see the module docstring's "ONE LEGITIMATE EXCEPTION".
ENV_READ_WITH_DEFAULT = re.compile(
    r"""os\.(?:environ\.get|getenv)\(\s*["']MODEL_EVAL_LOCAL_BASE_URL["']\s*,"""
    r"""|os\.environ\[\s*["']MODEL_EVAL_LOCAL_BASE_URL["']\s*\]"""
)

# (b) Any loopback URL literal naming a port, the shape a default value takes.
LOOPBACK_URL_LITERAL = re.compile(r"http://localhost:|127\.0\.0\.1:")

# (c) The placeholder token, verbatim, regardless of what name holds it.
PLACEHOLDER_TOKEN_LITERAL = "sk-local-lmstudio-unused"


def _runner_py_files():
    """Every runner/*.py file (non-recursive -- runner/tests/ is out of scope,
    see module docstring), subjects named so a zero-file grep cannot pass
    silently (harness #24: a gate that inspected zero subjects has not passed)."""
    files = sorted(glob.glob(os.path.join(RUNNER_DIR, "*.py")))
    assert files, "found no runner/*.py files -- RUNNER_DIR is wrong"
    return files


def _matches(pattern_or_literal, exclude_basename=None):
    """Line-numbered hits of a compiled regex or a plain substring, across
    every runner/*.py file except (optionally) one basename. Returns
    'basename:lineno' strings, never bare counts, so a failure names the site."""
    is_regex = hasattr(pattern_or_literal, "search")
    hits = []
    for path in _runner_py_files():
        basename = os.path.basename(path)
        if basename == exclude_basename:
            continue
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                found = (pattern_or_literal.search(line) if is_regex
                         else pattern_or_literal in line)
                if found:
                    hits.append(f"{basename}:{lineno}")
    return hits


def _count_in_file(pattern_or_literal, basename):
    is_regex = hasattr(pattern_or_literal, "search")
    path = os.path.join(RUNNER_DIR, basename)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if is_regex:
        return len(pattern_or_literal.findall(text))
    return text.count(pattern_or_literal)


# --------------------------------------------------------------------------- #
# Control arm: the patterns must be capable of matching at all.
# --------------------------------------------------------------------------- #
def test_control_arm_each_pattern_matches_the_real_definition_site():
    """If any of these is zero, the pattern no longer matches the real code and
    every assertion below would pass for the wrong reason (nothing found)."""
    assert _count_in_file(ENV_READ_WITH_DEFAULT, LOCAL_ENDPOINT_BASENAME) >= 1
    assert _count_in_file(LOOPBACK_URL_LITERAL, LOCAL_ENDPOINT_BASENAME) >= 1
    assert _count_in_file(PLACEHOLDER_TOKEN_LITERAL, LOCAL_ENDPOINT_BASENAME) >= 1


# --------------------------------------------------------------------------- #
# The pin: none of the three structural signatures may exist anywhere else.
# --------------------------------------------------------------------------- #
def test_no_default_carrying_env_read_outside_local_endpoint():
    hits = _matches(ENV_READ_WITH_DEFAULT, exclude_basename=LOCAL_ENDPOINT_BASENAME)
    assert hits == [], (
        "a default-carrying read of MODEL_EVAL_LOCAL_BASE_URL exists outside "
        f"local_endpoint.py (the no-default form is the one allowed exception, "
        f"see this file's docstring): {hits}")


def test_no_loopback_url_literal_outside_local_endpoint():
    hits = _matches(LOOPBACK_URL_LITERAL, exclude_basename=LOCAL_ENDPOINT_BASENAME)
    assert hits == [], (
        f"a loopback URL literal exists outside local_endpoint.py: {hits}")


def test_no_placeholder_token_literal_outside_local_endpoint():
    hits = _matches(PLACEHOLDER_TOKEN_LITERAL, exclude_basename=LOCAL_ENDPOINT_BASENAME)
    assert hits == [], (
        f"the placeholder-token literal exists outside local_endpoint.py: {hits}")


def test_exactly_one_definition_site_of_each():
    """The pin restated as a count rather than a line number: local_endpoint.py
    itself must carry exactly one definition of each, never zero (deleted) and
    never two (duplicated within its own file)."""
    assert _count_in_file(ENV_READ_WITH_DEFAULT, LOCAL_ENDPOINT_BASENAME) == 1
    assert _count_in_file(LOOPBACK_URL_LITERAL, LOCAL_ENDPOINT_BASENAME) == 1
    assert _count_in_file(PLACEHOLDER_TOKEN_LITERAL, LOCAL_ENDPOINT_BASENAME) == 1


# --------------------------------------------------------------------------- #
# The allowed exception, proven present and proven distinct from the pin.
# --------------------------------------------------------------------------- #
def test_run_py_carries_the_one_allowed_no_default_read():
    """run.py's provenance read (os.environ.get with ONE arg, no default) is
    the documented exception to rule (a). Asserted present, and asserted NOT
    to match ENV_READ_WITH_DEFAULT, so a future edit that quietly adds a
    default to it is caught by the rule above rather than waved through by
    this test's own exclusion list (there is no exclusion list -- run.py is
    fully in scope, it simply contains no default-carrying read)."""
    path = os.path.join(RUNNER_DIR, "run.py")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    assert 'os.environ.get("MODEL_EVAL_LOCAL_BASE_URL")' in text
    assert not ENV_READ_WITH_DEFAULT.search(
        'os.environ.get("MODEL_EVAL_LOCAL_BASE_URL")')


# --------------------------------------------------------------------------- #
# Both former duplicate sites now import rather than define.
# --------------------------------------------------------------------------- #
def test_run_py_and_probe_endpoints_py_import_the_shared_module():
    for name in ("run.py", "probe_endpoints.py"):
        with open(os.path.join(RUNNER_DIR, name), "r", encoding="utf-8") as f:
            text = f.read()
        assert "import local_endpoint" in text, f"{name} does not import local_endpoint"
        assert "local_endpoint.get_local_base_url()" in text, (
            f"{name} does not derive LOCAL_BASE_URL from local_endpoint")

"""test_local_base_url_pin.py -- issue #5: LOCAL_BASE_URL / LOCAL_PLACEHOLDER_TOKEN
must be defined in exactly one place.

Before local_endpoint.py existed, run.py and probe_endpoints.py each carried a
byte-identical copy of the studio/local-family endpoint literal, with nothing
pinning the two copies equal. Two literals that happen to agree today can drift
silently tomorrow -- an edit to one copy left unmade in the other means a probe
certifies one endpoint while a run dispatches against a different one, and
nothing before this test would have said so.

This is a REPO-WIDE grep, not a check of the two files named in the issue: the
property being pinned is "the literal exists exactly once", and that claim is
false the moment a third file reintroduces it too, not only when run.py or
probe_endpoints.py specifically regress. The grep pattern is deliberately the
full assignment/call, default value included, so it lands on definition sites
only -- run.py:1048 asks os.environ.get("MODEL_EVAL_LOCAL_BASE_URL") with no
default, for an unrelated purpose (labelling whether a run's endpoint was an
override or the default), and must not be counted as a second definition.

Positive control (run by hand, not committed): temporarily add a second literal
`os.environ.get("MODEL_EVAL_LOCAL_BASE_URL", "http://localhost:1234")` anywhere
under runner/, rerun this file, watch test_exactly_one_base_url_definition_site
fail, then revert. See the PR body for the paste of both runs.
"""
import glob
import os

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_URL_DEFINITION = (
    'os.environ.get("MODEL_EVAL_LOCAL_BASE_URL", "http://localhost:1234")'
)
TOKEN_DEFINITION = 'LOCAL_PLACEHOLDER_TOKEN = "sk-local-lmstudio-unused"'


def _runner_py_files():
    """Every runner/*.py file, subjects named so a zero-file grep cannot pass
    silently (harness #24: a gate that inspected zero subjects has not passed)."""
    files = sorted(glob.glob(os.path.join(RUNNER_DIR, "*.py")))
    assert files, "found no runner/*.py files -- RUNNER_DIR is wrong"
    return files


def _sites(literal):
    hits = []
    for path in _runner_py_files():
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if literal in line:
                    hits.append(f"{os.path.basename(path)}:{lineno}")
    return hits


# --------------------------------------------------------------------------- #
# Control arm: both literals must be findable at all, or the pattern is wrong.
# --------------------------------------------------------------------------- #
def test_control_arm_the_grep_finds_something():
    """If this fails, the literal strings above no longer match the real code
    and every assertion below would pass for the wrong reason (nothing found)."""
    assert _sites(BASE_URL_DEFINITION), "grep pattern found zero base-url sites"
    assert _sites(TOKEN_DEFINITION), "grep pattern found zero token sites"


# --------------------------------------------------------------------------- #
# The pin.
# --------------------------------------------------------------------------- #
def test_exactly_one_base_url_definition_site():
    sites = _sites(BASE_URL_DEFINITION)
    assert sites == ["local_endpoint.py:45"], (
        "LOCAL_BASE_URL's definition must live in exactly one place "
        f"(local_endpoint.py); found: {sites}")


def test_exactly_one_placeholder_token_definition_site():
    sites = _sites(TOKEN_DEFINITION)
    assert sites == ["local_endpoint.py:37"], (
        "LOCAL_PLACEHOLDER_TOKEN's definition must live in exactly one place "
        f"(local_endpoint.py); found: {sites}")


# --------------------------------------------------------------------------- #
# The unrelated os.environ.get call this test must NOT count.
# --------------------------------------------------------------------------- #
def test_the_no_default_env_read_in_run_py_is_not_conflated_with_a_definition():
    """run.py reads MODEL_EVAL_LOCAL_BASE_URL a second time, with no default
    argument, purely to label a run's endpoint as an override vs the default
    (invocation_provenance). That is a distinct fact from defining the
    constant, and the grep pattern above (default value included) must not
    match it -- if it ever did, the two tests above would wrongly fail the day
    that unrelated line is touched."""
    with open(os.path.join(RUNNER_DIR, "run.py"), "r", encoding="utf-8") as f:
        text = f.read()
    assert 'os.environ.get("MODEL_EVAL_LOCAL_BASE_URL")' in text
    assert BASE_URL_DEFINITION not in text


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

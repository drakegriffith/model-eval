"""test_local_endpoint_refusal.py -- a gated local run may not be served from
somewhere the registry row never measured.

THE OTHER HALF of verifier finding 2. The previous commit made the endpoint
VISIBLE: every row now records serving_endpoint and endpoint_source, so two rows
produced against two servers are no longer indistinguishable. Visibility is not
enough on its own, because it only helps a reader who already suspects something.

The row for glm-4.7 pins parallel=1, context_length=131072 and a prefill band of
57-71 tok/s. Those are measurements OF ONE MACHINE -- the Mac Studio's LM Studio
on loopback. Pointed at another host, every one of those numbers is a claim
about a box nobody probed, while the row keeps asserting them. The gate cannot
catch it: the declared serving config still matches the row, because the endpoint
was never one of the pinned fields.

So a gated run whose endpoint is not loopback is refused, before dispatch, with
exit 2. This also closes the concrete complaint in issue #7 ("MODEL_EVAL_LOCAL_BASE_URL
accepts non-loopback URLs").

WHAT IS NOT REFUSED, deliberately: the override itself. Changing the port, or
pointing at 127.0.0.1 explicitly, is how the local family is meant to be used and
how the tests above exercise it. What is refused is leaving the machine the row
describes.

No model is invoked anywhere in this file.
"""
import importlib
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(RUNNER_DIR)
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

GOOD_SERVING = {"parallel": 1, "context_length": 131072, "max_tokens": 8192,
                "temperature": 0, "seed": 42}


def write_config(tmp_path, model="glm-4.7-local", driver="claude-code"):
    lines = ["defaults:", "  timeout_t1_t2_s: 1200", "  seed: 1337", "", "serving:"]
    for key, value in GOOD_SERVING.items():
        lines.append(f"  {key}: {value}")
    lines += ["", "sweeps:", "  - name: endpointtest",
              f"    driver: {driver}", "    harness: false", "    reps: [1]",
              "    tasks: [t2-py-a]", "    configs:",
              f"      - {{model: {model}, effort: high}}"]
    path = tmp_path / "endpoint.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def run_runner(config, tmp_path, base_url=None):
    env = dict(os.environ)
    if base_url is None:
        env.pop("MODEL_EVAL_LOCAL_BASE_URL", None)
    else:
        env["MODEL_EVAL_LOCAL_BASE_URL"] = base_url
    return subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "run.py"),
         "--config", config, "--dry-run",
         "--results", str(tmp_path / "results.jsonl"),
         "--scratch", str(tmp_path / "scratch")],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=120)


@pytest.fixture(autouse=True)
def restore_module():
    yield
    importlib.reload(runner)


# --------------------------------------------------------------------------- #
# The predicate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url", [
    "http://localhost:1234",
    "http://127.0.0.1:1234",
    "http://127.0.0.1:9999",
    "http://[::1]:1234",
])
def test_loopback_endpoints_are_accepted(url):
    """The positive control. A refusal that fired on everything would look
    identical to a working check, and would break the local family outright."""
    assert runner.is_loopback_endpoint(url) is True


@pytest.mark.parametrize("url", [
    "http://evil.example:9999",
    "http://192.168.1.50:1234",
    "https://someone-elses-studio.example",
    "http://10.0.0.7:1234",
])
def test_non_loopback_endpoints_are_rejected(url):
    assert runner.is_loopback_endpoint(url) is False


def test_an_unparseable_endpoint_is_not_treated_as_loopback():
    """Fail closed. A URL this code cannot read is not a URL it may vouch for."""
    assert runner.is_loopback_endpoint("not a url at all") is False
    assert runner.is_loopback_endpoint("") is False


# --------------------------------------------------------------------------- #
# The refusal, through the runner's own entry
# All local-family models, per the convention in test_local_family.py:36,56.
# qwen3-coder-next-local is the case that exposed the defect: it is local family
# and has NO serving row, and the refusal was telling it about a row it does not
# have. qwen3.6-35b-a3b-local, added after that fix, has the same gap: it is
# local family with no serving row of its own either.
LOCAL_MODELS = ["glm-4.7-local", "qwen3-coder-next-local", "qwen3.6-35b-a3b-local"]


@pytest.mark.parametrize("model", LOCAL_MODELS)
def test_a_local_run_pointed_off_box_is_refused_before_dispatch(tmp_path, model):
    """The verifier's demonstration, now a refusal, for every local model. The
    serving gate alone could not catch this: the declared config still matches
    the row, because the endpoint was never a pinned field."""
    proc = run_runner(write_config(tmp_path, model=model), tmp_path,
                      base_url="http://evil.example:9999")

    assert proc.returncode == 2, proc.stdout
    assert "config rejected" in proc.stdout
    assert "evil.example" in proc.stdout
    assert "MODEL_EVAL_LOCAL_BASE_URL" in proc.stdout


def test_the_gated_refusal_names_the_measurements_that_stop_being_true(tmp_path):
    """glm-4.7 HAS a row, so the reason can cite it. Naming the measurements is
    what stops the next reader adding an opt-out."""
    proc = run_runner(write_config(tmp_path, model="glm-4.7-local"), tmp_path,
                      base_url="http://192.168.1.50:1234")

    assert proc.returncode == 2
    assert "prefill" in proc.stdout or "measured" in proc.stdout
    assert "registry row" in proc.stdout


def test_the_ungated_refusal_does_not_invent_a_registry_row(tmp_path):
    """THE DEFECT. qwen3-coder-next-local is local family with no serving row,
    and the refusal told it 'Its registry row pins parallel, context_length and a
    measured 57-71 tok/s prefill band' -- a row and a measurement that do not
    exist for it. Asserting a measurement nobody took is the exact failure this
    whole branch exists to prevent, so the refusal must not commit it while
    preventing it."""
    proc = run_runner(write_config(tmp_path, model="qwen3-coder-next-local"),
                      tmp_path, base_url="http://evil.example:9999")

    assert proc.returncode == 2, proc.stdout
    assert "57-71" not in proc.stdout, (
        "the refusal quoted glm-4.7's measured prefill band at a model that has "
        "no row and no such measurement")
    assert "registry row pins" not in proc.stdout
    # It still has to say something TRUE about why it refused.
    assert "no serving registry row" in proc.stdout


def test_the_default_loopback_endpoint_still_dispatches(tmp_path):
    """The control that matters most: this must not break the normal path."""
    proc = run_runner(write_config(tmp_path), tmp_path, base_url=None)

    assert proc.returncode == 0, proc.stdout
    assert "gated=1" in proc.stdout


def test_an_explicit_loopback_override_still_dispatches(tmp_path):
    """Changing the PORT is how the local family is meant to be used. The
    refusal is about leaving the machine, not about the override existing."""
    proc = run_runner(write_config(tmp_path), tmp_path,
                      base_url="http://127.0.0.1:9999")

    assert proc.returncode == 0, proc.stdout


def test_a_non_local_family_run_is_unaffected(tmp_path):
    """claude-sonnet-5 has no endpoint override and no registry row; a stray
    MODEL_EVAL_LOCAL_BASE_URL must not refuse it."""
    proc = run_runner(write_config(tmp_path, model="claude-sonnet-5",
                                   driver="claude-code"),
                      tmp_path, base_url="http://evil.example:9999")

    assert proc.returncode == 0, proc.stdout


# --------------------------------------------------------------------------- #
# The host match itself (verifier finding 4)
# --------------------------------------------------------------------------- #
# `startswith("127.")` is a STRING test on a value that is not a string quantity.
# It accepts `127.0.0.1.evil.com`, a perfectly ordinary DNS name that resolves
# wherever its owner points it -- so the refusal could be walked straight past by
# naming a host after the thing it was checking for.
#
# The fix is to ask the ipaddress module, which parses rather than pattern-matches,
# and to refuse anything it cannot parse. A hostname that is not a literal address
# is not one this code can vouch for, because what it resolves to is not knowable
# here -- `localhost` is allowed as the one named exception the platform pins.
DECEPTIVE_HOSTS = [
    "http://127.0.0.1.evil.com:1234",
    "http://127.0.0.1.example.org",
    "http://localhost.evil.com:1234",
    "http://127-0-0-1.evil.com",
    "http://evil.com/?h=127.0.0.1",
    "http://evil.com#127.0.0.1",
]


@pytest.mark.parametrize("url", DECEPTIVE_HOSTS)
def test_a_host_merely_named_after_loopback_is_refused(url):
    """The finding. Each of these is a DNS name its owner controls, and the old
    prefix test waved the first four through."""
    assert runner.is_loopback_endpoint(url) is False


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1234",
    "http://127.0.0.2:1234",     # the whole 127/8 block is loopback
    "http://127.255.255.254",
    "http://[::1]:1234",
    "http://localhost:1234",
])
def test_real_loopback_forms_are_still_accepted(url):
    """The positive control, widened: 127/8 is loopback in its entirety, not
    just 127.0.0.1, and refusing the rest would break a legitimate setup."""
    assert runner.is_loopback_endpoint(url) is True


def test_a_non_loopback_literal_is_refused():
    assert runner.is_loopback_endpoint("http://10.0.0.7:1234") is False
    assert runner.is_loopback_endpoint("http://192.168.1.50:1234") is False


def test_the_check_parses_rather_than_pattern_matches():
    """Stated as a property so the next person does not reintroduce a string
    test: the decision must survive forms that are textually unlike `127.` but
    numerically loopback, and reject forms that look like it and are not."""
    # What the parser does with shorthand forms is a CHOICE, not an accident:
    # `127.1` resolves to 127.0.0.1 through most resolvers, but Python's
    # ipaddress refuses it as a literal, so this refuses it too. That is the
    # fail-closed direction -- a form this code cannot parse is one it may not
    # vouch for -- and it costs a user nothing but writing the address in full.
    assert runner.is_loopback_endpoint("http://127.1:1234") is False
    assert runner.is_loopback_endpoint("http://0177.0.0.1:1234") is False
    # The load-bearing pair: numerically loopback vs merely named after it.
    assert runner.is_loopback_endpoint("http://127.0.0.1:1234") is True
    assert runner.is_loopback_endpoint("http://127.0.0.1.evil.com:1234") is False

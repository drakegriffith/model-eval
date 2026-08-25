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
# --------------------------------------------------------------------------- #
def test_a_gated_run_pointed_off_box_is_refused_before_dispatch(tmp_path):
    """The verifier's demonstration, now a refusal. The gate alone could not
    catch this: the declared serving config still matches the row, because the
    endpoint was never a pinned field."""
    proc = run_runner(write_config(tmp_path), tmp_path,
                      base_url="http://evil.example:9999")

    assert proc.returncode == 2, proc.stdout
    assert "config rejected" in proc.stdout
    assert "evil.example" in proc.stdout
    assert "MODEL_EVAL_LOCAL_BASE_URL" in proc.stdout


def test_the_refusal_says_why_the_row_cannot_vouch_for_another_host(tmp_path):
    proc = run_runner(write_config(tmp_path), tmp_path,
                      base_url="http://192.168.1.50:1234")

    assert proc.returncode == 2
    # The reason has to name the measurements that stop being true, or the next
    # reader will just add an opt-out.
    assert "prefill" in proc.stdout or "measured" in proc.stdout


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

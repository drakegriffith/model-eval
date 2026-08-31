"""test_child_env_allowlist.py -- issue #14 finding F1: the model's environment
was SUBTRACTIVE, so the parent session leaked into every arm.

WHAT WAS OPEN. `run_cli` built the child environment as

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

which is a claim about *everything that exists*: every name the author did not
think of rode into the child. Probed on this machine, the leak was not
hypothetical -- eleven CLAUDE_* names came through, among them CLAUDE_EFFORT,
plus ANTHROPIC_BASE_URL (re-points an arm at another endpoint), ANTHROPIC_MODEL
and ANTHROPIC_SMALL_FAST_MODEL (change which model answers, under the row's own
label), CLAUDE_CODE_MAX_OUTPUT_TOKENS and MAX_THINKING_TOKENS (change the
serving config the row is reported under, after serving_registry's gate has
already compared the DECLARED config and passed), and XDG_CONFIG_HOME /
XDG_DATA_HOME (re-point config discovery even after blocker 2 scoped HOME).

CLAUDE_EFFORT is the one that ruins the experiment rather than merely
threatening it: `CLAUDE_EFFORT=high` live in the parent is live in every arm, so
an effort ladder measures one effort five times while its rows carry five
different `effort` labels.

WHAT IS ASSERTED HERE. The env is built by ALLOWLIST -- the shape this repo
already uses at product/gauntlet_playground/executor.py and asserts at
runner/tests/test_product_executor.py. The expected name set is restated here as
a literal rather than imported from run.py (harness rule #5): a checker that
learns the answer from the module it checks cannot fail when that module is
wrong.

No model is invoked anywhere in this file. A stand-in probe prints its own
environ, per the precedent in test_home_isolation.py and test_local_family.py:
which variables a subprocess inherits is decided by run_cli, not by which binary
asks, so a probe proves it at zero token cost.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402

ENV_PROBE = "import json, os, sys\njson.dump(dict(os.environ), sys.stdout)\n"

# The allowlist, restated independently of run.py. These are the names a child
# process needs to find its interpreter, its home and its locale; not one of them
# can carry a credential, an endpoint, a model choice or a harness.
EXPECTED_ALLOWLIST = {"PATH", "HOME", "SHELL", "USER", "LOGNAME",
                      "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR"}

# What run_cli sets on purpose, on top of the allowlist. Anything in the child's
# environment outside ALLOWLIST | SET_BY_RUN_CLI | the family's own extras is a
# leak, and the exact-set assertion below is what says so.
SET_BY_RUN_CLI = {"GAUNTLET_TASK_DIR", "TMPDIR", "TMP", "TEMP",
                  "CODEX_HOME", "HOME", "CLAUDE_CONFIG_DIR"}

ANTHROPIC_EXTRAS = {"ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}

# issue #40: the local-family child's stream-idle and API timeout knobs, set
# deliberately by run_cli from the serving row (or the run's own wall-clock
# cap) -- never inherited, and not one of the ANTHROPIC_* names above because
# they are set for "local" only, not "kimi".
LOCAL_TIMEOUT_EXTRAS = {"CLAUDE_STREAM_IDLE_TIMEOUT_MS", "API_TIMEOUT_MS"}

# The claude family's subscription credential, injected by run_cli from a
# secrets file OUTSIDE every repo -- never inherited. Family-scoped for the same
# reason LOCAL_TIMEOUT_EXTRAS is: a local or kimi arm that grew this name would
# be carrying a claude credential it has no use for, and the exact-set assertion
# below is what would catch that.
#
# Present only when the operator has actually minted a token, so these tests
# must pass on a host with no secrets file and on one with -- which is why the
# name is PERMITTED here rather than REQUIRED.
CLAUDE_AUTH_EXTRAS = {"CLAUDE_CODE_OAUTH_TOKEN"}

# Added by the PLATFORM, not inherited: CoreFoundation stamps this into every
# process it initialises on macOS. Verified rather than assumed -- launching a
# child with an explicit `env={"PATH": "/usr/bin"}` still shows it, so it does
# not travel through run_cli's dict and stripping the allowlist cannot remove
# it. Named here so the exact-set assertion below stays exact instead of being
# loosened to a prefix rule that would also wave through a real leak.
OS_INJECTED = {"__CF_USER_TEXT_ENCODING"}

# The contaminants, injected into the RUNNER's own environment by the fixture
# below, so a green run inspects a real subject instead of depending on whatever
# happened to be exported in the shell that launched pytest.
CONTAMINANTS = {
    "ANTHROPIC_BASE_URL": "http://leaked-endpoint:9999",
    "ANTHROPIC_AUTH_TOKEN": "leaked-subscription-token",
    "ANTHROPIC_API_KEY": "leaked-api-key",
    "ANTHROPIC_MODEL": "some-other-model",
    "ANTHROPIC_SMALL_FAST_MODEL": "some-other-small-model",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "512",
    "MAX_THINKING_TOKENS": "31999",
    "XDG_CONFIG_HOME": "/leaked/xdg/config",
    "XDG_DATA_HOME": "/leaked/xdg/data",
    "CLAUDECODE": "1",
    "CLAUDE_EFFORT": "high",
    "CLAUDE_CODE_ENTRYPOINT": "cli",
    "OPENAI_API_KEY": "leaked-openai-key",
    "AWS_SECRET_ACCESS_KEY": "should-not-travel",
    "GH_TOKEN": "should-not-travel-either",
    # The claude arm injects a token of this name from a secrets file. A parent
    # value must never be what arrives: without this contaminant the injection
    # and a leak are indistinguishable, because both end with the name present.
    "CLAUDE_CODE_OAUTH_TOKEN": "leaked-parent-oauth-token",
}

# Every family that rides a binary reading these names, plus None (the mock
# path), because a path that skips the allowlist is a path where the leak
# comes back.
ARMS = [None, "claude-sonnet-5", "glm-4.7-local", "gpt-5.6-sol", "kimi-k3"]


@pytest.fixture
def contaminated_parent(monkeypatch):
    """The parent session an operator really launches a sweep from."""
    for name, value in CONTAMINANTS.items():
        monkeypatch.setenv(name, value)
    # Positive control: the leak vector must be PRESENT in the parent, or every
    # green assertion below would only be proving the variable was never set.
    assert os.environ["CLAUDE_EFFORT"] == "high"
    assert os.environ["ANTHROPIC_BASE_URL"] == "http://leaked-endpoint:9999"
    return CONTAMINANTS


@pytest.fixture
def repo(tmp_path):
    """run.py's real layout in miniature (mirrors test_home_isolation.py)."""
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-child-env"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do the task", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 1\n",
                                        encoding="utf-8")
    scratch = root / ".scratch" / "childenv--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=False)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


def probe_env(repo, monkeypatch, model=None, **kw):
    """The env of a subprocess launched through the REAL run_cli.

    Unsealed: the filesystem seal is a separate mechanism with a documented
    opt-out, and an isolation that only holds while a second mechanism is on is
    a coincidence rather than a property of the arm (test_home_isolation.py's
    argument, and it applies here unchanged).
    """
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    # kimi is the one family that reads a key file; stub it so the arm can be
    # probed on a machine without the key rather than silently skipped.
    monkeypatch.setattr(runner, "load_kimi_key", lambda: "sk-test-kimi-key")
    out, reason, _wall = runner.run_cli(
        [sys.executable, "-c", ENV_PROBE], repo["scratch"], 60,
        repo["task_dir"], model=model, **kw)
    assert reason == "ok", f"probe did not exit cleanly ({reason}): {out!r}"
    return json.loads(out)


# --------------------------------------------------------------------------- #
# The negative control: the exact leak that motivated the fix.
# --------------------------------------------------------------------------- #
def test_the_parent_sessions_effort_does_not_reach_the_arm(
        contaminated_parent, repo, monkeypatch):
    """THE motivating leak. A sweep launched from a Claude Code session exports
    CLAUDE_EFFORT; inherited, every rung of an effort ladder runs at the parent's
    effort while wearing its own label, so the ladder is fitted over a constant."""
    env = probe_env(repo, monkeypatch, model="glm-4.7-local")

    assert "CLAUDE_EFFORT" not in env, (
        f"CLAUDE_EFFORT={env.get('CLAUDE_EFFORT')!r} reached the model under "
        f"test; every arm of an effort ladder would run at that effort")
    assert "CLAUDECODE" not in env


@pytest.mark.parametrize("model", ARMS)
def test_no_arm_inherits_a_claude_or_xdg_variable(
        contaminated_parent, repo, monkeypatch, model):
    """Asserted over families, not over one: every family here rides a binary
    that reads these names."""
    env = probe_env(repo, monkeypatch, model=model)

    # CLAUDE_STREAM_IDLE_TIMEOUT_MS is deliberately set by run_cli only on the
    # local family (issue #40); excluding it on every arm would let a leaked
    # parent value ride into a non-local arm unnoticed.
    excused = {"CLAUDE_CONFIG_DIR"}
    if model is not None and runner.model_family(model) == "local":
        excused.add("CLAUDE_STREAM_IDLE_TIMEOUT_MS")
    if model is not None and runner.model_family(model) == "claude":
        # Excused as a NAME, never as a value: run_cli sets this deliberately
        # from the secrets file, so its presence is correct, but the parent's
        # value arriving under it would be exactly the leak this test exists
        # for. Assert the value first, then stop treating the name as a leak.
        assert env.get("CLAUDE_CODE_OAUTH_TOKEN") != \
            CONTAMINANTS["CLAUDE_CODE_OAUTH_TOKEN"], (
                f"{model}: the PARENT's oauth token travelled into the arm")
        excused.add("CLAUDE_CODE_OAUTH_TOKEN")

    leaked = sorted(k for k in env
                    if k.startswith(("CLAUDE_", "XDG_", "CLAUDECODE"))
                    and k not in excused)
    assert leaked == [], f"{model}: leaked {leaked} from the parent session"


@pytest.mark.parametrize("model", ARMS)
def test_no_arm_inherits_a_credential(contaminated_parent, repo, monkeypatch, model):
    """ANTHROPIC_API_KEY was popped and ANTHROPIC_AUTH_TOKEN was not, which is
    the failure mode a deny list has: the two names do the same job and only one
    was on the list."""
    env = probe_env(repo, monkeypatch, model=model)

    for name in ("OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "GH_TOKEN"):
        assert name not in env, f"{model}: {name} travelled into the arm"
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        assert env.get(name) != CONTAMINANTS[name], (
            f"{model}: {name} carries the PARENT's credential")


@pytest.mark.parametrize("model", ARMS)
def test_the_child_environment_is_exactly_the_allowlist_plus_declared_additions(
        contaminated_parent, repo, monkeypatch, model):
    """The exact-set assertion. A subset assertion is what let ten names through:
    it can only catch a leak somebody already thought to name."""
    env = probe_env(repo, monkeypatch, model=model)

    permitted = EXPECTED_ALLOWLIST | SET_BY_RUN_CLI | OS_INJECTED
    if model is not None and runner.model_family(model) in ("kimi", "local"):
        permitted |= ANTHROPIC_EXTRAS
    if model is not None and runner.model_family(model) == "local":
        permitted |= LOCAL_TIMEOUT_EXTRAS
    if model is not None and runner.model_family(model) == "claude":
        permitted |= CLAUDE_AUTH_EXTRAS
    unexpected = sorted(set(env) - permitted)
    assert unexpected == [], (
        f"{model}: names in the child env that nothing declared: {unexpected}")


def test_the_serving_numbers_the_registry_pins_cannot_be_overridden_from_outside(
        contaminated_parent, repo, monkeypatch):
    """The gate/env seam. serving_registry compares a run's DECLARED serving
    config against the row and passes; these two variables change the ACTUAL one
    afterwards, from outside anything the gate can see. Inherited, the gate is
    decorative."""
    env = probe_env(repo, monkeypatch, model="glm-4.7-local")

    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in env
    assert "MAX_THINKING_TOKENS" not in env


def test_the_claude_arm_keeps_the_endpoint_it_was_labelled_with(
        contaminated_parent, repo, monkeypatch):
    """The claude control arm sets no endpoint of its own, so an ambient
    ANTHROPIC_BASE_URL silently serves the control from somewhere else and the
    row does not say so. Note that test_local_family.py's equivalent has to
    `delenv` the variable first; this one deliberately does not."""
    env = probe_env(repo, monkeypatch, model="claude-sonnet-5")

    assert "ANTHROPIC_BASE_URL" not in env


@pytest.mark.parametrize("model,expected_url", [
    ("glm-4.7-local", None),   # filled in below from run.LOCAL_BASE_URL
    ("kimi-k3", None),
])
def test_the_family_branches_still_set_their_own_endpoint(
        contaminated_parent, repo, monkeypatch, model, expected_url):
    """The other half of the fix: stripping the environment must not strip what
    the family branch legitimately puts back. Without this, every assertion above
    is satisfied by an arm that cannot start."""
    want = (runner.LOCAL_BASE_URL if model == "glm-4.7-local"
            else runner.MOONSHOT_ANTHROPIC_URL)

    env = probe_env(repo, monkeypatch, model=model)

    assert env["ANTHROPIC_BASE_URL"] == want
    assert env["ANTHROPIC_API_KEY"]
    assert env["ANTHROPIC_AUTH_TOKEN"]


# --------------------------------------------------------------------------- #
# Positive controls -- what must SURVIVE. Every absence assertion above is
# satisfied by returning an empty dict, which would break every run in the sweep
# while turning this file green.
# --------------------------------------------------------------------------- #
def test_the_child_still_gets_what_it_needs_to_run(
        contaminated_parent, repo, monkeypatch):
    env = probe_env(repo, monkeypatch, model="glm-4.7-local")

    assert env["PATH"]
    assert env["HOME"]
    assert env["TMPDIR"]
    assert env["GAUNTLET_TASK_DIR"]
    assert env["CODEX_HOME"]
    assert env["CLAUDE_CONFIG_DIR"]


def test_child_env_copies_present_names_and_invents_absent_ones():
    """Copied, not defaulted. A synthesised HOME would point the model at a
    directory nobody created, and a synthesised locale is a small lie about the
    conditions the row was produced under."""
    env = runner.child_env({"PATH": "/usr/bin", "HOME": "/home/x",
                            "CLAUDE_EFFORT": "high"})

    assert env == {"PATH": "/usr/bin", "HOME": "/home/x"}


def test_the_allowlist_carries_nothing_that_can_redirect_or_resize_a_run():
    """The list is the whole argument, so it is asserted directly and not only
    through its effects: a name added later that should not be there fails here,
    in the one place a reviewer will look."""
    inspected = 0
    for name in runner.CHILD_ENV_ALLOWLIST:
        inspected += 1
        assert not name.startswith(("ANTHROPIC", "CLAUDE", "XDG_", "OPENAI",
                                    "AWS_", "GH_", "MAX_")), (
            f"{name!r} is on the child allowlist; names in these families carry "
            f"credentials, endpoints, model choices or harness settings and must "
            f"be set by the family branch or not at all")
    # Silence is not evidence: a refactor that empties the tuple must not pass
    # this test by inspecting nothing.
    assert inspected == len(EXPECTED_ALLOWLIST) == 10
    assert set(runner.CHILD_ENV_ALLOWLIST) == EXPECTED_ALLOWLIST

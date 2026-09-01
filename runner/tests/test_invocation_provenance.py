"""test_invocation_provenance.py -- the row records WHAT SERVED IT.

TWO VERIFIER FINDINGS, one concern.

Finding 2 (MEDIUM). `MODEL_EVAL_LOCAL_BASE_URL` is read at module scope from the
parent environment and injected into the child as `ANTHROPIC_BASE_URL`. The
allowlist added in issue #14 F1 does not stop it, and cannot: the variable is not
inherited, it is READ by the runner and then SET on the child deliberately. The
verifier demonstrated `http://evil.example:9999` reaching the child that way.

That is not a hole in the allowlist -- an override that a human sets on purpose
is a feature, and the local family exists precisely because the endpoint is not
fixed. What was wrong is that it was INVISIBLE. A row said `glm-4.7-local` and
carried nothing about which server answered, so two rows served by two different
endpoints were indistinguishable in the corpus forever after.

Finding 4 (LOW). The same gap one level down: `build_cli_cmd` emits the bare
argv name `claude`, so which binary answered was decided by the parent shell's
PATH and recorded nowhere. Two rows produced by two Claude Code versions -- or by
a shim earlier on PATH -- look identical.

The fix for both is the same and it is not a refusal: RECORD IT. A row now
carries the endpoint that served it, the SOURCE of that endpoint, the path of
the key file consulted (never its contents), and the resolved absolute path of
the binary that ran.

WHAT IS DELIBERATELY NOT DONE HERE: nothing is forbidden by this commit. The
override still works, because the pre-registration's serving stack is a human's
to set. Making it visible is what lets the gate and the pre-flight say anything
true about it -- and the loopback refusal that builds on this stamp is the next
commit, kept separate because "record it" and "refuse it" are different claims
with different blast radii.

No model is invoked anywhere in this file.
"""
import importlib
import os
import shutil
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402


@pytest.fixture(autouse=True)
def restore_module():
    """LOCAL_BASE_URL is read at import time, so any test that changes it has to
    put the module back for the tests that follow."""
    yield
    importlib.reload(runner)


# --------------------------------------------------------------------------- #
# The endpoint that served the row
# --------------------------------------------------------------------------- #
def test_a_local_row_records_the_endpoint_that_served_it():
    prov = runner.invocation_provenance("glm-4.7-local")

    assert prov["serving_endpoint"] == runner.LOCAL_BASE_URL
    assert prov["endpoint_source"] == "default"


def test_an_overridden_endpoint_is_recorded_as_overridden(monkeypatch):
    """The verifier's exact demonstration. The override still works -- what
    changes is that the row now says it happened, and names the variable that
    did it, so a reader can tell these rows from the ones served on loopback."""
    monkeypatch.setenv("MODEL_EVAL_LOCAL_BASE_URL", "http://evil.example:9999")
    importlib.reload(runner)

    prov = runner.invocation_provenance("glm-4.7-local")

    assert prov["serving_endpoint"] == "http://evil.example:9999"
    assert prov["endpoint_source"] == "MODEL_EVAL_LOCAL_BASE_URL"


def test_a_kimi_row_records_moonshot_as_its_endpoint():
    prov = runner.invocation_provenance("kimi-k3")

    assert prov["serving_endpoint"] == runner.MOONSHOT_ANTHROPIC_URL
    assert prov["endpoint_source"] == "moonshot"


def test_a_subscription_row_records_no_endpoint_override():
    """claude and codex run against their vendors' own defaults -- the runner
    sets no base URL for them, and after F1 no ambient one can reach them
    either. None is the honest value; a synthesised URL would assert a fact the
    runner does not have."""
    for model in ("claude-sonnet-5", "gpt-5.6-sol"):
        prov = runner.invocation_provenance(model)

        assert prov["serving_endpoint"] is None
        assert prov["endpoint_source"] == "vendor_default"


# --------------------------------------------------------------------------- #
# The key SOURCE -- path only, never contents
# --------------------------------------------------------------------------- #
def test_a_kimi_row_records_which_key_file_was_consulted():
    prov = runner.invocation_provenance("kimi-k3")

    assert prov["key_source"] == runner.KIMI_KEY_FILE
    assert prov["key_source"].endswith(".env")


def test_the_key_file_contents_never_reach_the_row(tmp_path, monkeypatch):
    """The one assertion that matters most in this file. A provenance field that
    solved the visibility problem by writing the secret into an append-only
    corpus would be a far worse bug than the one it fixed."""
    key_file = tmp_path / "kimi.env"
    key_file.write_text("MOONSHOT_API_KEY=sk-super-secret-value\n", encoding="utf-8")
    monkeypatch.setenv("GAUNTLET_KIMI_KEY_FILE", str(key_file))
    importlib.reload(runner)

    prov = runner.invocation_provenance("kimi-k3")

    assert "sk-super-secret-value" not in repr(prov)
    assert prov["key_source"] == str(key_file)


def test_a_local_row_records_that_its_token_is_a_placeholder():
    """LM Studio checks nothing; the token exists only to satisfy the claude
    binary's own precondition. Saying `placeholder` keeps a reader from hunting
    for an account that does not exist."""
    prov = runner.invocation_provenance("glm-4.7-local")

    assert prov["key_source"] == "placeholder"


def test_a_subscription_row_records_no_key_file(tmp_path, monkeypatch):
    """Still true of a genuine subscription row -- which now has to be STATED.

    This test used to pass on a hardcoded constant, so it asserted nothing about
    the host it ran on. Once the claude arm could authenticate from a secrets
    file, that constant became a contradiction: a row could read
    auth_source=api_key beside key_source=subscription. The subject is now
    pinned, so this asserts the subscription case instead of the constant.
    """
    monkeypatch.setattr(runner, "CLAUDE_TOKEN_FILE",
                        str(tmp_path / "no-secrets-here.env"))
    prov = runner.invocation_provenance("claude-sonnet-5")

    assert prov["key_source"] == "subscription"


def test_a_secrets_file_row_records_the_file_not_the_word(tmp_path, monkeypatch):
    """The other half, which is the case that actually regressed."""
    f = tmp_path / "claude.env"
    f.write_text("ANTHROPIC_API_KEY=sk-ant-api03-TESTONLY\n", encoding="utf-8")
    monkeypatch.setattr(runner, "CLAUDE_TOKEN_FILE", str(f))
    prov = runner.invocation_provenance("claude-sonnet-5")

    assert prov["key_source"] == str(f)
    assert "sk-ant" not in prov["key_source"], "key_source leaked a credential"


# --------------------------------------------------------------------------- #
# Which BINARY answered (finding 4)
# --------------------------------------------------------------------------- #
def test_the_resolved_binary_path_is_recorded_not_the_bare_argv_name():
    """`build_cli_cmd` emits `claude`; which file that names is decided by the
    parent shell's PATH. Two rows produced by two versions, or by a shim earlier
    on PATH, are otherwise indistinguishable."""
    prov = runner.invocation_provenance("glm-4.7-local")

    assert prov["cli_binary"] == "claude"
    assert prov["cli_binary_path"] == shutil.which("claude")


def test_codex_family_resolves_its_own_binary():
    prov = runner.invocation_provenance("gpt-5.6-sol")

    assert prov["cli_binary"] == "codex"
    assert prov["cli_binary_path"] == shutil.which("codex")


def test_an_unresolvable_binary_is_none_rather_than_a_guess(monkeypatch):
    """Fail-closed on the record, not on the run: if the binary cannot be found
    on PATH, the row says so instead of recording the bare name as though it
    were a path."""
    monkeypatch.setattr(shutil, "which", lambda name: None)

    prov = runner.invocation_provenance("glm-4.7-local")

    assert prov["cli_binary_path"] is None
    assert prov["cli_binary"] == "claude"


# --------------------------------------------------------------------------- #
# It reaches the row
# --------------------------------------------------------------------------- #
def test_every_provenance_field_is_stamped_on_a_results_row(tmp_path):
    """Through the row-writing path the runner actually uses, not by calling the
    helper twice. A provenance helper nothing stamps is the zero-invokers
    failure again."""
    results = tmp_path / "results.jsonl"
    runner.record_structurally_impossible(
        {"run_id": "s--glm-4.7-local--high--bare--t1-py-a--r1", "sweep": "s",
         "model": "glm-4.7-local", "effort": "high", "harness": False,
         "harness_level": 5, "driver": "pi", "task": "t1-py-a", "rep": 1},
        "driver pi tops out at harness level 2", str(results))

    import json
    row = json.loads(results.read_text(encoding="utf-8").strip())

    for field in ("serving_endpoint", "endpoint_source", "key_source",
                  "cli_binary", "cli_binary_path"):
        assert field in row, f"{field} is not stamped on the row"


def test_the_provenance_fields_are_a_closed_set():
    """Silence is not evidence: assert the count, so a field quietly dropped
    from the helper fails here rather than going missing from the corpus."""
    prov = runner.invocation_provenance("glm-4.7-local")

    assert set(prov) == {"serving_endpoint", "endpoint_source", "key_source",
                         "cli_binary", "cli_binary_path"}

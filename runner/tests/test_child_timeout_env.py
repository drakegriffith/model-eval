"""test_child_timeout_env.py -- issue #40: claude-code's own client-side
stream-idle timer (measured empirically at ~200s) aborts a local-family turn on
silence alone, even while the server is still decoding, because run.py set
neither CLAUDE_STREAM_IDLE_TIMEOUT_MS nor API_TIMEOUT_MS for that child. All 5
stage-0 reps of glm-4.7-local x claude-code ended `cli_error` on this signature
(the LM Studio server log shows active decoding at every abort).

THE FIX. run_cli's local-family branch (run.py) now sets both env vars from
`local_family_client_timeout_ms(model, wall_clock_s)`: the serving row's own
`client_timeout_ms` (models.yaml, validated by serving_registry) if one is
recorded, else the run's own resolved wall-clock cap in ms -- which is always
at least as large, since the subprocess is killed there regardless, so the
client timer can never be the thing that ends the run first.

WHAT IS ASSERTED HERE, four claims:
  (a) a local-family row that DOES declare client_timeout_ms -> both env vars
      equal that value, not the wall clock.
  (b) a local-family row that does NOT declare it (or has no row at all) ->
      both env vars equal the wall-clock timeout passed to run_cli, in ms.
  (c) a non-local row (claude, kimi) -> neither env var is present at all.
  (d) serving_registry refuses a non-int or <= 0 client_timeout_ms, naming the
      offending row and the key, at load time.

No model is invoked anywhere in this file: (a)-(c) launch a stand-in probe
through the REAL run_cli, same precedent as test_local_family.py and
test_child_env_allowlist.py; (d) calls serving_registry directly.
"""
import json
import os
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNNER_DIR)
import run as runner  # noqa: E402
import serving_registry  # noqa: E402

ENV_PROBE = "import json, os, sys\njson.dump(dict(os.environ), sys.stdout)\n"

TIMEOUT_KEYS = ("CLAUDE_STREAM_IDLE_TIMEOUT_MS", "API_TIMEOUT_MS")


@pytest.fixture
def repo(tmp_path):
    """run.py's real layout in miniature (mirrors test_local_family.py's `repo`
    fixture): scratch and task dir share a root run_cli can resolve."""
    root = tmp_path / "model-gauntlet"
    task_dir = root / "tasks" / "t-child-timeout"
    (task_dir / "base").mkdir(parents=True)
    (task_dir / "base" / "README.md").write_text("solve it", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("do the task", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 1\n",
                                        encoding="utf-8")
    scratch = root / ".scratch" / "childtimeout--probe--r1"
    scratch.parent.mkdir(parents=True)
    runner.prepare_scratch(str(task_dir), str(scratch), harness=False)
    return {"root": str(root), "task_dir": str(task_dir), "scratch": str(scratch)}


def via_run_cli(repo, model, monkeypatch, timeout_s=30):
    """Launch the env probe through the REAL run_cli. GAUNTLET_NO_SANDBOX=1 for
    the same reason test_local_family.py runs unsealed: env injection is
    orthogonal to the filesystem seal, which is proven elsewhere."""
    monkeypatch.setattr(runner, "ROOT", repo["root"])
    monkeypatch.setenv("GAUNTLET_NO_SANDBOX", "1")
    monkeypatch.setattr(runner, "load_kimi_key", lambda: "sk-test-kimi-key")
    out, reason, _wall = runner.run_cli(
        [sys.executable, "-c", ENV_PROBE], repo["scratch"], timeout_s,
        repo["task_dir"], model=model)
    assert reason == "ok", f"probe did not exit cleanly ({reason}): {out!r}"
    return json.loads(out)


def stub_rows(monkeypatch, rows):
    """Point run.py's serving_registry lookups at a fixed, in-memory row list
    instead of the real runner/models.yaml -- so these tests assert against
    what THIS test declares, not against whatever a human has since measured
    and recorded on disk."""
    monkeypatch.setattr(serving_registry, "load_rows", lambda *a, **kw: rows)


# --------------------------------------------------------------------------- #
# (a) a serving row WITH client_timeout_ms wins over the wall clock
# --------------------------------------------------------------------------- #
def test_local_row_with_client_timeout_ms_wins_over_wall_clock(repo, monkeypatch):
    stub_rows(monkeypatch, [
        {"model": "glm-4.7", "driver": "claude-code", "client_timeout_ms": 654321},
    ])

    env = via_run_cli(repo, "glm-4.7-local", monkeypatch, timeout_s=30)

    assert env.get("CLAUDE_STREAM_IDLE_TIMEOUT_MS") == "654321"
    assert env.get("API_TIMEOUT_MS") == "654321"
    # Not the wall clock the row's own value was supposed to override.
    assert env.get("CLAUDE_STREAM_IDLE_TIMEOUT_MS") != str(30 * 1000)


# --------------------------------------------------------------------------- #
# (b) no declared value (row present but blank, or no row at all) -> wall clock
# --------------------------------------------------------------------------- #
def test_local_row_without_client_timeout_ms_falls_back_to_wall_clock(
        repo, monkeypatch):
    stub_rows(monkeypatch, [
        {"model": "glm-4.7", "driver": "claude-code", "client_timeout_ms": None},
    ])

    env = via_run_cli(repo, "glm-4.7-local", monkeypatch, timeout_s=45)

    expected = str(45 * 1000)
    assert env.get("CLAUDE_STREAM_IDLE_TIMEOUT_MS") == expected
    assert env.get("API_TIMEOUT_MS") == expected


def test_local_model_with_no_registry_row_at_all_falls_back_to_wall_clock(
        repo, monkeypatch):
    """models with no row -- fable, sol, everything predating the registry --
    pass through ungated (serving_registry's own documented behaviour); the
    timeout env must fail open to the wall clock, not raise."""
    stub_rows(monkeypatch, [])

    env = via_run_cli(repo, "qwen3-coder-next-local", monkeypatch, timeout_s=17)

    expected = str(17 * 1000)
    assert env.get("CLAUDE_STREAM_IDLE_TIMEOUT_MS") == expected
    assert env.get("API_TIMEOUT_MS") == expected


# --------------------------------------------------------------------------- #
# (c) a non-local row carries neither key
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model", ["claude-sonnet-5", "kimi-k3"])
def test_non_local_row_carries_neither_timeout_key(repo, monkeypatch, model):
    # A row exists for glm-4.7 in this stub, proving the keys' absence here is
    # about FAMILY, not about "no row was found for anything".
    stub_rows(monkeypatch, [
        {"model": "glm-4.7", "driver": "claude-code", "client_timeout_ms": 999},
    ])

    env = via_run_cli(repo, model, monkeypatch, timeout_s=30)

    for key in TIMEOUT_KEYS:
        assert key not in env, f"{model}: unexpectedly carries {key}={env[key]!r}"


# --------------------------------------------------------------------------- #
# (d) serving_registry refuses a bad client_timeout_ms, naming row and key
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [0, -5, "200", 12.5, True])
def test_new_row_rejects_non_positive_or_non_int_client_timeout_ms(bad):
    with pytest.raises(serving_registry.RegistryError) as exc:
        serving_registry.new_row(
            "glm-4.7", "claude-code",
            {"parallel": 1, "context_length": 131072, "max_tokens_floor": 8192,
             "temperature": 0, "seed": 42, "quant": "unknown"},
            prefill_tok_s=57.0, client_timeout_ms=bad)
    message = str(exc.value)
    assert "client_timeout_ms" in message
    assert "glm-4.7" in message and "claude-code" in message


def test_load_rows_rejects_a_bad_client_timeout_ms_on_disk(tmp_path):
    bad_registry = tmp_path / "models.yaml"
    bad_registry.write_text(
        "models:\n"
        "  - model: glm-4.7\n"
        "    driver: claude-code\n"
        "    client_timeout_ms: -5\n",
        encoding="utf-8")

    with pytest.raises(serving_registry.RegistryError) as exc:
        serving_registry.load_rows(path=str(bad_registry))
    message = str(exc.value)
    assert "client_timeout_ms" in message
    assert "glm-4.7" in message and "claude-code" in message


def test_new_row_accepts_a_positive_int_client_timeout_ms():
    row = serving_registry.new_row(
        "glm-4.7", "claude-code",
        {"parallel": 1, "context_length": 131072, "max_tokens_floor": 8192,
         "temperature": 0, "seed": 42, "quant": "unknown"},
        prefill_tok_s=57.0, client_timeout_ms=600000)
    assert row["client_timeout_ms"] == 600000

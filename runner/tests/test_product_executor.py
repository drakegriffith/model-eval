"""test_product_executor.py -- the product's run loop, proven where it matters
(ticket 40, slice S2).

Three of ticket 40's acceptance criteria are the kind that a green suite does not
establish on its own, so each has a named test here:

  AC#1 -- A SECOND IMPLEMENTATION, NOT A WRAPPER. The gate proves the executor
     does not IMPORT run.py. That is necessary and not sufficient: a module can
     be a wrapper in every sense that matters while importing nothing, by
     shelling out to `python3 runner/run.py`. So the source is read for run.py's
     own entry points by name, the argv it builds is checked against run.py's,
     and a fresh interpreter confirms importing the executor leaves `run`,
     `judge` and `broker` out of sys.modules entirely.
  AC#2 -- API-KEYS-ONLY, ENUMERATED. Not "no subscription path was found" but
     "here is the complete list of paths, it has N rows, and every row says
     api_key". The list is printed by the CLI, and the printed text is asserted.
  AC#7 -- THE NEGATIVE CONTROL. A cap under the scaffold floor: the invocation
     is never called (counted, not assumed), usage.jsonl gains zero lines
     (counted before and after), and reported spend is exactly 0.0. A cap that
     has never been seen to refuse is not known to work.

No test here spends money. `execute` takes an `invoke` parameter precisely so
the loop's control flow -- the order of the gates, the running spend, the
per-task refusal rows -- is exercised against a recorded CLI response.
"""
import ast
import json
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")
TASKS_DIR = os.path.join(REPO_ROOT, "tasks")

sys.path.insert(0, RUNNER_DIR)
sys.path.insert(0, PRODUCT_DIR)
import spend_cap  # noqa: E402
import usage_ledger  # noqa: E402
from gauntlet_playground import executor  # noqa: E402

METERED_MODEL = "kimi-k3"
# A real task id from this repo's local task tree. The executor's task source is
# deliberately this tree and not ticket 39's catalogue (39 is open, blocked by
# 09), so the test reads the same place the product does.
LOCAL_TASK = "t1-py-a"


def kimi_stdout(tokens_in=40_000, tokens_out=2_000, cache_read=30_000,
                cache_creation=5_000):
    """One `claude -p --output-format json` result event, shaped as the real CLI
    emits it. Recorded rather than invented: parse_usage_detailed's claude/kimi
    branch reads exactly these four fields."""
    return json.dumps({
        "type": "result",
        "num_turns": 3,
        "usage": {"input_tokens": tokens_in,
                  "cache_creation_input_tokens": cache_creation,
                  "cache_read_input_tokens": cache_read,
                  "output_tokens": tokens_out},
    })


class RecordingInvoke:
    """Counts calls and keeps the argv/env it was handed. A count is the point:
    AC#7's claim is "the invocation was not called", and only a counter can say
    that positively."""

    def __init__(self, stdout=None):
        self.calls = []
        self.stdout = kimi_stdout() if stdout is None else stdout

    def __call__(self, cmd, env):
        self.calls.append((list(cmd), dict(env)))
        return self.stdout


def request(tmp_path, cap_usd, task_ids=(LOCAL_TASK,), model=METERED_MODEL,
            api_key="sk-test-not-a-real-key"):
    return executor.RunRequest(
        model=model, task_ids=tuple(task_ids), api_key=api_key,
        cap_usd=cap_usd, tasks_dir=TASKS_DIR,
        usage_path=str(tmp_path / "results" / "usage.jsonl"),
        effort="low", run_id_prefix="testrun")


def count_lines(path):
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def read_executor_source():
    with open(executor.__file__, "r", encoding="utf-8") as f:
        return f.read()


def _docstring_nodes(tree):
    """The Constant nodes that are docstrings, so the source scans below can
    exclude prose without excluding code."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                out.add(id(first.value))
    return out


def code_identifiers(tree):
    """Every name the module actually uses or defines: bare names, attribute
    tails, and its own definitions. Comments and docstrings contribute nothing,
    which is the point -- a module may explain what it does not call."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


def code_string_literals(tree):
    """Every string literal outside a docstring."""
    skip = _docstring_nodes(tree)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in skip]


# --------------------------------------------------------------------------- #
# AC#1 -- a separate implementation, not a wrapper around run.py's loop.
# --------------------------------------------------------------------------- #

def test_the_executor_loop_is_a_separate_implementation_not_a_wrapper():
    """The named test AC#1 asks for.

    Four different ways of being a wrapper are ruled out, because they fail
    differently: importing run.py (the gate catches this one), naming its
    functions, shelling out to it, and reproducing its argv. The last is the
    interesting one -- run.py's kimi command carries the MCP seal flags and the
    instrument's own harness plumbing, and a copy of that command would mean the
    product had inherited the harness by transcription instead of by import.
    """
    tree = ast.parse(read_executor_source(), filename=executor.__file__)

    # run.py's own entry points, by name. Both checks read CODE and not prose:
    # the module docstring discusses run.py at length and must be free to, so a
    # raw text scan would either fail on the explanation or force the
    # explanation out of the file. Identifiers and code string literals are the
    # two places a real dependency can hide.
    names = code_identifiers(tree)
    for symbol in ("execute_run", "run_cli", "build_runs", "graded_run",
                   "build_cli_cmd", "parse_usage", "compose_prompt"):
        assert symbol not in names, f"executor references run.py's {symbol}"

    # Not a wrapper by subprocess either -- shelling out to run.py needs the
    # path as a literal in executable code.
    literals = code_string_literals(tree)
    for shell_out in ("run.py", "runner/run", "-m run"):
        assert not any(shell_out in s for s in literals), \
            f"executor shells out to {shell_out}"

    # And its argv is its own. run.py's kimi command carries MCP seal flags; the
    # product's does not, because the product is not running a sealed experiment.
    cmd = executor.build_command(executor.INVOCATION_PATHS["kimi"],
                                 METERED_MODEL, "low", "PROMPT")
    assert "--strict-mcp-config" not in cmd and "--mcp-config" not in cmd
    assert cmd[0] == "claude" and "--model" in cmd and METERED_MODEL in cmd


def test_importing_the_executor_does_not_pull_the_instruments_harness_into_memory():
    """The behavioural half of AC#1. Asserted in a fresh interpreter because
    other tests in this suite import `run`, so an in-process check would report
    on the session rather than on the module.
    """
    script = (
        "import sys\n"
        "from gauntlet_playground import executor\n"
        "leaked = sorted({'run', 'judge', 'broker', 'tables', 'ladder_from_results'}\n"
        "                & set(sys.modules))\n"
        "assert not leaked, f'executor pulled in {leaked}'\n"
        "assert 'registry' in sys.modules and 'spend_cap' in sys.modules\n"
        "assert 'usage_ledger' in sys.modules\n"
        "print('CLEAN', len(executor.invocation_paths()))\n"
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([PRODUCT_DIR, RUNNER_DIR]))
    proc = subprocess.run([sys.executable, "-c", script], env=env,
                          cwd=os.path.dirname(REPO_ROOT), text=True,
                          capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "CLEAN 1"


def test_the_executor_reads_the_local_task_tree_and_says_it_is_not_the_catalogue():
    """Ticket 39 is open and blocked by 09, so the published catalogue does not
    exist. The executor reads tasks/<id>/PROMPT.md and documents that this is a
    different set -- a reader who assumes otherwise draws the wrong conclusion
    about what the product ran."""
    prompt = executor.local_task_prompt(TASKS_DIR, LOCAL_TASK)
    assert prompt.strip()
    doc = executor.local_task_prompt.__doc__
    assert "39" in doc and "catalogue" in doc.lower()
    with pytest.raises(FileNotFoundError, match="does not exist yet"):
        executor.local_task_prompt(TASKS_DIR, "no-such-task")


# --------------------------------------------------------------------------- #
# AC#2 -- API-keys-only, shown as an enumeration rather than as an absence.
# --------------------------------------------------------------------------- #

def test_every_invocation_path_the_executor_can_take_is_api_key_auth():
    paths = executor.invocation_paths()
    assert len(paths) == 1, [p.family for p in paths]
    assert [p.family for p in paths] == ["kimi"]
    for p in paths:
        assert p.auth == "api_key", f"{p.family} is not API-key auth"
        assert p.key_env_vars, f"{p.family} declares no variable to carry the key"
    # The enumeration is the dispatch table, not a second list that could drift.
    assert {p.family for p in paths} == set(executor.INVOCATION_PATHS)
    assert all(executor.INVOCATION_PATHS[p.family] is p for p in paths)


def test_the_invocation_path_list_is_printed_not_merely_computed():
    text = executor.format_invocation_paths()
    assert "1 invocation path(s), all API-key auth" in text
    assert "auth=api_key" in text
    assert "subscription-auth paths: none" in text
    assert "https://api.moonshot.ai/anthropic" in text


def test_the_executor_cli_shows_the_paths_and_the_caps_coverage():
    """AC#2 and AC#5 in one command a reader can run."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([PRODUCT_DIR, RUNNER_DIR]))
    proc = subprocess.run([sys.executable, "-m", "gauntlet_playground.executor"],
                          env=env, cwd=os.path.dirname(REPO_ROOT),
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert "1 invocation path(s), all API-key auth" in proc.stdout
    assert "meterable: 1 ids across 1 families (kimi)" in proc.stdout
    assert "refused:   15 ids" in proc.stdout


def test_the_child_environment_is_built_from_an_allowlist_and_carries_the_key():
    """Positive assertion, per AC#2's "show the list" spirit: the child's
    environment is exactly the allowlisted names plus the injected key, so a
    host subscription token cannot ride along."""
    ambient = {"PATH": "/usr/bin", "HOME": "/home/x", "TMPDIR": "/tmp",
               "ANTHROPIC_API_KEY": "host-subscription-token",
               "AWS_SECRET_ACCESS_KEY": "should-not-travel",
               "GAUNTLET_BROKER_SOCK": "/tmp/sock"}
    env = executor.invocation_env(executor.INVOCATION_PATHS["kimi"],
                                  "sk-callers-own-key", environ=ambient)

    assert set(env) == {"PATH", "HOME", "TMPDIR", "ANTHROPIC_BASE_URL",
                        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}
    assert env["ANTHROPIC_API_KEY"] == "sk-callers-own-key"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-callers-own-key"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.moonshot.ai/anthropic"
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GAUNTLET_BROKER_SOCK" not in env


def test_no_key_is_an_error_rather_than_a_fallback_to_a_subscription_session():
    with pytest.raises(ValueError, match="does not fall back to a subscription"):
        executor.invocation_env(executor.INVOCATION_PATHS["kimi"], "")


# --------------------------------------------------------------------------- #
# AC#7 -- the negative control, demonstrated once, with counts on both sides.
# --------------------------------------------------------------------------- #

def test_negative_control_a_cap_below_the_scaffold_floor_spends_nothing(tmp_path):
    """The demonstration AC#7 asks for. Three independent observations, because
    "it refused" and "it did not spend" and "the ledger did not grow" are three
    different claims and a bug could satisfy any two."""
    floor = spend_cap.metering_capability(METERED_MODEL).floor_usd
    assert floor > 0
    req = request(tmp_path, cap_usd=floor / 2)
    invoke = RecordingInvoke()

    before = count_lines(req.usage_path)
    report = execute_and_return(req, invoke)
    after = count_lines(req.usage_path)

    assert [o.status for o in report.outcomes] == ["refused"]
    assert invoke.calls == [], "the CLI was invoked under a refusing cap"
    assert report.spent_usd == 0.0
    assert report.ledger_rows_written == 0
    assert after == before == 0, "the ledger gained a row for a refused run"
    assert "scaffold floor" in report.outcomes[0].reason
    assert report.outcomes[0].ledgered is False


def test_a_refused_task_is_a_row_in_the_report_not_a_task_quietly_missing(tmp_path):
    """Dropping the task would make a refusal indistinguishable from a task the
    caller never asked for. Every requested id comes back with a status."""
    floor = spend_cap.metering_capability(METERED_MODEL).floor_usd
    req = request(tmp_path, cap_usd=floor / 2, task_ids=(LOCAL_TASK, "t1-py-b"))
    report = execute_and_return(req, RecordingInvoke())
    assert [o.task_id for o in report.outcomes] == [LOCAL_TASK, "t1-py-b"]
    assert {o.status for o in report.outcomes} == {"refused"}


def test_an_unmeterable_provider_is_refused_before_any_invocation_path_lookup(tmp_path):
    """The money gate is the OUTER gate. claude-opus-5 has no invocation path
    here AND no price; the refusal must report the price, because that is the
    finding the caller can act on -- adding a path would not make it runnable."""
    req = request(tmp_path, cap_usd=1000.0, model="claude-opus-5")
    invoke = RecordingInvoke()
    report = execute_and_return(req, invoke)

    assert [o.status for o in report.outcomes] == ["refused"]
    assert "spend cannot be observed" in report.outcomes[0].reason
    assert "no API-key invocation path" not in report.outcomes[0].reason
    assert invoke.calls == []
    assert count_lines(req.usage_path) == 0


# --------------------------------------------------------------------------- #
# AC#8 -- what runs is ledgered through the instrument's own counting rule.
# --------------------------------------------------------------------------- #

def test_a_completed_run_is_ledgered_through_parse_usage_detailed(tmp_path):
    """The product does not get a second counting rule: the row's token fields
    are compared against usage_ledger.parse_usage_detailed's output on the same
    bytes, computed here by calling it directly."""
    out = kimi_stdout()
    expected = usage_ledger.parse_usage_detailed("kimi", out)
    req = request(tmp_path, cap_usd=100.0)
    report = execute_and_return(req, RecordingInvoke(stdout=out))

    assert [o.status for o in report.outcomes] == ["ran"]
    assert count_lines(req.usage_path) == 1 == report.ledger_rows_written

    with open(req.usage_path, "r", encoding="utf-8") as f:
        row = json.loads(f.readline())
    assert row["tokens_in"] == expected["tokens_in"]
    assert row["tokens_out"] == expected["tokens_out"]
    assert row["cache_read_tokens"] == expected["cache_read_tokens"]
    assert row["cache_creation_tokens"] == expected["cache_creation_tokens"]
    assert row["retrofit_status"] == "measured"
    assert row["model_id"] == METERED_MODEL
    assert row["billing_mode"] == "metered"
    assert row["run_id"] == f"testrun-{LOCAL_TASK}"
    assert report.spent_usd == pytest.approx(row["usd_estimate"])
    assert report.spent_usd == pytest.approx(spend_cap.cost_of(METERED_MODEL, expected))


def test_the_running_spend_is_what_stops_a_multi_task_run(tmp_path):
    """Two tasks, a cap that covers one of them and one floor's headroom short
    of the second. The first runs, the second is refused, and the refusal cites
    the spend the first one made."""
    detail = usage_ledger.parse_usage_detailed("kimi", kimi_stdout())
    one_run = spend_cap.cost_of(METERED_MODEL, detail)
    floor = spend_cap.metering_capability(METERED_MODEL).floor_usd

    req = request(tmp_path, cap_usd=one_run + floor / 2,
                  task_ids=(LOCAL_TASK, "t1-py-b"))
    invoke = RecordingInvoke()
    report = execute_and_return(req, invoke)

    assert [o.status for o in report.outcomes] == ["ran", "refused"]
    assert len(invoke.calls) == 1, "the refused task was invoked anyway"
    assert report.ledger_rows_written == 1 == count_lines(req.usage_path)
    assert report.spent_usd == pytest.approx(one_run)
    assert f"${one_run:.4f} already spent" in report.outcomes[1].reason


def test_the_invoked_command_and_environment_are_the_ones_declared(tmp_path):
    """What actually reached the child process, rather than what build_command
    returns when asked politely in isolation."""
    req = request(tmp_path, cap_usd=100.0)
    invoke = RecordingInvoke()
    execute_and_return(req, invoke)

    (cmd, env), = invoke.calls
    assert cmd[0] == "claude"
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == METERED_MODEL
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "low"
    assert executor.local_task_prompt(TASKS_DIR, LOCAL_TASK) in cmd
    assert env["ANTHROPIC_BASE_URL"] == "https://api.moonshot.ai/anthropic"
    assert env["ANTHROPIC_API_KEY"] == req.api_key


def test_the_repos_own_ledger_is_never_written_by_these_tests(tmp_path):
    """The executor writes where the REQUEST says, so a product in another tree
    ledgers in that tree. Asserted the way ticket 37's portability tests do it:
    by watching the real path not move."""
    real = usage_ledger.paths_for_repo(REPO_ROOT).usage
    before = os.path.getmtime(real) if os.path.exists(real) else None
    req = request(tmp_path, cap_usd=100.0)
    execute_and_return(req, RecordingInvoke())
    after = os.path.getmtime(real) if os.path.exists(real) else None
    assert before == after
    assert os.path.exists(req.usage_path)


def execute_and_return(req, invoke):
    """One call site for `execute`, so every test above exercises the same
    entry point rather than a per-test variation of it."""
    return executor.execute(req, invoke=invoke)

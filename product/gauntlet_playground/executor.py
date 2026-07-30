"""executor.py -- the product's own run loop (ticket 40, slice S2).

WHY THIS EXISTS WHEN run.py ALREADY LOOPS OVER TASKS.
14b-repo-boundary-doctrine-consult §4 puts run.py's loop explicitly OUTSIDE the
core, and ticket 38's boundary means product code cannot reach it. That is a
ruled decision, not an obstacle to route around: run.py's loop is the
INSTRUMENT's harness -- reproducible sweeps, seeds, resume, judge panels,
transcript archival, the broker and the sandbox seal. The product needs none of
it. So this is a second, smaller implementation rather than a wrapper, and
runner/tests/test_product_executor.py asserts that as a fact about the module
(it never names run.py's functions, and importing it does not pull `run` into
sys.modules) rather than as an intention stated in a comment.

WHAT IT DOES NOT DO. Grading, verification and the result surface are ticket 44.
Publishing the task catalogue is ticket 39, which is open and blocked by 09 --
so `local_task_prompt` below reads this repo's own tasks/<id>/PROMPT.md and says
plainly that this is NOT the published catalogue. When 39 lands, the catalogue
becomes another `task_source` and this function stops being the default; nothing
else here moves.

THE ORDER OF THE TWO GATES, and why the money one is outer.
`execute` calls spend_cap.authorize BEFORE it looks up an invocation path. The
other order also "works" in that nothing runs, but it reports a spend refusal as
an unsupported-provider error, which is the wrong finding: the caller would go
looking for a missing binary when what they actually hit was a cap. Money is the
gate that has to hold for providers this product does not yet know how to run.

API-KEYS-ONLY (round 1's ruling, AC#2). Every path this executor can take is a
row of INVOCATION_PATHS, every row declares `auth="api_key"`, and
`invocation_paths()` exists so the list can be PRINTED. "No test failed" is not
the same claim as "here is the complete enumeration"; only the second one can be
checked by a reader.
"""
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import NamedTuple

import registry
import spend_cap
import usage_ledger


class InvocationPath(NamedTuple):
    """One way this executor can cause a model to run.

    `auth` is a field rather than an assumption so the API-keys-only ruling is
    machine-checkable: a subscription path would have to write `auth =
    "subscription"` here, in the enumeration, where a test and a reader both see
    it. `key_env_vars` is the complete set of variables the key is written into,
    listed because "which environment variable carries the secret" is a fact the
    caller is entitled to read rather than infer from a call site.
    """
    family: str
    auth: str
    binary: str
    base_url: str
    key_env_vars: tuple
    note: str


# The whole enumeration. One row today, and that is the honest state: the spend
# cap refuses every family it cannot meter, so a second row here without a price
# in usage_ledger.PRICING would be a path that can never be taken. Widening the
# metered set is ticket 11's job; widening this table follows it, not the other
# way round.
INVOCATION_PATHS = {
    "kimi": InvocationPath(
        family="kimi",
        auth="api_key",
        binary="claude",
        base_url="https://api.moonshot.ai/anthropic",
        key_env_vars=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
        note="Claude Code's binary pointed at Moonshot's Anthropic-compatible "
             "endpoint with the caller's own key injected. Metered, real money, "
             "and the only family the spend cap can price today.",
    ),
}

# The environment the child process gets, built by ALLOWLIST rather than by
# copying os.environ and removing what looks dangerous. A subtractive env is a
# claim about everything that exists; an additive one is a claim about six names,
# and the difference is that the second can be asserted positively -- which is
# what runner/tests/test_product_executor.py does. ANTHROPIC_* deliberately does
# not appear: those are set from the caller's key, never inherited, so a host
# subscription token in the ambient environment cannot reach the child.
ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


class TaskOutcome(NamedTuple):
    """What happened to one requested task.

    A refusal is an outcome with a row here, not a task quietly missing from the
    results. `ledgered` says whether a usage.jsonl row was written, and it is
    False for every refusal by construction: the ledger records spend, and a
    refused task did not spend.

    `wall_s` is the latency ticket 44's printed sentence reads out, and it is
    None -- never 0.0 -- for a task that did not run. Zero seconds is a
    measurement: it plots at the origin of a latency axis as the fastest result
    on the chart, which is the opposite of what a refusal means. It carries no
    default for the same reason `cap_usd` does not: every construction site
    below has to say which of the two it is.
    """
    task_id: str
    model_id: str
    status: str            # "ran" | "refused" | "unsupported"
    usd: float
    reason: str
    run_id: str | None
    usage_detail: dict | None
    ledgered: bool
    wall_s: float | None


class RunRequest(NamedTuple):
    """One user's ask: their key, their cap, their models, their tasks.

    `cap_usd` has no default. An absent cap and an unlimited one are the same
    bytes to a reader and only one of them is a decision somebody made, so
    spend_cap.authorize refuses None rather than reading it as no limit -- and
    this NamedTuple does not soften that by supplying a number nobody chose.
    """
    model: str
    task_ids: tuple
    api_key: str
    cap_usd: float
    tasks_dir: str
    usage_path: str
    effort: str = "medium"
    run_id_prefix: str | None = None


class RunReport(NamedTuple):
    """The rows ticket 44 will render, plus the two counts AC#7 asks for.

    `effort` is the rung the whole report was measured at, carried because
    ticket 44 plots a live point as a dot at one (model, effort) pair and 14a's
    ruling never marginalizes effort into the model. Without it the surface
    would have to guess -- and a guessed rung puts a dot on a tier nobody ran.
    One field for the report rather than one per outcome because a RunRequest
    carries a single effort: every outcome in this report was produced at it.
    """
    model_id: str
    family: str
    cap_usd: float
    spent_usd: float
    outcomes: tuple
    ledger_rows_written: int
    effort: str


def invocation_paths():
    """Every invocation path this executor can take, as a list (AC#2).

    Enumerated from the table rather than discovered by inspection, so the list a
    caller prints is the same object `execute` dispatches on. A path that exists
    but is missing from this list is not possible: there is one table.
    """
    return [INVOCATION_PATHS[family] for family in sorted(INVOCATION_PATHS)]


def format_invocation_paths(paths=None):
    paths = invocation_paths() if paths is None else paths
    lines = [f"gauntlet-playground executor -- {len(paths)} invocation path(s), "
             f"all API-key auth:"]
    for p in paths:
        lines.append(f"  {p.family:8s} auth={p.auth} binary={p.binary!r} "
                     f"base_url={p.base_url}")
        lines.append(f"           key injected as: {', '.join(p.key_env_vars)}")
        lines.append(f"           {p.note}")
    subscription = [p.family for p in paths if p.auth != "api_key"]
    lines.append(f"  subscription-auth paths: {', '.join(subscription) or 'none'} "
                 f"-- API-keys-only is round 1's ruling, and this is the whole list")
    return "\n".join(lines)


def invocation_env(path, api_key, environ=None):
    """The child's environment: the allowlist, then the key.

    Raises ValueError on an empty key rather than launching a process that will
    fail on the far side of the network. The caller supplied a key or did not,
    and that is knowable here.
    """
    if not api_key:
        raise ValueError(
            f"no API key supplied for the {path.family} invocation path -- this "
            f"executor runs on the caller's own key and does not fall back to a "
            f"subscription session")
    source = os.environ if environ is None else environ
    env = {name: source[name] for name in ENV_ALLOWLIST if name in source}
    env["ANTHROPIC_BASE_URL"] = path.base_url
    for name in path.key_env_vars:
        env[name] = api_key
    return env


def build_command(path, model_id, effort, prompt):
    """The child's argv. Deliberately short: this is the product's run, not the
    instrument's -- no MCP seal flags, no broker socket, no scoped CODEX_HOME,
    because the product runs a task and reports what it cost."""
    cmd = [path.binary, "-p", prompt, "--output-format", "json",
           "--model", model_id, "--dangerously-skip-permissions"]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def local_task_prompt(tasks_dir, task_id):
    """Read one task's prompt from this repo's tasks/<id>/PROMPT.md.

    THIS IS NOT TICKET 39's PUBLISHED CATALOGUE. 39 (publish the expendable
    catalogue) is open and blocked by 09, so there is nothing published to read
    yet. Naming that here rather than letting a reader assume the catalogue
    already exists is the point of this docstring: a visitor running the product
    today is running the instrument's own local task tree, which is a different
    set with different provenance.
    """
    prompt_path = os.path.join(tasks_dir, task_id, "PROMPT.md")
    if not os.path.isfile(prompt_path):
        raise FileNotFoundError(
            f"no PROMPT.md for task {task_id!r} at {prompt_path} (local task "
            f"source; ticket 39's published catalogue does not exist yet)")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def _invoke_api_key_cli(cmd, env, timeout=900):
    """The default invocation. Injected as `invoke` in tests so the loop's
    control flow is exercised without spending anyone's money.

    Named for what it does rather than `run_cli`, which is run.py's function.
    Two functions in one repo with the same name and different contracts is how
    a reader concludes the product wraps the instrument; the name is doing work
    here, and runner/tests/test_product_executor.py pins it.
    """
    proc = subprocess.run(cmd, env=env, text=True, capture_output=True,
                          timeout=timeout)
    return proc.stdout


def execute(request, invoke=None):
    """Run each requested task under the cap, ledger what ran, report what did not.

    The loop, in order, per task:

      1. spend_cap.authorize(model, cap, spent_so_far). This is FIRST -- see the
         module docstring. A SpendRefused becomes a "refused" outcome and the
         loop moves to the next task rather than aborting, because "your cap
         covered two of five tasks" is a more useful report than a traceback on
         task three. It does not become a ledger row: nothing was spent.
      2. Look up the invocation path. A family with no path is "unsupported" --
         a different finding from a refusal, and reported as one.
      3. Run, parse with usage_ledger.parse_usage_detailed, price with
         spend_cap.cost_of. AC#8: the product does not get a second counting
         rule, so both of those are the instrument's own functions rather than
         arithmetic repeated here.
      4. Append one usage.jsonl row through usage_ledger.build_usage_row /
         append_usage_row, and add its cost to the running spend, which the next
         iteration's authorize() sees. That is how a multi-task run stops.
    """
    invoke = _invoke_api_key_cli if invoke is None else invoke
    model_id, spec = registry.resolve_model(request.model)
    family = spec["family"]
    prefix = request.run_id_prefix or uuid.uuid4().hex[:12]

    spent = 0.0
    ledgered = 0
    outcomes = []

    for task_id in request.task_ids:
        try:
            spend_cap.authorize(request.model, request.cap_usd, spent)
        except spend_cap.SpendRefused as exc:
            outcomes.append(TaskOutcome(task_id, model_id, "refused", 0.0,
                                        exc.reason, None, None, False, None))
            continue

        path = INVOCATION_PATHS.get(family)
        if path is None:
            outcomes.append(TaskOutcome(
                task_id, model_id, "unsupported", 0.0,
                f"no API-key invocation path for family {family!r}; this executor "
                f"has paths for {', '.join(sorted(INVOCATION_PATHS))} and does not "
                f"fall back to a subscription CLI surface",
                None, None, False, None))
            continue

        prompt = local_task_prompt(request.tasks_dir, task_id)
        env = invocation_env(path, request.api_key)
        # The latency ticket 44's sentence reads out, measured here because this
        # is the only place that sees the invocation start and end. The clock is
        # monotonic, not the wall clock the ledger's `ts` comes from: a duration
        # subtracted from two time-of-day readings can come out negative when the
        # host's clock steps mid-run, and a negative latency plots as a point
        # rather than raising. The span is the invocation alone -- reading the
        # prompt off disk and writing the ledger row are this executor's costs,
        # not the model's, and folding them in would report a slower model.
        started = time.monotonic()
        out = invoke(build_command(path, model_id, request.effort, prompt), env)
        wall_s = time.monotonic() - started

        detail = usage_ledger.parse_usage_detailed(family, out)
        usd = spend_cap.cost_of(model_id, detail)
        spent += usd

        run_id = f"{prefix}-{task_id}"
        row = usage_ledger.build_usage_row(
            {"run_id": run_id,
             "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "model": request.model,
             "tokens_in": detail["tokens_in"],
             "tokens_out": detail["tokens_out"]},
            family, usage_detail=detail, model_id=model_id)
        usage_ledger.append_usage_row(request.usage_path, row)
        ledgered += 1

        outcomes.append(TaskOutcome(task_id, model_id, "ran", usd,
                                    f"ran and ledgered as {run_id}", run_id,
                                    detail, True, wall_s))

    return RunReport(model_id, family, request.cap_usd, spent,
                     tuple(outcomes), ledgered, request.effort)


def format_report(report):
    lines = [
        f"gauntlet-playground run -- {report.model_id} (family {report.family})",
        f"  cap ${report.cap_usd:.4f}; spent ${report.spent_usd:.4f}; "
        f"{report.ledger_rows_written} ledger row(s) written",
    ]
    for o in report.outcomes:
        lines.append(f"  {o.status.upper():11s} {o.task_id:12s} ${o.usd:.4f}  {o.reason}")
    return "\n".join(lines)


def main(argv=None):
    """Today's CLI shows the enumeration and the cap's coverage -- the two facts
    ticket 40 asks to be reported rather than assumed. It does not start a run:
    a command that spends a stranger's money is ticket 41's surface to design,
    and this entry point exists so the list can be read before then."""
    import argparse
    argparse.ArgumentParser(
        description="Show the product executor's invocation paths (all API-key "
                    "auth) and the spend cap's metering coverage."
    ).parse_args(argv)
    print(format_invocation_paths())
    print()
    print(spend_cap.format_metering_report(spend_cap.metering_report()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

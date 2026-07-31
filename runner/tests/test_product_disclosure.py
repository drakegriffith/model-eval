"""test_product_disclosure.py -- the pre-run disclosure and its gate (ticket 43).

What ticket 43 claims, proven the way this suite proves things -- positively,
with counters and line counts rather than absences:

  THE GATE HOLDS ON THE DIRECT PATH. execute() reached without a disclosure
     raises DisclosureRequired with the invocation never called (counted) and
     zero ledger rows (counted). The caller ticket 43 is about is the one who
     skipped every politer surface, so the test calls execute() directly.
  THE GATE IS OUTER TO THE MONEY GATE. An unmeterable model without a
     disclosure gets DisclosureRequired, not a refused-report: being told "you
     were not shown the terms" must come before "your cap does not cover this",
     or an undisclosed run could still learn things by probing the cap.
  THE FINGERPRINT BINDS, NOT THE OBJECT. A disclosure shown for one
     (model, task set, cap) does not authorize another; the matching one runs.
  NO MONEY WORDS SURVIVE. The formatted text goes through the same structural
     gate as the printed sentence, and the cap participates in the fingerprint
     without appearing in the text.
  EVERY ITEM IS LABELLED, AND THE VERSION IS LIVE. billing_mode says INFERRED
     and names the family-name test it came from; the CLI version is what the
     binary answered at disclosure time, not a recorded constant.
  WHAT IS PENDING SAYS SO BY NAME. asymmetry_sentence raises NotSettled, and
     the formatted text carries both PENDING lines instead of drafts.

No test here spends money, and none shells out to a real CLI: the version probe
is either injected or pointed at a script this file wrote.
"""
import json
import os
import stat
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")
TASKS_DIR = os.path.join(REPO_ROOT, "tasks")

sys.path.insert(0, RUNNER_DIR)
sys.path.insert(0, PRODUCT_DIR)
import registry  # noqa: E402
import usage_ledger  # noqa: E402
from gauntlet_playground import disclosure  # noqa: E402
from gauntlet_playground import executor  # noqa: E402

METERED_MODEL = "kimi-k3"
LOCAL_TASK = "t1-py-a"
# A cap with a distinctive decimal, so "the cap's figure is not in the text"
# is a check for these exact digits and not for a number that could appear by
# coincidence (a 100 might; a 73.31 does not).
DISTINCTIVE_CAP = 73.31


def kimi_stdout(tokens_in=40_000, tokens_out=2_000, cache_read=30_000,
                cache_creation=5_000):
    """One `claude -p --output-format json` result event, shaped as the real CLI
    emits it -- same recording test_product_executor.py uses."""
    return json.dumps({
        "type": "result",
        "num_turns": 3,
        "usage": {"input_tokens": tokens_in,
                  "cache_creation_input_tokens": cache_creation,
                  "cache_read_input_tokens": cache_read,
                  "output_tokens": tokens_out},
    })


class RecordingInvoke:
    """Counts calls and keeps the argv/env it was handed. The count is the
    point: "the invocation was not called" is only sayable by a counter."""

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


def stub_probe(binary, env):
    """A version probe for tests that are not about the binary."""
    return "stub-version"


def disclose_for(req):
    """Disclose the way a well-behaved caller does: path looked up in the same
    table execute() dispatches on, which may have no row (path=None)."""
    _, spec = registry.resolve_model(req.model)
    path = executor.INVOCATION_PATHS.get(spec["family"])
    return disclosure.disclose(req, path, probe_version=stub_probe)


# --------------------------------------------------------------------------- #
# The gate on the direct path.
# --------------------------------------------------------------------------- #

def test_execute_without_a_disclosure_runs_nothing_and_ledgers_nothing(tmp_path):
    """The direct caller -- the one ticket 43 is about -- gets an exception,
    not a report. Three observations: the raise, the call count, the line
    count; a partial run could satisfy any one of them alone."""
    req = request(tmp_path, cap_usd=100.0)
    invoke = RecordingInvoke()

    with pytest.raises(disclosure.DisclosureRequired,
                       match="disclosure.disclose"):
        executor.execute(req, invoke=invoke)

    assert invoke.calls == [], "the CLI was invoked without a disclosure"
    assert count_lines(req.usage_path) == 0, \
        "an undisclosed run wrote a ledger row"


def test_the_disclosure_gate_is_outer_to_the_money_gate(tmp_path):
    """claude-opus-5 is unmeterable: WITH a disclosure it comes back as a
    refused-report (the money gate's finding). WITHOUT one it must be
    DisclosureRequired -- if the cap were consulted first, an undisclosed
    caller could still probe what the cap covers."""
    req = request(tmp_path, cap_usd=1000.0, model="claude-opus-5")
    invoke = RecordingInvoke()

    with pytest.raises(disclosure.DisclosureRequired):
        executor.execute(req, invoke=invoke)

    report = executor.execute(req, invoke=invoke, disclosed=disclose_for(req))
    assert [o.status for o in report.outcomes] == ["refused"]
    assert "spend cannot be observed" in report.outcomes[0].reason
    assert invoke.calls == []


# --------------------------------------------------------------------------- #
# The fingerprint binds the disclosure to one run.
# --------------------------------------------------------------------------- #

def test_a_disclosure_for_a_different_cap_or_task_set_does_not_authorize(tmp_path):
    """The acknowledgement is of (model, task_ids, cap_usd), not of "a
    disclosure exists". Both mismatches that matter here: the cap (bound but
    unprinted -- exactly the field a caller could quietly change after being
    shown the screen) and the task set."""
    req = request(tmp_path, cap_usd=100.0)
    invoke = RecordingInvoke()

    other_cap = disclose_for(request(tmp_path, cap_usd=200.0))
    with pytest.raises(disclosure.DisclosureRequired, match="different"):
        executor.execute(req, invoke=invoke, disclosed=other_cap)

    other_tasks = disclose_for(
        request(tmp_path, cap_usd=100.0, task_ids=(LOCAL_TASK, "t1-py-b")))
    with pytest.raises(disclosure.DisclosureRequired, match="different"):
        executor.execute(req, invoke=invoke, disclosed=other_tasks)

    assert invoke.calls == []
    assert count_lines(req.usage_path) == 0


def test_the_matching_disclosure_authorizes_exactly_its_run(tmp_path):
    """The positive control for the binding: same (model, task set, cap),
    and the run proceeds to a ledgered outcome."""
    req = request(tmp_path, cap_usd=100.0)
    report = executor.execute(req, invoke=RecordingInvoke(),
                              disclosed=disclose_for(req))
    assert [o.status for o in report.outcomes] == ["ran"]
    assert report.ledger_rows_written == 1 == count_lines(req.usage_path)


# --------------------------------------------------------------------------- #
# No money words: the formatted text goes through the structural gate.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("money", ["about $0.42 per task", "roughly 3 USD",
                                   "a few dollars", "some cents"])
def test_a_money_word_added_to_any_item_is_refused_at_format_time(tmp_path, money):
    """The gate is on the FINISHED string, so a price added by a future edit to
    any item has one place it must pass. Tampered via _replace because that is
    what a future edit is: a different text in the same slot."""
    d = disclose_for(request(tmp_path, cap_usd=DISTINCTIVE_CAP))
    tampered = d._replace(items=(
        d.items[0]._replace(text=d.items[0].text + " -- " + money),
    ) + d.items[1:])
    with pytest.raises(ValueError, match="no slice may print a price"):
        disclosure.format_disclosure(tampered)


def test_the_clean_disclosure_prints_no_symbol_and_not_the_caps_figure(tmp_path):
    """The cap binds (it is in the fingerprint) and does not display: the
    distinctive digits must be absent from the text, along with the symbol the
    structural gate would catch."""
    d = disclose_for(request(tmp_path, cap_usd=DISTINCTIVE_CAP))
    text = disclosure.format_disclosure(d)
    assert "$" not in text
    assert "73.31" not in text, "the cap's figure leaked into the text"
    assert d.fingerprint in text
    assert d.fingerprint == disclosure.fingerprint(
        METERED_MODEL, (LOCAL_TASK,), DISTINCTIVE_CAP)


# --------------------------------------------------------------------------- #
# Every item is labelled, and the label is honest about its provenance.
# --------------------------------------------------------------------------- #

def test_four_items_each_labelled_and_billing_mode_names_its_inference(tmp_path):
    """billing_mode is the one fact nobody observed -- it is
    usage_ledger.build_usage_row's family-name test -- and its item must say
    INFERRED and say why. The other three happened at disclosure time and say
    OBSERVED. An unmarked mix would let the weakest item borrow the strongest
    item's provenance."""
    d = disclose_for(request(tmp_path, cap_usd=100.0))
    assert len(d.items) == 4
    assert [i.key for i in d.items] == [
        "invocation_path", "cli_version", "billing_mode", "cost_basis"]
    for item in d.items:
        assert item.label in disclosure.LABELS

    by_key = {i.key: i for i in d.items}
    billing = by_key["billing_mode"]
    assert billing.label == disclosure.INFERRED
    assert "build_usage_row" in billing.text
    assert "family" in billing.text and "string comparison" in billing.text
    # The stamped label is the ledger's own, read off a row its function built.
    row = usage_ledger.build_usage_row(
        {"run_id": "x", "model": METERED_MODEL, "tokens_in": 0,
         "tokens_out": 0}, "kimi", model_id="kimi-k3")
    assert f"{row['billing_mode']!r}" in billing.text

    for key in ("invocation_path", "cli_version", "cost_basis"):
        assert by_key[key].label == disclosure.OBSERVED, \
            f"{key} is not marked observed"
    assert executor.INVOCATION_PATHS["kimi"].base_url \
        in by_key["invocation_path"].text


# --------------------------------------------------------------------------- #
# The CLI version is live: asked at disclosure time, never a constant.
# --------------------------------------------------------------------------- #

def fake_binary(tmp_path, answer):
    """A --version-answering script at a stable path, rewritten per call so two
    discloses can see two different binaries at the SAME path -- which is what
    an upgrade is."""
    path = tmp_path / "fake-cli"
    path.write_text(f"#!/bin/sh\necho '{answer}'\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_the_version_is_what_the_binary_answered_not_a_recorded_constant(tmp_path):
    """Two discloses across a binary upgrade report two different versions. A
    recorded constant passes any single-shot check; only a change the module
    tracks proves it asked."""
    kimi = executor.INVOCATION_PATHS["kimi"]
    req = request(tmp_path, cap_usd=100.0)

    path = kimi._replace(binary=fake_binary(tmp_path, "fake-cli 1.0.0"))
    first = disclosure.disclose(req, path)
    fake_binary(tmp_path, "fake-cli 2.0.0")
    second = disclosure.disclose(req, path)

    first_v = [i for i in first.items if i.key == "cli_version"][0]
    second_v = [i for i in second.items if i.key == "cli_version"][0]
    assert "fake-cli 1.0.0" in first_v.text
    assert "fake-cli 2.0.0" in second_v.text, \
        "the version did not follow the binary, so it is not being asked"


def test_a_binary_that_does_not_answer_is_reported_unavailable(tmp_path):
    kimi = executor.INVOCATION_PATHS["kimi"]
    path = kimi._replace(binary=str(tmp_path / "no-such-binary"))
    d = disclosure.disclose(request(tmp_path, cap_usd=100.0), path)
    version = [i for i in d.items if i.key == "cli_version"][0]
    assert "unavailable:" in version.text
    assert version.label == disclosure.OBSERVED, \
        "a non-answer at disclosure time is still an observation"


def test_the_version_probe_carries_no_key(tmp_path):
    """A version question does not need a key, so none travels: the probe's
    environment is the executor's allowlist, which has no ANTHROPIC_* names."""
    seen = {}

    def recording_probe(binary, env):
        seen["env"] = dict(env)
        return "v1"

    kimi = executor.INVOCATION_PATHS["kimi"]
    disclosure.disclose(request(tmp_path, cap_usd=100.0), kimi,
                        probe_version=recording_probe)
    assert "env" in seen, "the probe was never called"
    leaked = [k for k in seen["env"] if k.startswith("ANTHROPIC")]
    assert leaked == [], f"the version probe's environment carries {leaked}"


# --------------------------------------------------------------------------- #
# What is pending is rendered as pending, by name.
# --------------------------------------------------------------------------- #

def test_the_asymmetry_sentence_is_not_written_and_the_function_says_so():
    """NotSettled, naming the open ticket -- not a draft that would settle §9
    by side effect in a file nobody reviewing §9 reads."""
    with pytest.raises(disclosure.NotSettled, match="ticket 20"):
        disclosure.asymmetry_sentence()


def test_the_formatted_disclosure_carries_both_gaps_by_name(tmp_path):
    d = disclose_for(request(tmp_path, cap_usd=100.0))
    text = disclosure.format_disclosure(d)
    pending = [line for line in text.splitlines() if "PENDING" in line]
    assert len(pending) == 2, "a PENDING line was dropped or drafted over"
    assert "ticket 20 §9" in pending[0] and "asymmetry" in pending[0]
    assert "14c" in pending[1] and "no prototype outcome" in pending[1]

"""disclosure.py -- what a visitor is told BEFORE their key spends (ticket 43).

WHAT THIS MODULE IS. The pre-run disclosure: five labelled facts -- the
billing-mode label, the cost basis, the corpus-derived expected spend, the
invocation path, and the CLI version, in that order (14c Ruling 1: the two
money items are what a stranger must read before spending; path and version
are covariates and read second) -- assembled into a `Disclosure` the caller
must hand back to executor.execute as `disclosed=`. The hand-back is the acknowledgement. Its fingerprint binds it to
(model, task_ids, cap_usd), so a disclosure shown for one run does not
authorize a different one, and execute() raises DisclosureRequired rather than
proceeding without a match. The gate lives in execute() itself, not in a CLI
front end, for spend_cap's reason: a gate enforced in the surface that asks
politely holds for exactly one caller, and the caller ticket 43 is about is the
one that reaches the executor directly.

OBSERVED vs INFERRED vs CORPUS, and why every item carries one of the three
words. An observed fact was produced by the machinery at disclosure time: the
path row execute() will dispatch on, the version string the binary answered
with just now, the probe usage the pricing formula returned a figure for. An
inferred fact is a label something wrote down without observing it:
billing_mode is usage_ledger.build_usage_row's family-name test -- a string
comparison -- and a visitor deciding whether to trust "metered" is entitled to
know the word came from a name check and not from a bill anyone has seen. A
corpus fact is a statistic over past sealed-corpus runs -- neither observed
now nor inferred from a name, and a reader weighing "expected spend" is
entitled to know it is history, not a promise. Printing the kinds unmarked
would let the weakest item borrow the strongest item's provenance.

WHAT WAS PENDING HAS SETTLED, AND WHERE EACH SETTLEMENT LANDED. Both gaps this
screen once rendered by name closed on 2026-07-31. 14c's prototype outcome
(Ruling 1) is the item order above and the [CORPUS] expected-spend line; it
also rules that PENDING lines group under a named gutter ("not yet stated on
this screen:") that disappears when empty -- today it is empty, and
format_disclosure keeps the mechanism so an absent gutter means no gap, not a
deleted renderer. Ticket 20 §9 ratified the asymmetry copy (§9-SETTLED, draft
A), which `asymmetry_sentence` now returns with a measured total substituted.
That sentence deliberately does NOT render here: it is post-run copy ("This
run used ...") and a pre-run screen has no measured total to state as fact,
and its ratified text names a dollar figure -- the word, not a price -- which
surface._refuse_money structurally refuses. Its gate is the verbatim pin in
test_product_disclosure.py instead.

NO MONEY WORDS. The finished string passes through surface._refuse_money, the
same structural gate the printed sentence uses, because this module holds the
visitor's cap and is one attribute access away from echoing it. The cap
participates in the fingerprint -- binding, never display -- and ticket 08
still records no verified prices, so no slice may print one.
"""
import hashlib
import json
import os
import statistics
import subprocess
from typing import NamedTuple

import corpus_gates
import registry
import spend_cap
import stats
import usage_ledger

# Product-internal imports are legal under half B; `surface` is where the money
# gate already lives, and a second copy of it here would be the drift ticket 40
# AC#8 refuses for token counting. executor is imported for the env allowlist
# only -- the path row arrives as an argument, from the caller who looked it up
# in the same table execute() dispatches on.
from gauntlet_playground import executor, surface

OBSERVED = "observed"
INFERRED = "inferred"
CORPUS = "corpus"
LABELS = (OBSERVED, INFERRED, CORPUS)


class DisclosureRequired(Exception):
    """execute() was reached without a disclosure that matches the request.

    Raised by the executor, defined here: the disclosure module owns what a
    valid acknowledgement is, and the executor owns refusing to run without
    one. Carrying no partial report -- control never reaches the loop.
    """


class DisclosureItem(NamedTuple):
    """One disclosed fact. `label` is OBSERVED or INFERRED, on the item rather
    than in surrounding prose, so a reader of the struct and a reader of the
    printed text see the same provenance claim."""
    key: str
    label: str
    text: str


class Disclosure(NamedTuple):
    """What the visitor was shown, plus the fingerprint that makes handing it
    back an acknowledgement of THIS run and no other."""
    model_id: str
    family: str
    task_ids: tuple
    cap_usd: float
    items: tuple      # five DisclosureItem, each labelled, money first
    pending: tuple    # structural gaps, named -- see PENDING_LINES
    fingerprint: str


# Empty since 2026-07-31, when both gaps that lived here settled the same day:
# ticket 20 §9 by ratifying the asymmetry copy (see `asymmetry_sentence`), and
# 14c by shipping its prototype outcome -- the item order and the [CORPUS]
# expected-spend line ARE that outcome, cited. The tuple stays as the slot a
# future gap goes in: format_disclosure renders these under 14c's named gutter
# ("not yet stated on this screen:"), which disappears when this is empty.
PENDING_LINES = ()


def asymmetry_sentence(total_tokens):
    """Ticket 20 §9's asymmetry copy -- ratified draft A (§9-SETTLED,
    2026-07-31), with this run's measured token total substituted for the
    example figure ("2.4 million" was §4's carried example, not a constant).

    Post-run copy, deliberately not rendered by format_disclosure: sentence
    one states a measured total as fact, which a pre-run screen does not have,
    and the ratified text names a dollar figure -- the word, not a price --
    which surface._refuse_money structurally refuses. This sentence's gate is
    therefore the verbatim pin in test_product_disclosure.py. That pin
    restates the wording on purpose (the checker must not read its rule from
    the worker); an edit here the pin does not also make is §9 re-litigated,
    not a cleanup.

    The three-sentence contract is binding: (1) measured tokens as fact,
    (2) subscription marginal cost zero stated plainly, (3) dollars computed
    only from reader-supplied fresh and cache-read rates, owned by the reader.
    """
    return (f"This run used {_token_figure(total_tokens)} tokens. On a "
            f"subscription plan you paid nothing extra for it. To see a "
            f"dollar figure, enter what you pay per million tokens — fresh "
            f"and cached separately, because cached tokens bill at about a "
            f"tenth — and the arithmetic runs on your numbers, not ours.")


def _token_figure(total):
    """The measured total at the ratified example's register: '2.4 million'
    above a million, exact with separators below it."""
    if total >= 1_000_000:
        text = f"{total / 1_000_000:.1f}"
        if text.endswith(".0"):
            text = text[:-2]
        return f"{text} million"
    return f"{total:,}"


def fingerprint(model, task_ids, cap_usd):
    """The binding between a disclosure and one run: (model, task_ids, cap_usd),
    hashed. Computed identically here and in execute()'s gate -- one function,
    two callers -- so 'matches' has a single definition. The cap is IN the
    binding precisely because it is not in the printed text: changing the cap
    after being shown the disclosure invalidates the acknowledgement."""
    payload = json.dumps(
        {"model": model, "task_ids": list(task_ids), "cap_usd": cap_usd},
        sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def disclose(request, path, probe_version=None, corpus_rows=()):
    """Build the pre-run disclosure for one executor.RunRequest.

    `path` is the executor.InvocationPath the run would take, or None for a
    family the table has no row for -- a pathless family still gets a
    disclosure, because execute() still owes it a report (an "unsupported"
    outcome), and the gate in front of that report still demands one.

    `probe_version` is injectable for the same reason execute()'s `invoke` is:
    the default asks the real binary, and tests that are not about the binary
    exercise the rest of the disclosure without shelling out.

    `corpus_rows` feeds the expected-spend item, on printed_sentence's
    argument: the caller who has the sealed corpus loaded passes it. A caller
    without it still gets the item -- stating that no figure can be quoted and
    over how many rows that was decided, which is a statement the screen owes
    either way.

    Item order is 14c Ruling 1 (money first): billing_mode, cost basis,
    expected spend, then the covariates.
    """
    probe = _probe_cli_version if probe_version is None else probe_version
    model_id, spec = registry.resolve_model(request.model)
    family = spec["family"]
    items = (
        _billing_item(request, model_id, family),
        _cost_item(model_id),
        _expected_spend_item(model_id, request.effort, corpus_rows),
        _path_item(family, path),
        _version_item(path, probe),
    )
    return Disclosure(
        model_id=model_id,
        family=family,
        task_ids=tuple(request.task_ids),
        cap_usd=request.cap_usd,
        items=items,
        pending=PENDING_LINES,
        fingerprint=fingerprint(request.model, request.task_ids,
                                request.cap_usd),
    )


def _path_item(family, path):
    """OBSERVED: read off executor.INVOCATION_PATHS -- the same table
    execute() dispatches on, so what is disclosed is what will run."""
    if path is None:
        listed = ", ".join(sorted(executor.INVOCATION_PATHS)) or "none"
        return DisclosureItem(
            "invocation_path", OBSERVED,
            f"invocation path: none for family {family!r} -- observed from "
            f"executor.INVOCATION_PATHS (rows: {listed}); execute() will "
            f"report this task set as unsupported rather than fall back to "
            f"another auth surface")
    return DisclosureItem(
        "invocation_path", OBSERVED,
        f"invocation path: family={path.family} auth={path.auth} "
        f"binary={path.binary!r} base_url={path.base_url}; your key travels "
        f"as {', '.join(path.key_env_vars)} -- observed from "
        f"executor.INVOCATION_PATHS, the table execute() dispatches on")


def _probe_cli_version(binary, env):
    """Ask the binary itself, now. Raises on any non-answer; the caller turns
    the raise into an 'unavailable: <reason>' line. There is deliberately no
    recorded version string anywhere in this module to fall back to -- a
    constant would go stale the day the binary updates and keep printing."""
    proc = subprocess.run([binary, "--version"], env=env, text=True,
                          capture_output=True, timeout=60)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "no output"
        raise RuntimeError(
            f"{binary!r} --version exited {proc.returncode}: {detail}")
    answer = (proc.stdout or proc.stderr).strip()
    if not answer:
        raise RuntimeError(f"{binary!r} --version exited 0 with no output")
    return answer


def _version_item(path, probe):
    """OBSERVED either way: a version the binary answered with, or the fact
    that it did not answer, with the reason. Both are things that happened at
    disclosure time; neither is a constant someone recorded."""
    if path is None:
        return DisclosureItem(
            "cli_version", OBSERVED,
            "CLI version: unavailable: no invocation path, so there is no "
            "binary to ask")
    env = {name: os.environ[name]
           for name in executor.ENV_ALLOWLIST if name in os.environ}
    try:
        answer = probe(path.binary, env)
    except Exception as exc:
        return DisclosureItem(
            "cli_version", OBSERVED,
            f"CLI version: unavailable: {exc} -- {path.binary!r} --version "
            f"was asked at disclosure time and did not answer; no recorded "
            f"constant is substituted for the answer it did not give")
    return DisclosureItem(
        "cli_version", OBSERVED,
        f"CLI version: {answer} -- observed by running {path.binary!r} "
        f"--version at disclosure time, through the executor's allowlisted "
        f"environment and with no key injected (a version question does not "
        f"need one)")


def _billing_item(request, model_id, family):
    """INFERRED, and the item says why: billing_mode is a family-name test in
    usage_ledger.build_usage_row, not an observation of a bill. The label is
    read off a probe row built by the ledger's own function -- never restated
    here as a second string comparison -- along with the row's other
    provenance fields (ticket 32's pattern), so what is disclosed is what the
    ledger will actually stamp."""
    row = usage_ledger.build_usage_row(
        {"run_id": "disclosure-probe", "model": request.model,
         "tokens_in": 0, "tokens_out": 0},
        family, model_id=model_id)
    return DisclosureItem(
        "billing_mode", INFERRED,
        f"billing_mode: every ledger row for this run will be stamped "
        f"{row['billing_mode']!r} -- INFERRED: usage_ledger.build_usage_row "
        f"decides that label with a family-name test (family == 'kimi'), a "
        f"string comparison and not a bill anyone observed. The same row "
        f"carries its own provenance fields: scaffold_overhead_source="
        f"{row['scaffold_overhead_source']!r}, usd_estimate_kind="
        f"{row['usd_estimate_kind']!r}")


def _cost_item(model_id):
    """OBSERVED: whether the pricing machinery answers for this id is probed
    through the same call spend_cap's metering verdict uses. The unit is
    session-total tokens processed -- ticket 20 §4's ruling on which quantity
    bills -- and no money figure appears in either branch."""
    priced = usage_ledger.usd_estimate(
        model_id, spend_cap.PROBE_TOKENS_IN,
        spend_cap.PROBE_TOKENS_OUT) is not None
    unit = ("cost accrues in total tokens processed -- input, output, cache "
            "reads and cache writes summed, the session-total quantity the "
            "meter bills (ticket 20 §4's ruling)")
    if priced:
        text = (f"cost basis: {unit}. Pricing goes through "
                f"usage_ledger.usd_estimate, the ledger's own formula, which "
                f"returned a figure for {model_id} when probed at disclosure "
                f"time. Your declared cap is enforced by spend_cap.authorize "
                f"before every task; the cap's figure is bound into this "
                f"disclosure's fingerprint and deliberately not printed, "
                f"because ticket 08 records no verified prices and no slice "
                f"may print one")
    else:
        text = (f"cost basis: {unit} -- but usage_ledger.usd_estimate "
                f"returned no figure for {model_id} when probed at disclosure "
                f"time, so no run of it has a cost anyone can state, and "
                f"spend_cap.authorize will refuse it before any invocation "
                f"rather than run at an unknown price. No figure is printed "
                f"either way: ticket 08 records no verified prices")
    return DisclosureItem("cost_basis", OBSERVED, text)


def _expected_spend_item(model_id, effort, corpus_rows):
    """CORPUS: a statistic over past sealed-corpus runs, and labelled as one --
    what runs like this one have spent, not what this run will.

    14c Ruling 1's new element, satisfying ticket 43's "expected cost" AC:
    token-denominated, no money words. The quantity is OUTPUT tokens because
    that is the corpus's one comparable token axis -- ticket 31 struck the
    summed input side (stats.summary_tokens holds the ruling) -- so the line
    says so rather than borrowing the word "total" for a number that is not
    one. Rows pass the instrument's own clean-exit gate
    (corpus_gates.summarizable_rows), and the subjects are counted out loud in
    both arms: a figure over N rows and a refusal over 0 must not print alike.
    """
    rows = list(corpus_rows)
    kept, _ = corpus_gates.summarizable_rows(rows)
    mine = [r for r in kept
            if r.get("model") == model_id and r.get("effort") == effort]
    if not mine:
        return DisclosureItem(
            "expected_spend", CORPUS,
            f"expected spend: no figure -- of {len(rows)} corpus row(s) "
            f"inspected, {len(kept)} survived the clean-exit gate and 0 are "
            f"for {model_id} at effort={effort}. A median over zero runs is "
            f"indistinguishable from one over all of them, so none is quoted")
    by_task = {}
    for r in mine:
        by_task.setdefault(r.get("task"), []).append(stats.summary_tokens(r))
    medians = [statistics.median(v) for v in by_task.values()]
    return DisclosureItem(
        "expected_spend", CORPUS,
        f"expected spend: ~{_token_span(medians)} output tokens per task -- "
        f"per-task corpus medians for {model_id} at effort={effort}, over "
        f"{len(mine)} kept corpus run(s) across {len(by_task)} task(s) (14c "
        f"prototype outcome, Ruling 1). Output tokens are the corpus's one "
        f"comparable token axis (ticket 31); the billed session-total runs "
        f"higher than this figure")


def _token_span(values):
    """'9k-14k' across per-task medians, or the single figure when they agree
    at this rounding -- a span whose ends print alike is one number."""
    lo, hi = _k(min(values)), _k(max(values))
    return lo if lo == hi else f"{lo}-{hi}"


def _k(n):
    return f"{round(n / 1000)}k" if n >= 1000 else f"{round(n)}"


def format_disclosure(d):
    """The disclosure as the screen the visitor reads, gated on the FINISHED
    string by surface._refuse_money -- the only place a money figure added by
    any future edit to the items above has to pass through."""
    lines = [f"gauntlet-playground pre-run disclosure -- {d.model_id} "
             f"(family {d.family}), {len(d.task_ids)} task(s): "
             f"{', '.join(d.task_ids)}"]
    for item in d.items:
        lines.append(f"  [{item.label}] {item.text}")
    # 14c Ruling 1's gutter: pending lines group under a header that names
    # what they are, and the whole gutter disappears when nothing is pending
    # -- an absent header means an empty slot, not a dropped renderer, and
    # the tests assert both directions.
    if d.pending:
        lines.append("  not yet stated on this screen:")
        for line in d.pending:
            lines.append(f"    {line}")
    lines.append(
        f"  to acknowledge, pass this disclosure back: "
        f"executor.execute(request, ..., disclosed=<this disclosure>). It is "
        f"bound to (model, task set, cap) as fingerprint {d.fingerprint} and "
        f"authorizes exactly that run")
    return surface._refuse_money("\n".join(lines))

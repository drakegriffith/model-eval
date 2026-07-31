"""disclosure.py -- what a visitor is told BEFORE their key spends (ticket 43).

WHAT THIS MODULE IS. The pre-run disclosure: four labelled facts -- the
invocation path, the CLI version, the billing-mode label, and the cost basis --
assembled into a `Disclosure` the caller must hand back to executor.execute as
`disclosed=`. The hand-back is the acknowledgement. Its fingerprint binds it to
(model, task_ids, cap_usd), so a disclosure shown for one run does not
authorize a different one, and execute() raises DisclosureRequired rather than
proceeding without a match. The gate lives in execute() itself, not in a CLI
front end, for spend_cap's reason: a gate enforced in the surface that asks
politely holds for exactly one caller, and the caller ticket 43 is about is the
one that reaches the executor directly.

OBSERVED vs INFERRED, and why every item carries one of the two words.
An observed fact was produced by the machinery at disclosure time: the path row
execute() will dispatch on, the version string the binary answered with just
now, the probe usage the pricing formula returned a figure for. An inferred
fact is a label something wrote down without observing it: billing_mode is
usage_ledger.build_usage_row's family-name test -- a string comparison -- and a
visitor deciding whether to trust "metered" is entitled to know the word came
from a name check and not from a bill anyone has seen. Printing both kinds
unmarked would let the weakest item borrow the strongest item's provenance.

WHAT IS PENDING IS RENDERED AS PENDING. Ticket 20 §9's asymmetry copy is not
written (§9 is still open), and 14c is open and unbuilt with no prototype
outcome. The two lines that would carry their content therefore name the gap
instead of filling it, and `asymmetry_sentence` raises NotSettled rather than
returning a draft: a sentence composed here would be §9 settled by side effect,
in a file nobody reviewing §9 would think to read.

NO MONEY WORDS. The finished string passes through surface._refuse_money, the
same structural gate the printed sentence uses, because this module holds the
visitor's cap and is one attribute access away from echoing it. The cap
participates in the fingerprint -- binding, never display -- and ticket 08
still records no verified prices, so no slice may print one.
"""
import hashlib
import json
import os
import subprocess
from typing import NamedTuple

import registry
import spend_cap
import usage_ledger

# Product-internal imports are legal under half B; `surface` is where the money
# gate already lives, and a second copy of it here would be the drift ticket 40
# AC#8 refuses for token counting. executor is imported for the env allowlist
# only -- the path row arrives as an argument, from the caller who looked it up
# in the same table execute() dispatches on.
from gauntlet_playground import executor, surface

OBSERVED = "observed"
INFERRED = "inferred"
LABELS = (OBSERVED, INFERRED)


class NotSettled(Exception):
    """Raised where settled copy would go, when the settling has not happened.

    Not a placeholder string and not a TODO: a caller asking for the asymmetry
    sentence gets this exception, which names the open ticket, instead of prose
    this module has no authority to draft.
    """


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
    items: tuple      # four DisclosureItem, each labelled
    pending: tuple    # the structural gaps, named -- see PENDING_LINES
    fingerprint: str


# The gaps this disclosure refuses to paper over, rendered by name. These are
# STRUCTURAL lines, not drafts-in-waiting: when ticket 20 §9 closes, the first
# line is replaced by the copy §9 settles on, and `asymmetry_sentence` starts
# returning it instead of raising.
PENDING_LINES = (
    "PENDING (ticket 20 §9): the asymmetry copy is not written and §9 is "
    "still open -- this line renders the gap by name rather than drafting the "
    "sentence; disclosure.asymmetry_sentence() raises NotSettled instead of "
    "composing one",
    "PENDING (14c): open, unbuilt, no prototype outcome -- no claim from it "
    "appears in this disclosure",
)


def asymmetry_sentence():
    """The asymmetry sentence -- NOT WRITTEN, and this function says so.

    A NotSettled path rather than a drafted one: the copy is ticket 20 §9's to
    settle, §9 is open, and 14c -- the other place an outcome could have come
    from -- is open and unbuilt with no prototype outcome to cite. Returning a
    draft from here would settle §9 by side effect in a module nobody
    reviewing §9 reads.
    """
    raise NotSettled(
        "the asymmetry copy is not written: ticket 20 §9 is still open, and "
        "14c (open, unbuilt, no prototype outcome) has produced nothing to "
        "cite. The disclosure carries the gap as a PENDING line by name; a "
        "sentence drafted here would be §9 settled by side effect")


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


def disclose(request, path, probe_version=None):
    """Build the pre-run disclosure for one executor.RunRequest.

    `path` is the executor.InvocationPath the run would take, or None for a
    family the table has no row for -- a pathless family still gets a
    disclosure, because execute() still owes it a report (an "unsupported"
    outcome), and the gate in front of that report still demands one.

    `probe_version` is injectable for the same reason execute()'s `invoke` is:
    the default asks the real binary, and tests that are not about the binary
    exercise the rest of the disclosure without shelling out.
    """
    probe = _probe_cli_version if probe_version is None else probe_version
    model_id, spec = registry.resolve_model(request.model)
    family = spec["family"]
    items = (
        _path_item(family, path),
        _version_item(path, probe),
        _billing_item(request, model_id, family),
        _cost_item(model_id),
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


def format_disclosure(d):
    """The disclosure as the screen the visitor reads, gated on the FINISHED
    string by surface._refuse_money -- the only place a money figure added by
    any future edit to the items above has to pass through."""
    lines = [f"gauntlet-playground pre-run disclosure -- {d.model_id} "
             f"(family {d.family}), {len(d.task_ids)} task(s): "
             f"{', '.join(d.task_ids)}"]
    for item in d.items:
        lines.append(f"  [{item.label}] {item.text}")
    for line in d.pending:
        lines.append(f"  {line}")
    lines.append(
        f"  to acknowledge, pass this disclosure back: "
        f"executor.execute(request, ..., disclosed=<this disclosure>). It is "
        f"bound to (model, task set, cap) as fingerprint {d.fingerprint} and "
        f"authorizes exactly that run")
    return surface._refuse_money("\n".join(lines))

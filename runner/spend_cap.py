#!/usr/bin/env python3
"""spend_cap.py -- the fail-closed spend cap (ticket 40).

WHY THIS IS IN THE CORE AND NOT IN THE SURFACE THAT ASKS FOR THE RUN.
A cap enforced in the UI layer holds for exactly one caller: the one that asks
politely. The CLI entry point, a test harness, a future batch mode and anything
ticket 41 eventually feeds all bypass it for free. This module sits below the
ledger it caps, so every caller reaches it on the way to spending, because every
caller has to spend to matter. runner/tests/test_spend_cap.py asserts that
positively -- it calls this module with no product frame anywhere on the stack
and the refusal still happens.

WHAT FAIL-CLOSED MEANS HERE, and what it does not mean. The failure mode this
module exists to prevent is not "cap set too high". It is "cap silently
inapplicable": a caller asks for a provider whose spend nobody can compute, the
cap reads the absent number as zero, decides zero is under the cap, and proceeds.
So an unmeterable provider is REFUSED, not run at an unknown price. That makes
the product start out able to run one family. The alternative is spending a
stranger's money against a number nobody can produce.

HOW METERING CAPABILITY IS DECIDED, and why not by family name.
usage_ledger.build_usage_row labels a row `billing_mode = "metered" if family ==
"kimi" else "subscription"`. That label is a NAME CHECK, and this module
deliberately does not read it. `metering_capability` instead EXERCISES the
metering path: it prices a probe usage through the same usd_estimate the ledger
bills with, and asks whether a number came back. A model is meterable when the
machinery that will charge for it can already produce a figure -- which is the
property the cap actually needs, and which moves the day a verified price lands
rather than the day someone edits a string comparison.

The consequence worth stating: adding a dated price for a claude id to
usage_ledger.PRICING makes that id meterable here with no edit to this file, and
deleting kimi's price makes kimi refused. Both directions are pinned by tests.
Widening the metered set is ticket 11's job (the parameter surface owns metering
coverage per family); this module refuses, and 11 is what makes fewer things get
refused. Do not add a second family test here.

WHAT THIS MODULE DOES NOT DO. It does not price a run's OUTPUT before the run --
nobody can. Its pre-spend number is a FLOOR: the scaffold overhead every run of
that model has been measured to consume before the task is even read
(usage_ledger.SCAFFOLD_FLOOR_TOKENS, probed 2026-07-25). A cap that cannot cover
the floor cannot cover any run, so refusing there is sound; a cap that clears the
floor is not a promise the run fits. The caller re-authorizes before each further
run with the spend so far, which is how a multi-task run stops.
"""
import argparse
from typing import NamedTuple

import registry
import usage_ledger

# Membership in B''s core, declared on disk for runner/import_gate.py, which
# reads this with ast and never imports the module. Same declaration, same
# reason, as every other core module: an import scan cannot tell "opened it and
# found nothing to object to" from "never opened it".
CORE_MODULE = True

# The probe usage, in tokens. Deliberately large and deliberately arbitrary: this
# is not an estimate of anything, it is a question put to the pricing machinery --
# "given a usage, do you return a number?". A one-token probe would answer the
# same question and round to zero, which is indistinguishable from "free".
PROBE_TOKENS_IN = 1_000_000
PROBE_TOKENS_OUT = 1_000_000


class SpendRefused(Exception):
    """Raised instead of spending. Carries the provider by name so the caller
    does not have to parse the message to report it.

    A refusal is not a warning and not a zero-cost estimate: control does not
    return to the spending path at all.
    """

    def __init__(self, model_id, family, reason):
        super().__init__(f"refused {model_id} (family {family}): {reason}")
        self.model_id = model_id
        self.family = family
        self.reason = reason


class Metering(NamedTuple):
    """What the cap could observe about one model's spend.

    `family` is carried for the MESSAGE, never for the decision -- a refusal that
    cannot name the provider is not actionable. `meterable` is decided entirely
    by `probe_usd` and `floor_tokens`, both of which are observations.
    """
    model_id: str
    family: str
    meterable: bool
    reason: str
    probe_usd: float | None
    floor_tokens: int | None
    floor_usd: float | None


class Authorization(NamedTuple):
    """Permission to spend once, with the numbers it was granted against."""
    model_id: str
    family: str
    cap_usd: float
    spent_usd: float
    floor_usd: float
    remaining_usd: float


def metering_capability(model):
    """Can this model's spend be observed? Raises ValueError on an unknown model
    (registry's rule, not restated here).

    Two observations, both made by calling the machinery rather than by reading a
    label off it:

    1. Pricing answers. `usage_ledger.usd_estimate` is the function that will
       compute what the run cost; if it returns None for a probe usage there is
       no verified price for this id and the run's cost is not a number anyone
       has. A non-positive answer to a million-token probe is the same finding
       wearing a zero.
    2. A floor exists. Refusing BEFORE spending needs a lower bound on what a run
       consumes, and the measured scaffold overhead is that bound. Without it the
       cap could only ever notice an overrun after the money was gone.
    """
    model_id, spec = registry.resolve_model(model)
    family = spec["family"]

    probe_usd = usage_ledger.usd_estimate(model_id, PROBE_TOKENS_IN, PROBE_TOKENS_OUT)
    floor_tokens = usage_ledger.SCAFFOLD_FLOOR_TOKENS.get(model_id)

    if probe_usd is None:
        return Metering(
            model_id, family, False,
            f"spend cannot be observed: pricing {PROBE_TOKENS_IN} in / "
            f"{PROBE_TOKENS_OUT} out through usage_ledger.usd_estimate returned no "
            f"number for {model_id}, so no run of it has a cost this cap could "
            f"compare against a limit",
            None, floor_tokens, None)

    if probe_usd <= 0:
        return Metering(
            model_id, family, False,
            f"spend cannot be observed: a {PROBE_TOKENS_IN}-token probe prices at "
            f"${probe_usd} for {model_id} -- a rate that cannot distinguish a large "
            f"run from a small one is not a meter",
            probe_usd, floor_tokens, None)

    if floor_tokens is None:
        return Metering(
            model_id, family, False,
            f"spend cannot be bounded before it happens: no measured scaffold floor "
            f"for {model_id} in usage_ledger.SCAFFOLD_FLOOR_TOKENS, so the cheapest "
            f"possible run of it has no price and the cap could only ever object "
            f"after the money was spent",
            probe_usd, None, None)

    floor_usd = usage_ledger.usd_estimate(model_id, floor_tokens, 0)
    return Metering(
        model_id, family, True,
        f"metered: usd_estimate prices this id, and its measured scaffold floor of "
        f"{floor_tokens} tokens costs ${floor_usd:.4f} before any task work",
        probe_usd, floor_tokens, floor_usd)


def authorize(model, cap_usd, spent_usd=0.0):
    """Permission to spend, or SpendRefused. Call this BEFORE each run.

    `spent_usd` is what the caller has already spent under this cap, so a
    multi-run caller re-authorizes each time and stops when the remainder can no
    longer cover a run's floor. It is a parameter rather than state read from the
    ledger because "spent under this cap" is the caller's window -- one session,
    one job, one sweep -- and this module must not guess which.

    A cap of None is refused, not treated as unlimited. An absent limit and an
    infinite one are the same bytes to a reader, and only one of them is a
    decision somebody made.
    """
    m = metering_capability(model)
    if not m.meterable:
        raise SpendRefused(m.model_id, m.family, m.reason)

    if cap_usd is None:
        raise SpendRefused(
            m.model_id, m.family,
            "no spend cap was declared, and an undeclared cap is refused rather "
            "than read as unlimited")

    remaining = cap_usd - spent_usd
    if remaining < m.floor_usd:
        raise SpendRefused(
            m.model_id, m.family,
            f"cap ${cap_usd:.4f} with ${spent_usd:.4f} already spent leaves "
            f"${remaining:.4f}, under the ${m.floor_usd:.4f} scaffold floor a run of "
            f"{m.model_id} costs before it reads the task")

    return Authorization(m.model_id, m.family, cap_usd, spent_usd, m.floor_usd, remaining)


def cost_of(model_id, usage_detail):
    """What a completed run actually cost, through the ledger's own formula.

    Raises ValueError when the price is gone. Reaching here means `authorize`
    already observed a price for this id, so its absence now is an inconsistency
    to fail loudly on -- returning 0.0 would silently un-meter a run that was
    authorized precisely because it was meterable.
    """
    usd = usage_ledger.usd_estimate(model_id,
                                    usage_detail["tokens_in"],
                                    usage_detail["tokens_out"],
                                    usage_detail["cache_read_tokens"],
                                    usage_detail["cache_creation_tokens"])
    if usd is None:
        raise ValueError(
            f"{model_id} was authorized as meterable but usage_ledger.usd_estimate "
            f"now returns no price for it -- the run has already happened and its "
            f"cost is unknown")
    return usd


def metering_report(model_ids=None):
    """Count what the cap can and cannot meter, over the whole registry.

    Both counts are reported because they are different claims. `refused: 0` over
    this registry would mean the cap covers every declared provider, which is not
    true today and would more likely mean the probe stopped probing.
    """
    ids = sorted(registry.MODELS if model_ids is None else model_ids)
    verdicts = [metering_capability(i) for i in ids]
    meterable = [v for v in verdicts if v.meterable]
    refused = [v for v in verdicts if not v.meterable]
    meterable_families = sorted({v.family for v in meterable})
    return {
        "inspected": len(verdicts),
        "meterable_ids": [v.model_id for v in meterable],
        "refused_ids": [v.model_id for v in refused],
        "meterable_families": meterable_families,
        # A family is refused when NO id in it is meterable. Split this way rather
        # than by counting refused ids so that the day one claude id gets a
        # verified price, claude stops being a refused family without every other
        # claude id having to move at the same time.
        "refused_families": sorted({v.family for v in refused}
                                   - set(meterable_families)),
        "verdicts": verdicts,
    }


def format_metering_report(report):
    lines = [
        f"spend cap -- metering coverage over {report['inspected']} declared models",
        f"  meterable: {len(report['meterable_ids'])} ids across "
        f"{len(report['meterable_families'])} families "
        f"({', '.join(report['meterable_families']) or 'none'})",
        f"  refused:   {len(report['refused_ids'])} ids across "
        f"{len(report['refused_families'])} families "
        f"({', '.join(report['refused_families']) or 'none'})",
    ]
    if not report["refused_ids"]:
        lines.append("  REFUSED 0 -- the cap claims it can meter every declared "
                     "provider. Confirm that before believing it.")
    lines.append("  decided by pricing a probe usage through "
                 "usage_ledger.usd_estimate, never by the family name")
    for v in report["verdicts"]:
        lines.append(f"    {'METER ' if v.meterable else 'REFUSE'} {v.model_id:28s} "
                     f"{v.reason}")
    return "\n".join(lines)


def main(argv=None):
    argparse.ArgumentParser(
        description="Report which models the fail-closed spend cap can meter."
    ).parse_args(argv)
    print(format_metering_report(metering_report()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

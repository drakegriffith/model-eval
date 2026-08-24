"""test_spend_cap.py -- the fail-closed spend cap, proven where it lives (ticket 40).

Three claims are load-bearing here, and each is the kind that reads as true
whether or not anyone checked it, so each is asserted positively:

  1. THE CAP IS IN THE CORE, not in the surface that asks for the run. AC#4. The
     cheap version of this test imports the cap and watches it refuse -- which is
     equally green if the cap only works because the product happened to set
     something up first. So the refusal is exercised from an interpreter where
     product/ is not even on the import path, and the raised exception's frames
     are walked to show none of them came from under product/.
  2. METERABILITY IS OBSERVED, NOT LOOKED UP BY NAME. AC#6. A test that only
     shows "kimi is meterable, claude is not" is satisfied by the very family
     name check this ticket exists to replace. Both directions are moved
     instead: give a claude id a price and it becomes meterable; take kimi's
     price away and kimi is refused. Only an observation moves that way.
  3. THE COUNTS ARE THE ONES THE TICKET NAMED. AC#5: one meterable family over
     the real registry, and a non-zero refused set. `refused: 0` would mean the
     probe stopped probing, not that coverage grew.

The registry and the pricing table are never edited on disk. Direction-of-
metering tests monkeypatch usage_ledger.PRICING for the duration of one test,
which is exactly the surface the claim is about.
"""
import os
import subprocess
import sys
import traceback

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")

sys.path.insert(0, RUNNER_DIR)
import registry  # noqa: E402
import spend_cap  # noqa: E402
import usage_ledger  # noqa: E402

# The one id with a verified price today (usage_ledger.PRICING) and therefore the
# only meterable id. Named once here so a test that stops meaning what it says
# when pricing widens fails on this line rather than misreporting.
METERED_ID = "kimi-k3"
# A claude id that has a measured scaffold floor but no price. It is refused
# today for the price alone, which is what makes it the right subject for the
# "give it a price and watch it move" direction.
UNPRICED_ID = "claude-opus-5"


# --------------------------------------------------------------------------- #
# AC#4 -- the cap holds for a caller that never touches the product surface.
# --------------------------------------------------------------------------- #

def test_the_cap_refuses_with_no_product_frame_anywhere_on_the_stack():
    """The refusal is produced by core code called from core-only code.

    Both halves of the stack are inspected, because they answer different
    questions: the exception's traceback holds the frames between this call and
    the raise (did the cap route through the product on its way to refusing?),
    and the live stack holds the frames above it (was this test reached through
    a product entry point?). Neither may name a file under product/.
    """
    with pytest.raises(spend_cap.SpendRefused) as excinfo:
        spend_cap.authorize(UNPRICED_ID, cap_usd=1000.0)

    tb_files = [f.filename for f in traceback.extract_tb(excinfo.value.__traceback__)]
    stack_files = [f.filename for f in traceback.extract_stack()]
    assert tb_files, "no traceback frames captured -- nothing was inspected"
    for filename in tb_files + stack_files:
        assert not os.path.abspath(filename).startswith(PRODUCT_DIR + os.sep), (
            f"a product frame is on the stack of a core refusal: {filename}")

    # And the refusal names the provider, per AC#5.
    assert excinfo.value.model_id == UNPRICED_ID
    assert excinfo.value.family == "claude"
    assert "claude" in str(excinfo.value)


def test_the_cap_refuses_in_an_interpreter_that_cannot_even_import_the_product():
    """The same claim, made where it cannot be an accident of test ordering.

    In-process, `"gauntlet_playground" not in sys.modules` is not a fact about
    the cap -- test_product_boundary.py imports the product package earlier in
    the same session, so the assertion would report on the suite rather than on
    the module under test. A fresh interpreter with only runner/ on PYTHONPATH
    is where the claim is real: the product is not importable there at all, and
    the refusal still happens.
    """
    script = (
        "import sys\n"
        "import spend_cap\n"
        f"assert 'gauntlet_playground' not in sys.modules, sorted(sys.modules)\n"
        "try:\n"
        f"    spend_cap.authorize({UNPRICED_ID!r}, cap_usd=1000.0)\n"
        "except spend_cap.SpendRefused as exc:\n"
        "    print('REFUSED', exc.model_id, exc.family)\n"
        "else:\n"
        "    raise SystemExit('the cap did not refuse an unpriced provider')\n"
        "import importlib\n"
        "try:\n"
        "    importlib.import_module('gauntlet_playground')\n"
        "except ModuleNotFoundError:\n"
        "    print('PRODUCT UNIMPORTABLE')\n"
        "else:\n"
        "    raise SystemExit('product/ was on the path -- claim not proven')\n"
    )
    env = dict(os.environ, PYTHONPATH=RUNNER_DIR)
    proc = subprocess.run([sys.executable, "-c", script], env=env,
                          cwd=os.path.dirname(REPO_ROOT), text=True,
                          capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert f"REFUSED {UNPRICED_ID} claude" in proc.stdout
    assert "PRODUCT UNIMPORTABLE" in proc.stdout


def test_the_cap_is_a_declared_core_module():
    """AC#3's in-module third of the three-place cost. The gate reads this
    declaration off disk with ast; this asserts the value the module actually
    carries at import time, which is the other way of reading the same line."""
    assert spend_cap.CORE_MODULE is True


# --------------------------------------------------------------------------- #
# AC#6 -- decided by observed metering capability, not by the family name.
# --------------------------------------------------------------------------- #

def test_pricing_a_claude_id_makes_it_meterable_with_no_edit_to_the_cap():
    """Direction one. `claude-opus-5` is refused today because usd_estimate
    returns None for it, not because "claude" is in its name. Give the pricing
    table an entry and the same call returns meterable -- which a family-name
    check could not do.
    """
    before = spend_cap.metering_capability(UNPRICED_ID)
    assert before.meterable is False

    priced = dict(usage_ledger.PRICING)
    priced[UNPRICED_ID] = {"in_fresh": 15.0, "in_cache_read": 1.50,
                           "in_cache_write": 18.75, "out": 75.0,
                           "date": "2026-07-30",
                           "source": "test fixture -- not a verified price"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(usage_ledger, "PRICING", priced)
        after = spend_cap.metering_capability(UNPRICED_ID)

    assert after.meterable is True, after.reason
    assert after.family == "claude"
    assert after.floor_usd is not None and after.floor_usd > 0
    # And the authorization path agrees -- meterability is not a separate opinion
    # from permission to spend.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(usage_ledger, "PRICING", priced)
        auth = spend_cap.authorize(UNPRICED_ID, cap_usd=1000.0)
    assert auth.model_id == UNPRICED_ID and auth.remaining_usd > auth.floor_usd

    # The table is restored -- the claim is about the cap, not about the fixture.
    assert UNPRICED_ID not in usage_ledger.PRICING
    assert spend_cap.metering_capability(UNPRICED_ID).meterable is False


def test_deleting_kimis_price_makes_kimi_refused():
    """Direction two, and the one that rules out the name check outright. If
    metering were `family == "kimi"`, kimi would stay meterable with its price
    deleted. It does not: the cap asks the pricing machinery and gets nothing.
    """
    assert spend_cap.metering_capability(METERED_ID).meterable is True

    unpriced = {k: v for k, v in usage_ledger.PRICING.items() if k != METERED_ID}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(usage_ledger, "PRICING", unpriced)
        verdict = spend_cap.metering_capability(METERED_ID)
        with pytest.raises(spend_cap.SpendRefused) as excinfo:
            spend_cap.authorize(METERED_ID, cap_usd=1000.0)

    assert verdict.meterable is False, verdict.reason
    assert verdict.family == "kimi"
    assert excinfo.value.family == "kimi"
    assert spend_cap.metering_capability(METERED_ID).meterable is True


def test_the_cap_does_not_read_the_ledgers_billing_mode_label():
    """The narrower form of AC#6, read off the source. `billing_mode` is the
    field produced by the family name check; the cap must not consult it, and a
    future edit that reaches for the convenient label fails here."""
    with open(spend_cap.__file__, "r", encoding="utf-8") as f:
        source = f.read()
    body = source.split('"""', 2)[-1]   # module docstring may discuss it
    assert "billing_mode" not in body
    assert 'family == "kimi"' not in body


# --------------------------------------------------------------------------- #
# AC#5 -- report the counts, and the counts are 1 family and a non-empty refusal.
# --------------------------------------------------------------------------- #

def test_metering_report_over_the_real_registry_meters_one_family_and_refuses_some():
    """The count the ticket named. Asserted over the real registry rather than a
    fixture, because the number that matters is coverage of the providers this
    instrument actually declares."""
    report = spend_cap.metering_report()

    assert report["inspected"] == len(registry.MODELS)
    assert len(report["meterable_families"]) == 1, report["meterable_families"]
    assert report["meterable_families"] == ["kimi"]
    assert report["meterable_ids"] == [METERED_ID]
    assert report["refused_ids"] != [], (
        "refused 0 over this registry would mean the probe stopped probing")
    assert len(report["refused_ids"]) == len(registry.MODELS) - 1
    # studio/local-family added an unmetered "local" family alongside the two
    # pre-existing unmetered ones; re-derived from the registry rather than
    # re-hardcoded, so this stays true the next time a family is added too.
    assert set(report["refused_families"]) == (
        {spec["family"] for spec in registry.MODELS.values()} - {"kimi"})


def test_the_formatted_report_states_both_counts():
    """AC#5 asks for the counts to be reported, not merely computed."""
    text = spend_cap.format_metering_report(spend_cap.metering_report())
    refused_n = len(registry.MODELS) - 1  # every model but the one metered id
    refused_families = sorted({spec["family"] for spec in registry.MODELS.values()}
                              - {"kimi"})
    assert "meterable: 1 ids across 1 families (kimi)" in text
    assert (f"refused:   {refused_n} ids across {len(refused_families)} families "
           f"({', '.join(refused_families)})") in text
    assert "never by the family name" in text
    assert "REFUSED 0" not in text


def test_the_cap_cli_prints_the_report():
    proc = subprocess.run([sys.executable, os.path.join(RUNNER_DIR, "spend_cap.py")],
                          env=dict(os.environ, PYTHONPATH=RUNNER_DIR),
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert "meterable: 1 ids" in proc.stdout
    assert f"METER  {METERED_ID}" in proc.stdout


# --------------------------------------------------------------------------- #
# The refusal boundaries themselves.
# --------------------------------------------------------------------------- #

def test_a_cap_under_the_scaffold_floor_is_refused_before_any_spend():
    """The core half of AC#7's negative control (the ledger half lives in
    runner/tests/test_product_executor.py, where there is a ledger to count)."""
    floor = spend_cap.metering_capability(METERED_ID).floor_usd
    assert floor > 0
    with pytest.raises(spend_cap.SpendRefused) as excinfo:
        spend_cap.authorize(METERED_ID, cap_usd=floor / 2)
    assert "scaffold floor" in str(excinfo.value)
    assert excinfo.value.model_id == METERED_ID

    # And the boundary is not a blanket refusal of the metered provider.
    assert spend_cap.authorize(METERED_ID, cap_usd=floor * 2).remaining_usd == floor * 2


def test_spend_already_made_is_subtracted_before_the_floor_comparison():
    """How a multi-task run stops: the second authorize sees the first one's
    spend, and the remainder is what is compared against the floor."""
    floor = spend_cap.metering_capability(METERED_ID).floor_usd
    ok = spend_cap.authorize(METERED_ID, cap_usd=floor * 3, spent_usd=floor)
    assert ok.remaining_usd == pytest.approx(floor * 2)
    with pytest.raises(spend_cap.SpendRefused):
        spend_cap.authorize(METERED_ID, cap_usd=floor * 3, spent_usd=floor * 2.5)


def test_an_undeclared_cap_is_refused_rather_than_read_as_unlimited():
    with pytest.raises(spend_cap.SpendRefused) as excinfo:
        spend_cap.authorize(METERED_ID, cap_usd=None)
    assert "undeclared cap" in str(excinfo.value)


def test_an_unknown_model_raises_rather_than_being_quietly_unmeterable():
    """Registry's rule, not restated in the cap. An unknown id must not fall
    through the meterable check as "no price, therefore refused" -- that would
    report a typo as a provider-coverage finding."""
    with pytest.raises(ValueError):
        spend_cap.metering_capability("claude-opus-6-does-not-exist")


def test_cost_of_bills_through_the_ledgers_own_formula():
    """AC#8's core half: the product does not get a second counting rule. The
    expected number here is computed by calling usage_ledger directly, so the
    test fails if the cap ever grows its own arithmetic."""
    detail = {"tokens_in": 100_000, "tokens_out": 5_000,
              "cache_read_tokens": 60_000, "cache_creation_tokens": 10_000}
    expected = usage_ledger.usd_estimate(METERED_ID, detail["tokens_in"],
                                         detail["tokens_out"],
                                         detail["cache_read_tokens"],
                                         detail["cache_creation_tokens"])
    assert spend_cap.cost_of(METERED_ID, detail) == expected
    assert expected > 0


def test_cost_of_fails_loudly_when_the_price_vanished_after_authorization():
    """Returning 0.0 here would silently un-meter a run that was authorized
    precisely because it was meterable."""
    detail = {"tokens_in": 1000, "tokens_out": 100,
              "cache_read_tokens": 0, "cache_creation_tokens": 0}
    unpriced = {k: v for k, v in usage_ledger.PRICING.items() if k != METERED_ID}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(usage_ledger, "PRICING", unpriced)
        with pytest.raises(ValueError, match="authorized as meterable"):
            spend_cap.cost_of(METERED_ID, detail)

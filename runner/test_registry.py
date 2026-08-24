"""Interface tests for runner/registry.py (ticket 30).

These are load-bearing, not confirmatory. Before this file existed the repo had
NO test asserting on any of the nine registry symbols -- verified by grep over
runner/test_*.py and runner/tests/test_*.py, whose only match was a docstring
mention in test_usage_ledger.py:199. The extraction out of run.py therefore had
no regression net under it, and this file IS that net.

What the contract in registry.py promises, and so what is tested here:
  - resolution is total over {canonical ids} u {aliases} and raises on anything
    else -- never a default family, because a mis-resolved family silently
    changes how usage is parsed;
  - an undeclared effort tier raises before anything is spent;
  - `metered` is answered per model, both ways.

Every loop over the registry asserts how many subjects it inspected. A test that
iterates an accidentally-empty dict passes while proving nothing, and that
failure is byte-identical to a real pass.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registry  # noqa: E402
from registry import (  # noqa: E402
    ALIASES, CLAUDE_TIERS, CODEX_TIERS, CODEX_TIERS_6, MODELS,
    check_effort, is_metered, model_family, resolve_model,
)


# --------------------------------------------------------------------------- #
# resolve_model -- canonical ids
# --------------------------------------------------------------------------- #
def test_resolve_model_is_identity_on_every_canonical_id():
    """A canonical id resolves to itself and hands back its own record."""
    inspected = []
    for mid, expected_spec in MODELS.items():
        got_id, got_spec = resolve_model(mid)
        assert got_id == mid
        assert got_spec is expected_spec
        inspected.append(mid)

    assert len(inspected) == len(MODELS)
    assert len(inspected) >= 16, f"roster shrank unexpectedly: {inspected}"


def test_resolve_model_returns_the_spec_not_a_copy():
    """Callers read `family`/`efforts`/`metered` straight off the returned spec."""
    _, spec = resolve_model("claude-opus-5")

    assert spec["family"] == "claude"
    assert spec["efforts"] == CLAUDE_TIERS
    assert "metered" not in spec


# --------------------------------------------------------------------------- #
# resolve_model -- aliases
# --------------------------------------------------------------------------- #
def test_every_alias_resolves_to_its_canonical_id():
    """Old runs.yaml files and archived results.jsonl rows name aliases."""
    inspected = []
    for alias, canonical in ALIASES.items():
        got_id, got_spec = resolve_model(alias)
        assert got_id == canonical, f"{alias} -> {got_id}, expected {canonical}"
        assert got_spec is MODELS[canonical]
        inspected.append(alias)

    assert set(inspected) == set(ALIASES), (
        f"inspected {sorted(inspected)}, ALIASES holds {sorted(ALIASES)}")
    assert len(inspected) == 4, f"alias set changed: {sorted(inspected)}"


def test_alias_and_canonical_id_resolve_identically():
    """`fable` and `claude-fable-5` must be one measurement, not two."""
    assert resolve_model("fable") == resolve_model("claude-fable-5")


def test_hybrid_is_an_alias_of_fable_not_its_own_model():
    """hybrid is a MODE -- Fable orchestrating -- and the registry says so."""
    assert resolve_model("hybrid")[0] == "claude-fable-5"
    assert "hybrid" not in MODELS


def test_every_alias_target_exists_in_models():
    """A dangling alias would raise for the caller, not here, and much later."""
    for alias, canonical in ALIASES.items():
        assert canonical in MODELS, f"alias {alias} points at absent {canonical}"
    assert len(ALIASES) == 4


# --------------------------------------------------------------------------- #
# resolve_model -- unknown input fails closed
# --------------------------------------------------------------------------- #
def test_unknown_model_raises_and_never_defaults_a_family():
    """The contract's central promise: no fallback family, ever."""
    with pytest.raises(ValueError) as excinfo:
        resolve_model("claude-opus-6")

    msg = str(excinfo.value)
    assert "claude-opus-6" in msg
    assert "known ids" in msg and "aliases" in msg


def test_unknown_model_message_lists_what_was_legal():
    """A typo has to be fixable without opening registry.py."""
    with pytest.raises(ValueError) as excinfo:
        resolve_model("fabel")  # transposed alias

    msg = str(excinfo.value)
    assert "fable" in msg
    assert "claude-fable-5" in msg


@pytest.mark.parametrize("bad", ["", "  ", "CLAUDE-OPUS-5", "claude-opus-5 ", None])
def test_empty_case_shifted_and_padded_names_all_raise(bad):
    """Resolution is exact. No trimming, no case folding, no empty-means-default."""
    with pytest.raises(ValueError):
        resolve_model(bad)


def test_model_family_raises_on_unknown_rather_than_returning_unknown():
    """model_family is a thin wrapper; it must inherit the fail-closed behavior."""
    with pytest.raises(ValueError):
        model_family("gpt-9")


# --------------------------------------------------------------------------- #
# model_family
# --------------------------------------------------------------------------- #
def test_model_family_on_each_family_via_id_and_alias():
    assert model_family("claude-opus-5") == "claude"
    assert model_family("gpt-5.6-sol") == "codex"
    assert model_family("kimi-k3") == "kimi"
    assert model_family("sol") == "codex"
    assert model_family("kimi") == "kimi"
    assert model_family("glm-4.7-local") == "local"
    assert model_family("qwen3-coder-next-local") == "local"


def test_every_model_declares_a_family_and_a_nonempty_tier_list():
    """Downstream dispatches on family; an absent one would KeyError mid-sweep."""
    inspected = 0
    for mid, spec in MODELS.items():
        assert spec.get("family") in ("claude", "codex", "kimi", "local"), mid
        assert spec.get("efforts"), f"{mid} declares no effort tiers"
        assert all(isinstance(t, str) and t for t in spec["efforts"]), mid
        inspected += 1

    assert inspected == len(MODELS) >= 16


# --------------------------------------------------------------------------- #
# is_metered -- both paths
# --------------------------------------------------------------------------- #
def test_is_metered_true_on_the_metered_path():
    """Kimi is real money; the spend meter and --max-usd gate read this."""
    assert is_metered("kimi-k3") is True
    assert is_metered("kimi") is True


def test_is_metered_false_on_the_unmetered_path():
    """Subscription-driven ids report False, not None -- callers use `is`."""
    assert is_metered("claude-opus-5") is False
    assert is_metered("gpt-5.6-sol") is False
    assert is_metered("fable") is False


def test_exactly_one_model_is_metered_today():
    """A row silently gaining `metered` would change what --max-usd governs."""
    metered = sorted(mid for mid in MODELS if is_metered(mid))

    assert metered == ["kimi-k3"], f"metered roster changed: {metered}"


def test_is_metered_raises_on_unknown_model():
    with pytest.raises(ValueError):
        is_metered("kimi-k4")


# --------------------------------------------------------------------------- #
# check_effort -- legal and illegal, per family
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model,effort", [
    ("claude-opus-5", "max"),      # claude tops out at max
    ("claude-opus-5", "low"),
    ("gpt-5.5", "xhigh"),          # 4-tier codex id
    ("gpt-5.6-sol", "ultra"),      # 6-tier codex id
    ("gpt-5.6-luna", "max"),       # codex id on the claude ladder
    ("kimi-k3", "high"),
    ("fable", "xhigh"),            # via alias
])
def test_check_effort_accepts_a_declared_tier(model, effort):
    """A legal tier returns None and raises nothing."""
    assert check_effort(model, effort) is None


@pytest.mark.parametrize("model,effort", [
    ("claude-opus-5", "ultra"),    # ultra is codex-6 only
    ("gpt-5.5", "max"),            # 4-tier id must reject the 5th and 6th
    ("gpt-5.5", "ultra"),
    ("gpt-5.6-luna", "ultra"),     # luna declares claude tiers, not codex-6
    ("kimi-k3", "ultra"),
    ("claude-opus-5", "HIGH"),     # tier names are exact, not case-folded
    ("sol", "extreme"),            # via alias
])
def test_check_effort_rejects_an_undeclared_tier(model, effort):
    """Fail closed before the sweep: the CLI would ignore it silently."""
    with pytest.raises(ValueError) as excinfo:
        check_effort(model, effort)

    msg = str(excinfo.value)
    assert effort in msg
    assert "declared" in msg


def test_check_effort_names_the_canonical_id_not_the_alias_in_its_error():
    """The operator fixing the config needs the id the tiers belong to."""
    with pytest.raises(ValueError) as excinfo:
        check_effort("sol", "extreme")

    assert "gpt-5.6-sol" in str(excinfo.value)


@pytest.mark.parametrize("effort", [None, "", 0, False])
def test_no_tier_asserted_is_always_legal(effort):
    """A caller that declares no tier cannot mistype one -- see the contract."""
    assert check_effort("claude-opus-5", effort) is None


def test_check_effort_rejects_an_unknown_model_before_looking_at_the_tier():
    """Model resolution runs first, so a bad model raises even with a legal tier."""
    with pytest.raises(ValueError) as excinfo:
        check_effort("gpt-5.7", "high")

    assert "unknown model" in str(excinfo.value)


def test_the_three_tier_ladders_are_distinct_and_ordered_as_documented():
    """CODEX_TIERS_6 exists precisely because sol/terra declare two more tiers."""
    assert CODEX_TIERS == ["low", "medium", "high", "xhigh"]
    assert CLAUDE_TIERS == CODEX_TIERS + ["max"]
    assert CODEX_TIERS_6 == CLAUDE_TIERS + ["ultra"]


# --------------------------------------------------------------------------- #
# Contract boundary -- the extraction stays a leaf, and run.py stays in sync
# --------------------------------------------------------------------------- #
def test_registry_carries_no_verification_date():
    """Ticket 30 AC#5: §4's draft listed one, the records do not hold one.

    Emitting a date here would be new behavior smuggled inside an extraction.
    This test is the guard on that decision, so adding it later is a deliberate
    act with its own ticket rather than an accident.
    """
    for mid, spec in MODELS.items():
        assert "verified" not in spec, mid
        assert "date" not in spec, mid


def test_registry_answers_nothing_about_price():
    """`pricing` is a separate, gated ticket; dollars are not a registry fact."""
    for mid, spec in MODELS.items():
        assert not any(k.startswith("price") for k in spec), mid
        assert "usd" not in spec, mid


def test_registry_imports_nothing_from_the_runner():
    """The point of the extraction: a leaf caller must not pull in run.py.

    usage_ledger.py imported the 1300-line worker to answer "what family is
    this model". If registry ever imports run (or usage_ledger, or broker), that
    burden comes straight back and the cycle returns with it.
    """
    src = open(registry.__file__, encoding="utf-8").read()

    for forbidden in ("import run", "import usage_ledger", "import broker",
                      "import sandbox_seal"):
        assert forbidden not in src, f"registry.py grew a dependency: {forbidden}"


def test_run_py_reexports_are_the_same_objects():
    """run.py keeps re-exports so the import switch is atomic (AC#4).

    Same objects, not merely equal ones: a copied dict would drift silently and
    the two halves of the instrument would disagree about the roster.
    """
    import run  # noqa: E402  -- run.py imports registry, not the reverse

    assert run.MODELS is registry.MODELS
    assert run.ALIASES is registry.ALIASES
    assert run.CLAUDE_TIERS is registry.CLAUDE_TIERS
    assert run.CODEX_TIERS is registry.CODEX_TIERS
    assert run.CODEX_TIERS_6 is registry.CODEX_TIERS_6
    assert run.resolve_model is registry.resolve_model
    assert run.model_family is registry.model_family
    assert run.is_metered is registry.is_metered
    assert run.check_effort is registry.check_effort


def test_usage_ledger_reaches_the_registry_without_importing_run():
    """First-consumer proof (AC#3), asserted rather than assumed."""
    import usage_ledger  # noqa: E402

    assert usage_ledger.registry is registry

    src = open(usage_ledger.__file__, encoding="utf-8").read()
    assert "import run" not in src, "the local `import run` came back"

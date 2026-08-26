#!/usr/bin/env python3
"""registry.py -- the model registry: which models exist and what they accept.

INTERFACE CONTRACT (ticket 30; drafted as `registry` v0.1 in
projects/frontier-benchmark/14b-repo-boundary-doctrine-consult.md §4). This
comment is the contract; the code below implements it. Change one and you are
changing the other.

Behavior
    Resolves a caller-supplied model name -- alias or canonical CLI id -- to a
    canonical id and its record, and answers three questions about it: which CLI
    family drives it, whether a run costs real money, and which effort tiers it
    legally accepts. This is the single resolution point in the instrument:
    command construction, usage parsing, key injection, the spend meter and the
    offline ledger retrofit all come through here, so an alias expands once and
    by one rule.

In  model: str -- an alias ("fable") or a canonical CLI id ("claude-fable-5").
    effort: str | None -- an effort tier name; None/"" means "no tier asserted"
            and is always legal (a caller that declares no tier cannot mistype
            one).

Out canonical id (str) · family (str: "claude" | "codex" | "kimi" | "local") ·
    metered (bool) · the declared legal tier list (list[str]).
    Nothing here returns a price, and nothing here returns a date -- see
    Limitations.

Errors
    Fail closed, always by raising ValueError:
      - unknown model raises. It never falls back to a default family, because a
        mis-resolved family silently changes how usage is parsed and the run
        would be mismetered rather than rejected.
      - an effort tier the model does not declare raises, before anything is
        spent. A misspelled tier that a CLI silently ignores produces a sweep of
        identical runs wearing different labels -- exactly the artefact the
        effort ladder exists to rule out.
    Both messages name what was legal, so the caller can fix the typo without
    reading this file.

Limitations
    - Hand-maintained facts. The source of truth for ids and tiers is
      runner/CLI-FACTS.md (declared tiers from ~/.codex/models_cache.json for
      codex and `claude --help` for claude); ticket 02's probe is what checks
      them against reality. This module does not verify itself.
    - DECLARED tiers are not real tiers. Whether a tier actually moves spend is
      effort_verdict.py's verdict, not a fact stated here. Declared is only
      enough to reject a typo before a 4M-token sweep launches.
    - No verification date is carried. §4's draft listed "the date the facts were
      verified" as an output; that date does not exist in these records
      (§7 panel finding), so emitting one here would be new behavior invented
      inside an extraction. Sourcing it from runner/CLI-FACTS.md is a separate
      ticket with its own test (ticket 30 AC#5).
    - Says nothing about price. `pricing` is a separate module with its own
      ticket, gated on per-row provenance flags; dollars live in usage_ledger.py
      today.
    - No I/O, no environment reads, no network. Stdlib only, and in fact
      import-free: this module is a leaf so that leaf callers (usage_ledger.py)
      need not import the 1300-line worker to ask what family a model is.
"""

# Membership in B''s core, declared on disk for runner/import_gate.py, which reads
# this with ast and never imports the module. The declaration exists because this
# module is import-free: an import scan alone cannot tell "opened it and found
# nothing to object to" from "never opened it". Deleting this line does not
# quietly shrink the core -- it fails the gate.
CORE_MODULE = True

# --------------------------------------------------------------------------- #
# Model registry
# --------------------------------------------------------------------------- #
# Every model the runner can invoke, keyed by the canonical CLI id from
# runner/CLI-FACTS.md. Three behaviours vary across the roster and nothing else
# does: which CLI binary drives it (`family`), which effort tiers it will accept
# (`efforts`), and whether a run costs real money (`metered`). Everything
# downstream -- command construction, usage parsing, key injection, spend
# metering -- dispatches on `family`, so adding a model is a row here rather than
# a new branch in four functions.
#
# `efforts` is the DECLARED tier set (source: ~/.codex/models_cache.json for
# codex, `claude --help` for claude). Declared is not the same as real -- whether
# a tier moves spend is what effort_verdict.py decides -- but declared is enough
# to reject a typo before a 4M-token sweep launches.
CLAUDE_TIERS = ["low", "medium", "high", "xhigh", "max"]
CODEX_TIERS = ["low", "medium", "high", "xhigh"]
CODEX_TIERS_6 = ["low", "medium", "high", "xhigh", "max", "ultra"]

MODELS = {
    # claude family -- driven by `claude -p`, subscription auth
    "claude-opus-5":             {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-opus-5[1m]":         {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-opus-4-8":           {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-fable-5":            {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-sonnet-5":           {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-haiku-4-5":          {"family": "claude", "efforts": CLAUDE_TIERS},
    "claude-haiku-4-5-20251001": {"family": "claude", "efforts": CLAUDE_TIERS},
    # codex family -- driven by `codex exec`, subscription auth. sol and terra are
    # the only two ids declaring six tiers; the rest stop at xhigh.
    "gpt-5.6-sol":               {"family": "codex", "efforts": CODEX_TIERS_6},
    "gpt-5.6-terra":             {"family": "codex", "efforts": CODEX_TIERS_6},
    "gpt-5.6-luna":              {"family": "codex", "efforts": CLAUDE_TIERS},
    "gpt-5.5":                   {"family": "codex", "efforts": CODEX_TIERS},
    "gpt-5.4":                   {"family": "codex", "efforts": CODEX_TIERS},
    "gpt-5.4-mini":              {"family": "codex", "efforts": CODEX_TIERS},
    "gpt-5.3-codex-spark":       {"family": "codex", "efforts": CODEX_TIERS},
    "codex-auto-review":         {"family": "codex", "efforts": CODEX_TIERS},
    # kimi family -- Claude Code binary pointed at Moonshot's Anthropic-compatible
    # endpoint, injected API key, real money. Separate family from `claude`
    # precisely because those last two facts differ.
    "kimi-k3":                   {"family": "kimi", "efforts": CLAUDE_TIERS,
                                  "metered": True},
    # local family -- same Claude Code binary pointed at an LM Studio server on
    # loopback (studio/local-family), no key, no spend. `efforts` here is
    # CLAUDE_TIERS by inheritance of the same --effort flag the claude binary
    # takes for every family riding it, NOT because anyone has confirmed LM
    # Studio's Anthropic-compatible endpoint does anything with the tier.
    # Whether a tier moves anything is a measured question, not a hand-set
    # one: effort_verdict.py classifies each model's ladder as credited (REAL,
    # spread exceeds same-tier noise) or not-yet-credited from the actual
    # probe_endpoints.py data, so there is no separate flag here to drift out
    # of sync with that measurement.
    "glm-4.7-local":             {"family": "local", "efforts": CLAUDE_TIERS},
    "qwen3-coder-next-local":    {"family": "local", "efforts": CLAUDE_TIERS},
}

# Short names used by the existing runs.yaml files and already written into
# results.jsonl. Kept so old configs and old rows stay readable; new configs may
# name either an alias or a canonical id. `hybrid` is a MODE, not a model -- it
# runs Fable as an orchestrator (see run.HYBRID_INSTRUCTION), and pointing it
# here states that plainly instead of hiding it in a build_cli_cmd branch.
ALIASES = {
    "fable": "claude-fable-5",
    "hybrid": "claude-fable-5",
    "sol": "gpt-5.6-sol",
    "kimi": "kimi-k3",
}


def resolve_model(model):
    """Return (canonical_id, spec) for an alias or canonical CLI id.

    Single resolution point: build_cli_cmd, parse_usage, run_cli, the spend
    meter and usage_ledger's retrofit all come through here, so an alias is
    expanded once and by one rule. Raises ValueError on an unknown name -- a
    typo must not reach a paid CLI.
    """
    mid = ALIASES.get(model, model)
    spec = MODELS.get(mid)
    if spec is None:
        raise ValueError(
            f"unknown model {model!r}; known ids: {', '.join(sorted(MODELS))}; "
            f"aliases: {', '.join(sorted(ALIASES))}")
    return mid, spec


def model_family(model):
    return resolve_model(model)[1]["family"]


def is_metered(model):
    """True when a run of this model costs real money (Kimi only today)."""
    return bool(resolve_model(model)[1].get("metered"))


def check_effort(model, effort):
    """Reject an effort tier the model does not declare, before anything is spent.

    Fail-closed on purpose: a misspelled tier that the CLI silently ignores would
    produce a sweep of identical runs wearing different labels, which is exactly
    the artefact the effort ladder exists to rule out.
    """
    mid, spec = resolve_model(model)
    if effort and effort not in spec["efforts"]:
        raise ValueError(
            f"model {mid} does not declare effort {effort!r}; "
            f"declared: {', '.join(spec['efforts'])}")

#!/usr/bin/env python3
"""Context-rot v3: latent association and cross-window synthesis.

Pre-registered in V3-PREREGISTRATION.md before any run. Read that file first;
the thresholds, arms, statistics, and spend plan live there and are fixed.

v2 scored 1251/1251 keys correct at every length through 700k. That is not
evidence long context is fine. Exact lexical retrieval produces a sharp logit
spike that survives softmax dilution, so it is the one task the dilution
mechanism cannot touch. v3 measures the two things v2 did not:

  latent  PRIMARY. The value is stated in ordinary prose with zero lexical
          overlap with the question, and reaching it needs one inference hop:
          "the gateway abandons a request after a quarter of a second" asked as
          `timeout_ms`, answer 250. This is NoLiMa's task (arXiv:2502.05167),
          which no Claude model has been published on and which nobody has run
          past 128K. Semantic decoys state a superseded value in equally
          plausible prose, so a miss is attributable.

  synth   NOVEL. Ten services, each with `retry_budget` planted in the shallow
          half of the log and `timeout_ms` in the deep half. The questions are
          SELECTIONS, not computations ("among services with retry_budget >= X,
          which has the highest timeout_ms"), so what is measured is the
          cross-window join and not arithmetic. Every question's answer is
          constructed to differ from the globally-highest timeout_ms, so a model
          that ignores the shallow-half filter scores zero on it rather than
          being carried by a plausible guess.

  exact   v2's retrieval task, minus the arithmetic line. Control only, run at
          the pilot length as a within-v3 sanity check that the harness and the
          scorer still agree with v2's 100%.

Reuses `corpus()` and `invoke()` from run.py rather than restating them, so the
padding and the subscription-authenticated invocation path are the same code v2
ran. v2's `build_prompt` is generalized here into `assemble(head, tail, blocks)`,
keeping the three properties that were hard-won: cut points resolved against the
pristine padding (otherwise inserted blocks become boundaries that later blocks
snap onto and every fact chain-stacks into one band), insertion deepest-first,
and a windowed boundary snap so a fact does not drift percent-scale off its
intended depth. Planted material counts against the length budget. Positions are
measured in the finished prompt, never assumed.

Scoring is deterministic string and integer comparison. No judge.

Usage:
  # pilot: calibrate difficulty until the 5k base lands in the 90-95 band
  python3 experiments/context-rot/run_v3.py --arm latent synth \\
      --instances 10 --lengths 5000 --difficulty 1 \\
      --out experiments/context-rot/results/context-rot-v3-pilot1.jsonl

  # grid, once calibrated
  python3 experiments/context-rot/run_v3.py --arm latent synth \\
      --instances 30 --lengths 5000 50000

  python3 experiments/context-rot/run_v3.py --summarize <file.jsonl>
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import (  # noqa: E402  shared with v2 on purpose
    CHARS_PER_TOKEN,
    DECOY_FORMS,
    DECOYS_PER_FACT,
    FACT_BLOCK,
    FACTS,
    corpus,
    invoke,
)

OUT = Path(__file__).resolve().parent / "results"
DEFAULT_LENGTHS = [5_000]
FACT_FILE = {k: fname for k, fname, _, _ in FACTS}
Z = 1.96


# ---------------------------------------------------------------------------
# shared assembler
# ---------------------------------------------------------------------------

def assemble(pad: str, head: str, tail: str, blocks: list[dict],
             target_tokens: int) -> tuple[str, dict]:
    """Insert `blocks` into padding at their requested depths; measure where they land.

    A block is {"id", "depth", "text", "probe"}: `text` is planted, `probe` is a
    substring unique to that block, used afterwards to measure its true fraction
    of the finished prompt.
    """
    overhead = len(head) + len(tail) + sum(len(b["text"]) for b in blocks)
    budget = int(target_tokens * CHARS_PER_TOKEN)
    # The max(500, ...) floor below silently ships a prompt LONGER than the
    # target when the planted material alone overflows it, which turns the 5k
    # control into a 5.2k control while every row still says 5000. Degradation
    # is defined as the 5k-to-long gap on an identical task, so a control that
    # is not the length it claims corrupts the only comparison this experiment
    # makes. Fail instead, and say by how much.
    if overhead + 500 > budget:
        sys.exit(f"assemble: planted material is {overhead:,} chars but a "
                 f"{target_tokens:,}-token prompt budgets {budget:,}. This prompt "
                 f"would be longer than the length it reports. Lower difficulty, "
                 f"lower hops, or raise the shortest length.")
    want = max(500, budget - overhead)
    body = pad[:want] if want <= len(pad) else pad * (want // len(pad) + 1)
    body = body[:want]

    cuts, used = [], set()
    window = max(200, len(body) // 50)
    for b in blocks:
        raw = max(1, min(len(body) - 1, int(len(body) * b["depth"])))
        cut = body.rfind("\n=== FILE:", max(0, raw - window), raw)
        if cut <= 0 or cut in used:
            cut = body.rfind("\n", max(0, raw - window), raw)
        if cut <= 0 or cut in used:
            cut = raw
        used.add(cut)
        cuts.append((cut, b["text"]))
    for cut, text in sorted(cuts, key=lambda ct: -ct[0]):
        body = body[:cut] + text + body[cut:]

    prompt = head + body + tail
    positions = {}
    for b in blocks:
        idx = prompt.find(b["probe"])
        positions[b["id"]] = round(idx / len(prompt), 4) if idx >= 0 else None
    return prompt, positions


def spread(n: int, lo: float = 0.05, hi: float = 0.95) -> list[float]:
    """Even depths so position stays a controlled variable, not a lottery."""
    if n == 1:
        return [(lo + hi) / 2]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


# ---------------------------------------------------------------------------
# arm: exact  (v2's retrieval task, control)
# ---------------------------------------------------------------------------

EXACT_CONTRACT = """\
OUTPUT CONTRACT (governs your entire reply, no exceptions):
- Reply with exactly {n} lines.
- Each line reports one requested value, in the order the question lists them,
  formatted as: <key_name>: <integer>
  Spell each key name exactly as the question spells it, lowercase.
- No prose, no explanation, no units, no restating the question, no code fences,
  no blank lines.

TASK BRIEF
Below is a session log: a long series of files that were read during an earlier
working session. A number of deploy settings are defined somewhere inside it. At
the end of the log you will be asked to report some of them. Read the log.

Key names are exact. The log also contains settings whose names merely resemble
the ones you will be asked for (for example `legacy_retry_budget` or
`retry_budget_default`). Those are different settings. Report only the value
defined for the exact key name requested.

=== BEGIN SESSION LOG ===
"""

EXACT_QUESTION = """\
=== END SESSION LOG ===

Using only the settings defined in the session log above, report the value of
each of these keys, in this order, one per line:

{keylist}

Follow the output contract stated at the top of this message.
"""


def instance_exact(seed: int, difficulty: int, hops: int = 1) -> dict:
    """v2's plantings and decoys. `difficulty` is deliberately ignored: this is
    the control, so its task must not move while the other arms are tuned."""
    if hops != 1:
        sys.exit("exact: hops is not implemented for the control arm; "
                 "run it at --hops 1 or drop it from --arm")
    rng = random.Random(seed)
    vals, blocks = {}, []

    for (key, fname, (lo, hi), mult), depth in zip(FACTS, spread(len(FACTS))):
        vals[key] = rng.randrange(lo, hi + 1) * mult
        blocks.append({
            "id": f"true:{key}", "depth": depth,
            "text": FACT_BLOCK.format(fname=fname, key=key, value=vals[key]),
            "probe": f"\n{key}: {vals[key]}\n",
        })

    decoy_values: dict[str, list[int]] = {}
    for key, fname, (lo, hi), mult in FACTS:
        for i, form in enumerate(rng.sample(DECOY_FORMS, DECOYS_PER_FACT)):
            offset = rng.choice([-3, -2, -1, 1, 2, 3]) * mult
            dv = max(mult, vals[key] + offset)
            if dv == vals[key]:
                dv = vals[key] + mult
            decoy_values.setdefault(key, []).append(dv)
            name = form.format(k=key)
            blocks.append({
                "id": f"decoy:{key}:{i}", "depth": rng.uniform(0.02, 0.98),
                "text": FACT_BLOCK.format(fname=fname, key=name, value=dv),
                "probe": f"\n{name}: {dv}\n",
            })

    order = [k for k, _, _, _ in FACTS]
    rng.shuffle(order)  # question order != depth order, so reading in sequence earns nothing
    items = [{"key": k, "answer": vals[k], "block_id": f"true:{k}",
              "decoys": decoy_values.get(k, [])} for k in order]

    return {
        "items": items, "blocks": blocks,
        "head": EXACT_CONTRACT.format(n=len(items)),
        "tail": EXACT_QUESTION.format(keylist="\n".join(f"    {i['key']}" for i in items)),
        "answer_type": "int",
        "exclude": [k for k, _, _, _ in FACTS],
    }


# ---------------------------------------------------------------------------
# arm: latent
# ---------------------------------------------------------------------------

# Each clause states the value in prose that shares no words with the key it
# answers, and needs one hop to turn into the integer: a number word, a unit
# conversion, a rate inverted into a period, or a small product. Four phrasings
# per key with four different values, so instances differ and a model cannot
# learn one mapping. Clauses are stored lowercase and planted verbatim, which is
# what makes them usable both as the live statement and as a superseded decoy.
LATENT_VARIANTS: dict[str, list[tuple[str, int]]] = {
    "retry_budget": [
        ("a failing call is repeated at most four more times before the caller gives up", 4),
        ("a call that fails is sent again up to seven times, then abandoned", 7),
        ("each request gets three second chances and no more", 3),
        ("a failed call may be replayed five further times before it is dropped", 5),
    ],
    "shard_count": [
        ("the keyspace is split across four racks, two partitions on each", 8),
        ("there are three partitions on every one of the two rings", 6),
        ("the key range is divided into a dozen equal slices", 12),
        ("each of the five zones owns exactly two slices of the key range", 10),
    ],
    "timeout_ms": [
        ("the gateway abandons a request after a quarter of a second", 250),
        ("a call is cut off once it has run for half a second", 500),
        ("the edge gives a slow call three quarters of a second, then drops it", 750),
        ("a connection is severed after a second and a half", 1500),
    ],
    "batch_size": [
        ("the writer flushes once thirty-two records have piled up", 32),
        ("records are held until five dozen have accumulated, then written together", 60),
        ("the loader writes in groups of one hundred and twenty eight", 128),
        ("sixteen records at a time are handed to the writer", 16),
    ],
    "queue_depth": [
        ("the buffer holds two hundred and fifty six items before back-pressure begins", 256),
        ("back-pressure starts at the sixty-fourth waiting item", 64),
        ("the ring accepts five hundred and twelve pending entries and no more", 512),
        ("ninety six entries may wait before producers are slowed", 96),
    ],
    "worker_slots": [
        ("three threads run on each of the eight cores", 24),
        ("every one of the four nodes runs five concurrent handlers", 20),
        ("the pool is two handlers wide on each of six machines", 12),
        ("each of the nine boxes carries four simultaneous handlers", 36),
    ],
    "flush_interval_ms": [
        ("the writer drains twice a second", 500),
        ("buffers are emptied four times a second", 250),
        ("a drain happens every fifth of a second", 200),
        ("the writer empties its buffer once every three seconds", 3000),
    ],
    "max_payload_kb": [
        ("an upload larger than half a megabyte is rejected", 512),
        ("anything above a quarter of a megabyte is refused at the edge", 256),
        ("bodies over two megabytes are turned away", 2048),
        ("the edge refuses uploads heavier than one and a half megabytes", 1536),
    ],
    "drain_grace_s": [
        ("on shutdown the process waits a minute and a half for in-flight work", 90),
        ("a stopping node is given two full minutes to finish what it started", 120),
        ("shutdown allows half a minute for outstanding work", 30),
        ("the process is given three minutes to settle before it is killed", 180),
    ],
}

# Framing that carries no numbers of its own, so it cannot be mistaken for the fact.
LEADS = [
    "This was settled during the last capacity review.",
    "This came out of the incident retro in the spring.",
    "The platform team owns this decision.",
    "The number below has been stable for several quarters.",
    "Support asked for this to be written down somewhere findable.",
]
TRAILS = [
    "Nobody has proposed changing it since.",
    "It is enforced in the edge configuration, not in application code.",
    "Changing it requires a review from the on-call lead.",
    "The same rule applies in staging.",
    "This is the value the runbook checks against during an incident.",
]

# --hops 2. The value is never stated, in any unit, anywhere in the window. A
# platform block states a base quantity; a service block, planted in the other
# half of the window, states this service's fraction of it. Neither block alone
# yields an answer, so the model has to carry one fact to the other and only
# then convert units. That is the same cross-window join synth measures, on the
# arm where lexical overlap with the question is already zero.
#
# Fractions stay trivial (halves, quarters, thirds) on purpose: the thing under
# test is holding two separated facts together, not arithmetic. If a miss ever
# looks like bad division rather than a bad join, this data is the wrong
# instrument and the fractions are the first thing to simplify.
LATENT_ANCHORS: dict[str, str] = {
    "retry_budget": "no single call may be sent again more than twelve times before an operator is paged",
    "shard_count": "the cluster owns twenty four slices of the key range in total",
    "timeout_ms": "a call chain is given one full second from first byte to last",
    "batch_size": "the widest write the loader will ever issue is two hundred and fifty six records",
    "queue_depth": "the ring is built with room for one thousand and twenty four entries",
    "worker_slots": "the tier is provisioned with seventy two concurrent handlers in total",
    "flush_interval_ms": "the longest gap between writes the platform tolerates is one full second",
    "max_payload_kb": "the edge refuses any body above two megabytes, whatever a service asks for",
    "drain_grace_s": "a hard kill lands four minutes after the stop signal",
}

# Four relations per key, same shape as LATENT_VARIANTS, so the decoy knob keeps
# its meaning: variant[0] is live, the next `difficulty` are superseded rules
# pointing at the same anchor. A decoy that shares the anchor competes properly,
# because discarding it needs the recency rule, not a second lookup.
LATENT_HOP2_VARIANTS: dict[str, list[tuple[str, int]]] = {
    "retry_budget": [
        ("this service gives up at a third of the platform ceiling", 4),
        ("this service is held to half the platform ceiling", 6),
        ("this service stops at a quarter of what the platform permits", 3),
        ("this service is allowed two thirds of the platform ceiling", 8),
    ],
    "shard_count": [
        ("this table takes half of the cluster total", 12),
        ("this table is spread over a third of the cluster total", 8),
        ("this table claims a quarter of the cluster total", 6),
        ("this table sits on a sixth of the cluster total", 4),
    ],
    "timeout_ms": [
        ("the edge abandons its own leg at a quarter of the chain allowance", 250),
        ("the edge gives its own leg half the chain allowance", 500),
        ("the edge allows itself three quarters of the chain allowance", 750),
        ("the edge cuts its own leg at a tenth of the chain allowance", 100),
    ],
    "batch_size": [
        ("this job writes in groups of half the widest write", 128),
        ("this job uses a quarter of the widest write", 64),
        ("this job writes an eighth of the widest write at a time", 32),
        ("this job hands over a sixteenth of the widest write per call", 16),
    ],
    "queue_depth": [
        ("back-pressure begins once a quarter of the ring is occupied", 256),
        ("back-pressure begins at half the ring", 512),
        ("back-pressure begins once an eighth of the ring is taken", 128),
        ("back-pressure begins at a sixteenth of the ring", 64),
    ],
    "worker_slots": [
        ("this pool is sized at a third of the tier", 24),
        ("this pool takes half the tier", 36),
        ("this pool is a quarter of the tier", 18),
        ("this pool is a sixth of the tier", 12),
    ],
    "flush_interval_ms": [
        ("this writer drains at half the tolerated gap", 500),
        ("this writer drains at a quarter of the tolerated gap", 250),
        ("this writer drains at a fifth of the tolerated gap", 200),
        ("this writer drains at a tenth of the tolerated gap", 100),
    ],
    "max_payload_kb": [
        ("this endpoint accepts a quarter of the edge maximum", 512),
        ("this endpoint accepts half the edge maximum", 1024),
        ("this endpoint accepts an eighth of the edge maximum", 256),
        ("this endpoint accepts three quarters of the edge maximum", 1536),
    ],
    "drain_grace_s": [
        ("this process is given a quarter of the time before the hard kill", 60),
        ("this process is given half the time before the hard kill", 120),
        ("this process is given an eighth of the time before the hard kill", 30),
        ("this process is given three quarters of the time before the hard kill", 180),
    ],
}

LATENT_ANCHOR_BLOCK = """\

=== FILE: docs/platform/{fname} ===
# {title}

{lead} Across the whole platform, {anchor}. {trail}

"""

LATENT_BLOCK = """\

=== FILE: docs/runbooks/{fname} ===
# {title}

{lead} The rule in force is that {clause}. {trail}

"""

LATENT_DECOY_BLOCK = """\

=== FILE: docs/runbooks/{fname} ===
# {title}

{lead} An earlier revision of this runbook recorded that {clause}. That guidance
was superseded at a later review and no longer describes the system.

"""

LATENT_CONTRACT = """\
OUTPUT CONTRACT (governs your entire reply, no exceptions):
- Reply with exactly {n} lines.
- Each line reports one requested value, in the order the question lists them,
  formatted as: <key_name>: <integer>
  Spell each key name exactly as the question spells it, lowercase.
- The value is a bare integer in the unit named by the key. No units, no
  decimals, no thousands separators.
- No prose, no explanation, no reasoning, no restating the question, no code
  fences, no blank lines.

TASK BRIEF
Below is a session log: a long series of files read during an earlier working
session. Somewhere inside it, a handful of runbook pages describe how this
service is configured. They describe the settings in ordinary prose rather than
writing them out as key/value pairs, so each number has to be worked out from
what the page says.

Where a page records that an earlier decision was later superseded, report the
value that is in force now, not the one that was replaced.

=== BEGIN SESSION LOG ===
"""

LATENT_QUESTION = """\
=== END SESSION LOG ===

Using only what the session log above states, report the current value of each
of these settings, in this order, one per line:

{keylist}

Follow the output contract stated at the top of this message.
"""


def instance_latent(seed: int, difficulty: int, hops: int = 1) -> dict:
    """`difficulty` = semantic decoys per key (0-3), `hops` = inference steps.

    The two knobs are orthogonal on purpose. Decoys change how much competition
    a fact has; hops change how far apart the pieces of one answer sit. Pilots 1
    to 3 moved decoys to their ceiling and never left 100%, which is why hops
    exists at all: it is a different axis, not a bigger number on the same one.
    """
    rng = random.Random(seed)
    table = LATENT_VARIANTS if hops == 1 else LATENT_HOP2_VARIANTS
    keys = list(table)
    # Same reason as synth's guard: silently clamping difficulty 4 back to 3
    # would write difficulty=4 into rows that actually ran at 3. The decoy knob
    # genuinely ends at len(variants)-1; raising latent past that needs new
    # variant prose, not a bigger number.
    if hops not in (1, 2):
        sys.exit(f"latent: hops={hops} is not implemented (1 or 2)")
    ceiling = min(len(v) for v in table.values()) - 1
    if difficulty > ceiling:
        sys.exit(f"latent: difficulty={difficulty} exceeds the decoy ceiling "
                 f"({ceiling}) at hops={hops}; add variant prose to go higher")
    ndec = max(0, difficulty)

    # At two hops the anchor sits in the shallow half and the relation that
    # depends on it in the deep half, so no answer can be assembled from one
    # region of the window. At one hop the fact is whole and spans the range,
    # which is pilots 1 to 3 unchanged.
    depths = spread(len(keys), 0.55, 0.95) if hops == 2 else spread(len(keys))
    anchor_depths = spread(len(keys), 0.05, 0.45)

    items, blocks = [], []
    for idx, (key, depth) in enumerate(zip(keys, depths)):
        variants = list(table[key])
        rng.shuffle(variants)
        clause, value = variants[0]
        fname = FACT_FILE[key].replace(".yaml", ".md")
        title = fname.replace(".md", "").replace("_", " ").title()
        if hops == 2:
            afname = FACT_FILE[key].replace(".yaml", "_policy.md")
            blocks.append({
                "id": f"anchor:{key}", "depth": anchor_depths[idx],
                "text": LATENT_ANCHOR_BLOCK.format(
                    fname=afname, title=afname.replace(".md", "").replace("_", " ").title(),
                    anchor=LATENT_ANCHORS[key],
                    lead=rng.choice(LEADS), trail=rng.choice(TRAILS)),
                "probe": LATENT_ANCHORS[key],
            })
        blocks.append({
            "id": f"true:{key}", "depth": depth,
            "text": LATENT_BLOCK.format(fname=fname, title=title, clause=clause,
                                        lead=rng.choice(LEADS), trail=rng.choice(TRAILS)),
            "probe": clause,
        })
        decoys = []
        for i, (dclause, dvalue) in enumerate(variants[1:1 + ndec]):
            decoys.append(dvalue)
            blocks.append({
                "id": f"decoy:{key}:{i}", "depth": rng.uniform(0.02, 0.98),
                "text": LATENT_DECOY_BLOCK.format(fname=fname, title=title, clause=dclause,
                                                  lead=rng.choice(LEADS)),
                "probe": dclause,
            })
        items.append({"key": key, "answer": value, "block_id": f"true:{key}",
                      "decoys": decoys})

    rng.shuffle(items)  # question order != depth order
    return {
        "items": items, "blocks": blocks,
        "head": LATENT_CONTRACT.format(n=len(items)),
        "tail": LATENT_QUESTION.format(keylist="\n".join(f"    {i['key']}" for i in items)),
        "answer_type": "int",
        "exclude": keys,
    }


# ---------------------------------------------------------------------------
# arm: synth
# ---------------------------------------------------------------------------

SERVICE_NAMES = [
    "ironwood", "saltmarsh", "quillfeather", "dunlin", "marlstone", "glasswort",
    "kestrel", "thornbury", "wickfield", "ambergate", "redpoll", "halloway",
    "pinemoor", "greyling",
    # Added for difficulty 4-5. Same register as the first fourteen (one word,
    # no shared prefix, none a substring of another) so that pool size stays
    # the only thing that changes when difficulty moves.
    "bramblecote", "fennroyd", "coldharbour", "sedgewick", "millbank",
    "starnwood", "harrowgate", "brackwater",
]
SYNTH_QUESTIONS = 4
SYNTH_MIN_CANDIDATES = 3
# Difficulty is the number of services, i.e. how many cross-window pairs have to
# be joined before anything can be compared. Difficulty 1 is the pre-registered
# ten. Filter selectivity is NOT the knob: a wider surviving set forces the
# thresholds low, and then the top timeout_ms stops changing as the filter
# moves, which collapses four questions into one asked four ways.
SYNTH_SERVICES = {0: 6, 1: 10, 2: 12, 3: 14, 4: 18, 5: 22}

SYNTH_RETRY_BLOCK = """\

=== FILE: deploy/{svc}/limits.yaml ===
# generated by ops-sync, do not edit by hand
service: {svc}
retry_budget: {value}
region: us-east-1

"""

SYNTH_TIMEOUT_BLOCK = """\

=== FILE: deploy/{svc}/gateway.yaml ===
# generated by ops-sync, do not edit by hand
service: {svc}
timeout_ms: {value}
region: us-east-1

"""

SYNTH_CONTRACT = """\
OUTPUT CONTRACT (governs your entire reply, no exceptions):
- Reply with exactly {n} lines.
- Each line answers one question, in the order the questions are numbered,
  formatted as: q<number>: <service_name>
- A service name is copied exactly as the log spells it, lowercase. Answer with
  a name only. Never answer with a number.
- No prose, no explanation, no reasoning, no restating the question, no code
  fences, no blank lines.

TASK BRIEF
Below is a session log: a long series of files read during an earlier working
session. Somewhere inside it are the deploy files for several services. Each
service has its `retry_budget` recorded in one file and its `timeout_ms`
recorded in a different file, and the two files for a service sit far apart in
the log. At the end you will be asked a few questions, each of which picks out
one service.

Each question filters the services on `retry_budget` first, then selects among
only the services that pass the filter. A service that fails the filter is not
an answer no matter what its `timeout_ms` is.

=== BEGIN SESSION LOG ===
"""

SYNTH_QUESTION = """\
=== END SESSION LOG ===

Using only the deploy files in the session log above, answer these questions.
Each answer is the name of exactly one service.

{qlist}

Follow the output contract stated at the top of this message.
"""


def _synth_draw(rng: random.Random, nsvc: int, minc: int, min_distinct: int):
    """One candidate draw. Returns None if it cannot support clean questions.

    Clean means: no tie for the top `timeout_ms` inside any filtered set, and
    every question's answer differs from the globally-highest `timeout_ms`
    service. Without that second constraint a model could skip the shallow half
    of the log entirely, always name the global maximum, and score well.
    """
    services = rng.sample(SERVICE_NAMES, nsvc)
    timeouts = rng.sample([100 + 50 * i for i in range(28)], nsvc)  # distinct by construction
    retries = {s: rng.randrange(1, 10) for s in services}
    timeout = dict(zip(services, timeouts))

    global_max = max(services, key=lambda s: timeout[s])
    valid = []
    for x in range(1, 10):
        cands = [s for s in services if retries[s] >= x]
        if len(cands) < minc or global_max in cands:
            continue
        best = max(cands, key=lambda s: timeout[s])
        valid.append({"x": x, "answer": best, "candidates": sorted(cands)})
    if len(valid) < SYNTH_QUESTIONS:
        return None
    # Prefer thresholds whose answers differ from each other, so four questions
    # are four observations rather than the same one asked four ways.
    chosen, seen = [], set()
    for q in valid:
        if q["answer"] not in seen:
            chosen.append(q)
            seen.add(q["answer"])
    for q in valid:
        if len(chosen) >= SYNTH_QUESTIONS:
            break
        if q not in chosen:
            chosen.append(q)
    if len(seen) < min_distinct:
        # Four thresholds that all select the same service are one observation
        # asked four ways. Redraw until the answer actually moves with the filter.
        return None
    chosen = sorted(chosen[:SYNTH_QUESTIONS], key=lambda q: q["x"])
    return services, retries, timeout, global_max, chosen


def instance_synth(seed: int, difficulty: int, hops: int = 1) -> dict:
    # synth is already a two-hop join by construction (filter on one fact,
    # compare another). It has no separate hops knob, so asking for one is an
    # error rather than a silently ignored flag.
    if hops != 1:
        sys.exit("synth: hops is not a knob on this arm (its join is structural); "
                 "run it at --hops 1 or drop it from --arm")
    rng = random.Random(seed)
    # Hard error, not .get(default): an unmapped difficulty used to fall back to
    # ten services, so --difficulty 4 ran EASIER than 3 while every result row
    # still recorded difficulty=4. A calibration run that silently mislabels its
    # own difficulty is worse than one that does not start.
    if difficulty not in SYNTH_SERVICES:
        sys.exit(f"synth: no service count mapped for difficulty={difficulty} "
                 f"(mapped: {sorted(SYNTH_SERVICES)})")
    nsvc = SYNTH_SERVICES[difficulty]
    # Both constraints scale with the pool: a six-service draw cannot support
    # three distinct answers and a three-service floor at the same time.
    minc, min_distinct = (3, 3) if nsvc >= 8 else (2, 2)
    for _ in range(2000):
        drawn = _synth_draw(rng, nsvc, minc, min_distinct)
        if drawn:
            break
    else:
        sys.exit(f"synth: no clean draw for seed={seed} difficulty={difficulty}")
    services, retries, timeout, global_max, qs = drawn

    blocks = []
    # retry_budget lives in the shallow half, timeout_ms in the deep half, so
    # every question is a join across the window rather than a local lookup.
    for svc, depth in zip(services, spread(len(services), 0.05, 0.45)):
        blocks.append({
            "id": f"retry:{svc}", "depth": depth,
            "text": SYNTH_RETRY_BLOCK.format(svc=svc, value=retries[svc]),
            "probe": f"service: {svc}\nretry_budget: {retries[svc]}\n",
        })
    deep = list(services)
    rng.shuffle(deep)  # shallow order != deep order
    for svc, depth in zip(deep, spread(len(deep), 0.55, 0.95)):
        blocks.append({
            "id": f"timeout:{svc}", "depth": depth,
            "text": SYNTH_TIMEOUT_BLOCK.format(svc=svc, value=timeout[svc]),
            "probe": f"service: {svc}\ntimeout_ms: {timeout[svc]}\n",
        })

    items, qlines = [], []
    for n, q in enumerate(qs, 1):
        qlines.append(f"    q{n}: among the services whose retry_budget is {q['x']} or "
                      f"greater, which one has the highest timeout_ms?")
        items.append({"key": f"q{n}", "answer": q["answer"], "block_id": None,
                      "decoys": [], "threshold": q["x"], "candidates": q["candidates"]})

    return {
        "items": items, "blocks": blocks,
        "head": SYNTH_CONTRACT.format(n=len(items)),
        "tail": SYNTH_QUESTION.format(qlist="\n".join(qlines)),
        "answer_type": "name",
        "exclude": ["retry_budget", "timeout_ms"] + services,
        "meta": {"services": services, "retries": retries, "timeout": timeout,
                 "global_max": global_max},
    }


ARMS = {"exact": instance_exact, "latent": instance_latent, "synth": instance_synth}


def instance(arm: str, seed: int, difficulty: int, hops: int = 1) -> dict:
    inst = ARMS[arm](seed, difficulty, hops)
    inst.update({"seed": seed, "arm": arm, "difficulty": difficulty, "hops": hops})
    return inst


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.+?)\s*$")


def score(reply: str, inst: dict) -> dict:
    lines = [l.strip() for l in (reply or "").strip().splitlines() if l.strip()]
    claimed: dict[str, str] = {}
    for line in lines:
        m = LINE.fullmatch(line)
        if m:
            claimed[m.group(1).lower()] = m.group(2).strip().strip(".`")

    per_key, miss_kind = {}, {}
    meta = inst.get("meta") or {}
    for it in inst["items"]:
        key, truth = it["key"], it["answer"]
        raw = claimed.get(key)
        if inst["answer_type"] == "int":
            m = re.fullmatch(r"-?\d+", raw or "")
            got = int(raw) if m else None
        else:
            got = (raw or "").lower().strip() or None

        per_key[key] = got == truth
        if got is None:
            miss_kind[key] = "missing" if raw is None else "unparseable"
        elif got != truth:
            if inst["answer_type"] == "int":
                miss_kind[key] = "decoy" if got in it["decoys"] else "other"
            elif got == meta.get("global_max"):
                # named the highest timeout_ms overall: the filter was ignored.
                miss_kind[key] = "global_max"
            elif got in it.get("candidates", []):
                miss_kind[key] = "in_filter_wrong"
            elif got in (meta.get("services") or []):
                miss_kind[key] = "outside_filter"
            else:
                miss_kind[key] = "not_a_service"

    n_items = len(inst["items"])
    n_correct = sum(per_key.values())
    fmt = (len(lines) == n_items and all(LINE.fullmatch(l) for l in lines))
    return {"per_key": per_key, "miss_kind": miss_kind,
            "n_items": n_items, "n_items_correct": n_correct,
            "all_items_correct": n_correct == n_items,
            "format_ok": fmt, "claimed": claimed}


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def one_run(pad, inst, tokens, model, timeout, retries=1) -> dict:
    prompt, positions = assemble(pad, inst["head"], inst["tail"], inst["blocks"], tokens)
    for attempt in range(retries + 1):
        res = invoke(prompt, model, timeout)
        if "error" not in res and (res.get("reply") or "").strip():
            break
        if attempt < retries:
            time.sleep(5)
    row = {"seed": inst["seed"], "arm": inst["arm"], "difficulty": inst["difficulty"],
           "hops": inst.get("hops", 1),
           "target_tokens": tokens, "model": model, "attempts": attempt + 1,
           "prompt_chars": len(prompt),
           "answers": {i["key"]: i["answer"] for i in inst["items"]},
           "thresholds": {i["key"]: i["threshold"] for i in inst["items"]
                          if "threshold" in i} or None,
           "item_positions": {i["key"]: positions.get(i["block_id"])
                              for i in inst["items"] if i["block_id"]} or None,
           "positions": positions, **res}
    if "reply" in res:
        row.update(score(res["reply"], inst))
        row["reply"] = res["reply"][:400]
    return row


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def wilson(p: float, n: int, z: float = Z) -> tuple[float, float]:
    """Wilson score interval. Used because the outcome is a proportion near 1,
    where the normal approximation has zero width and says nothing."""
    if n <= 0:
        return (0.0, 1.0)
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - half) / d), min(1.0, (centre + half) / d))


def summarize(rows: list[dict]) -> None:
    scored = [r for r in rows if "n_items_correct" in r]
    if not scored:
        print("no scored rows")
        return

    for arm in sorted({r["arm"] for r in scored}):
        arows = [r for r in scored if r["arm"] == arm]
        by: dict[int, list[dict]] = {}
        for r in arows:
            by.setdefault(r["target_tokens"], []).append(r)

        diffs = sorted({r.get("difficulty") for r in arows})
        print(f"\n=== arm={arm}  difficulty={','.join(str(d) for d in diffs)} ===")
        print(f"{'target':>8} {'measured_in':>12} {'inst':>5} {'items':>6} {'item_acc':>9} "
              f"{'wilson95':>15} {'all_ok':>7} {'format':>7} {'med_s':>7}")

        acc_by_len = {}
        for t in sorted(by):
            rs = by[t]
            n = len(rs)                                   # n counts INSTANCES: keys inside
            items = sum(r["n_items"] for r in rs)         # one instance share a haystack and
            acc = sum(r["n_items_correct"] for r in rs) / items   # are clustered, not independent.
            lo, hi = wilson(acc, n)                       # so the CI uses n, not `items`.
            allk = sum(r["all_items_correct"] for r in rs) / n
            fmt = sum(r["format_ok"] for r in rs) / n
            meas = sorted(r.get("input_tokens") or 0 for r in rs)[n // 2]
            secs = sorted(r["wall_s"] for r in rs)[n // 2]
            acc_by_len[t] = acc
            print(f"{t:>8} {meas:>12,} {n:>5} {items:>6} {acc:>8.1%} "
                  f"{f'[{lo:.1%}, {hi:.1%}]':>15} {allk:>6.0%} {fmt:>6.0%} {secs:>7.1f}")

        # Pre-registered readouts. Base is the shortest length run; effective
        # length is NoLiMa's rule, adopted unchanged so the bar is not ours.
        base_len = min(acc_by_len)
        base = acc_by_len[base_len]
        print(f"\nbase ({base_len:,} tok): {base:.1%}", end="")
        if len(acc_by_len) == 1:
            band = "IN BAND" if 0.90 <= base <= 0.95 else (
                "ABOVE BAND, raise difficulty" if base > 0.95 else
                "BELOW BAND, lower difficulty")
            print(f"   calibration target 90-95%: {band}")
        else:
            thresh = 0.85 * base
            ok = [t for t, a in acc_by_len.items() if a >= thresh]
            eff = max(ok) if ok else None
            print(f"   85% floor: {thresh:.1%}   effective length: "
                  f"{f'{eff:,}' if eff else 'below the shortest length run'}")
            longest = max(acc_by_len)
            drop = base - acc_by_len[longest]
            print(f"degradation {base_len:,} -> {longest:,}: {drop*100:+.1f} points "
                  f"({base:.1%} -> {acc_by_len[longest]:.1%})")
            below = [t for t in sorted(acc_by_len) if acc_by_len[t] < thresh]
            if eff and below and min(below) < eff:
                print(f"NOTE: non-monotonic. Lengths below the 85% floor: "
                      f"{', '.join(f'{t:,}' for t in below)}")

        # Depth, pooled across lengths, where an item maps to one planted block.
        buckets: dict[int, list[bool]] = {}
        for r in arows:
            for key, ok in r["per_key"].items():
                pos = (r.get("item_positions") or {}).get(key)
                if pos is not None:
                    buckets.setdefault(min(9, int(pos * 10)), []).append(ok)
        if buckets:
            print(f"\n{'depth':>10} {'n':>6} {'item_acc':>9}   (all lengths pooled)")
            for b in sorted(buckets):
                v = buckets[b]
                print(f"{b*10:>3}-{b*10+10:<6} {len(v):>6} {sum(v)/len(v):>8.1%}")
        elif arm == "synth":
            # No 1:1 item->block map here, so verify the split held instead.
            sh = [p for r in arows for k, p in (r.get("positions") or {}).items()
                  if k.startswith("retry:") and p is not None]
            dp = [p for r in arows for k, p in (r.get("positions") or {}).items()
                  if k.startswith("timeout:") and p is not None]
            if sh and dp:
                print(f"\nplanting split: retry_budget {min(sh):.2f}-{max(sh):.2f}, "
                      f"timeout_ms {min(dp):.2f}-{max(dp):.2f}"
                      f"{'' if max(sh) < min(dp) else '   WARNING: halves overlap'}")

        kinds: dict[str, int] = {}
        for r in arows:
            for kind in r["miss_kind"].values():
                kinds[kind] = kinds.get(kind, 0) + 1
        print("misses by kind:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())) or "none")

    errs = [r for r in rows if "error" in r]
    print(f"\n{len(errs)} run(s) errored.")
    for e in errs[:5]:
        print(f"  arm={e.get('arm')} seed={e['seed']} tokens={e['target_tokens']}: "
              f"{str(e['error'])[:120]}")
    trunc = [r for r in scored if r.get("stop_reason") not in (None, "end_turn", "stop_sequence")]
    if trunc:
        print(f"{len(trunc)} run(s) with unexpected stop_reason: "
              f"{ {r.get('stop_reason') for r in trunc} }")
    nomeasure = [r for r in scored if not r.get("input_tokens")]
    if nomeasure:
        print(f"WARNING: {len(nomeasure)} scored run(s) report 0 input tokens, so there is "
              f"no evidence those prompts were the length they were meant to be.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", nargs="+", choices=list(ARMS), default=["latent", "synth"])
    ap.add_argument("--difficulty", type=int, default=1,
                    help="latent: semantic decoys per key (0-3). "
                         "synth: minimum services surviving the filter. "
                         "exact: ignored, the control must not move.")
    ap.add_argument("--hops", type=int, default=1,
                    help="latent: inference steps per answer. 1 = value stated in "
                         "prose (pilots 1-3). 2 = base quantity and this service's "
                         "fraction of it planted in opposite halves of the window. "
                         "Not implemented on exact or synth, which error rather "
                         "than ignore it.")
    ap.add_argument("--instances", type=int, default=10)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--lengths", type=int, nargs="+", default=DEFAULT_LENGTHS)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--out", default=None)
    ap.add_argument("--summarize", default=None)
    args = ap.parse_args()

    if args.summarize:
        summarize([json.loads(l) for l in Path(args.summarize).read_text().splitlines() if l.strip()])
        return

    OUT.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else OUT / f"context-rot-v3-{args.model}.jsonl"

    jobs, pads = [], {}
    for arm in args.arm:
        insts = [instance(arm, 1000 + i, args.difficulty, args.hops)
                 for i in range(args.instances)]
        # Padding excludes this arm's key and service names, so planted material
        # stays unique in the prompt. Each arm needs its own exclusion set.
        excl = sorted({e for i in insts for e in i["exclude"]})
        pads[arm] = corpus(excl)
        print(f"{arm}: padding {len(pads[arm]):,} chars "
              f"(~{len(pads[arm])/CHARS_PER_TOKEN:,.0f} tok), "
              f"{len(insts[0]['items'])} items + "
              f"{len(insts[0]['blocks']) - len(insts[0]['items'])} other blocks per instance")
        jobs += [(arm, i, t) for t in args.lengths for i in insts]

    print(f"{len(jobs)} runs: {len(args.arm)} arm(s) x {args.instances} instances x "
          f"{len(args.lengths)} length(s), difficulty={args.difficulty}, "
          f"hops={args.hops}, model={args.model}")

    rows, done = [], 0
    with out.open("w") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(one_run, pads[a], i, t, args.model, args.timeout)
                for a, i, t in jobs]
        for f in futs:
            row = f.result()
            rows.append(row)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            done += 1
            mark = "!" if "error" in row else ("." if row.get("all_items_correct") else
                                               str(row.get("n_items_correct", "?")))
            print(mark, end="", flush=True)
            if done % 25 == 0:
                print(f" {done}/{len(jobs)}", flush=True)
    spent = sum(r.get("cost_usd") or 0 for r in rows)
    print(f"\n\nwrote {out}  (list-price equivalent ${spent:.2f})")
    summarize(rows)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""serving_registry.py -- one row per (model, driver), and the gate that reads it.

INTERFACE CONTRACT (issue #8, stage 1). The spec is the "Auto-assert rules"
section of the 2026-08-25 panel findings (docs/studio-handoff/findings.md, PR
#9). This comment is the contract; the code below implements it.

Why this is not runner/registry.py
    registry.py answers "does this model name resolve, which CLI family drives
    it, does a run cost money". That is one record per MODEL and it is used on
    every code path in the instrument, so it is CORE and import-free by design.
    The panel's rules need something registry.py deliberately does not carry:
    one record per (model, DRIVER) pair, holding the serving config a row was
    produced under, what the driver can physically express, and what has and
    has not been probed. Those are per-pair facts with a file behind them and a
    CLI that writes it.

    So the roster is not duplicated -- it is extended sideways. Every row here
    names a model that must resolve through registry.resolve_model, pinned by
    test_every_shipped_row_names_a_model_the_model_registry_knows. If the two
    ever drift, that test is what says so.

Behavior
    Reads and writes runner/models.yaml; stamps the panel's seven auto-asserts
    onto every new row; and refuses, before dispatch, the three things that
    would produce a row nobody can report:
      (a) a run whose requested serving config differs from its registry row;
      (b) a comparison between rows that are not comparable;
      (c) a cell the driver cannot express.

In  A (model, driver) pair, plus the serving config the caller intends to run
    under. Callers: the runner's pre-dispatch validation (see "Wiring" below)
    and this module's own CLI.

Out Nothing on success -- these are checks, and a check that returns a verdict
    invites a caller to ignore it. Every refusal raises RegistryError, which is
    a ValueError so that fail-closed callers already catching ValueError stop
    without changes. Case (c) raises StructurallyImpossible, a distinct subtype,
    because a cell that cannot exist must not be scored 0: a 0 says the model
    tried and failed. The type is how a scoring caller tells those apart.

Errors
    Fail closed, always. An unknown (model, driver) raises rather than falling
    back to a default row, for registry.py's own reason: a mis-resolved row
    silently mislabels what the run was produced under, and the run would be
    misreported rather than rejected.

    `unknown` is not a value that matches itself. A serving field recorded as
    `unknown` refuses comparison, because two absences are not an equality --
    that is the could-not-determine result wearing a pass.

Wiring (APPLIED -- issue #12; the caller is run.py's check_serving_config)
    This section used to carry a four-argument recipe, three of whose arguments
    did not exist: `requested_serving_from` was illustrative pseudocode that
    read as an existing helper, run dicts carried no `driver` key, and no config
    carried a serving block for the helper to read. It is replaced here by what
    the runner actually does, because a wiring note that cannot be followed is
    worse than none.

    The two inputs, and where each comes from:

    `driver:`   declared in the runs config, on the sweep or on the config
                entry. NEVER defaulted. `.get("driver", "claude-code")` would
                file every pi run under the claude-code row, and findings.md
                reports pi as a separate vehicle contrast, so the default would
                merge two treatments into one label. A missing driver is a
                config error.

    `serving:`  a block of NUMBERS in the runs config, naming what the run will
                actually be served under -- the DECLARED intent, not a probe of
                the live server. Deterministic gating: the same config refuses
                or passes the same way tomorrow. Whether the live server matches
                the declaration is a separate, pre-flight question, answered by
                lms_preflight.py, which refuses before a sweep starts and never
                touches LM Studio's settings.

    The call, as run.py makes it:

        rows = serving_registry.load_rows()
        model = serving_registry.serving_model_name(r["model"])   # strips -local
        if serving_registry.has_row(rows, model):
            serving_registry.check_dispatch(
                rows, model, driver_of(r), cfg["serving"],
                harness_level=r.get("harness_level"))

    `serving_model_name` exists because the two registries name the same model
    differently on purpose: registry.py's id carries the VEHICLE (`glm-4.7-local`
    is the claude binary pointed at LM Studio), while a row here is keyed on the
    model and carries the driver in its own field. One rule, stated once, so the
    two rosters can drift only where a test says so.

    `has_row` is what keeps the gate honest without making it universal: a model
    with no row is a hosted model with no serving config to contradict, and is
    not gated. A model WITH a row and no declared serving block is refused
    (UninspectedConfig) rather than waved through. The runner prints how many
    runs the gate inspected, because an unreported zero is indistinguishable
    from the state issue #12 was filed about.

    StructurallyImpossible is caught BEFORE ValueError at the call site. It
    subclasses RegistryError subclasses ValueError, so a naive insertion into
    run.py's existing `except ValueError` block would make one inexpressible
    cell abort the whole sweep with exit 2. A matrix containing pi x L5 is not
    an invalid config; it is a valid matrix containing a cell that does not
    exist, and that cell is dropped with its own recorded status while the rest
    of the matrix runs.

Limitations
    - Hand-maintained facts, same as registry.py. The seed rows carry the
      panel's SEAT-MEASURED values; this module does not verify itself against
      a live server, and deliberately does not touch LM Studio's settings.
      Changing the server is a human action; this file records what was
      expected and the gate refuses on mismatch.
    - The file format is a strict subset of YAML, not YAML. The reader refuses
      every construct it does not implement, naming the line. A subset parser
      that guesses is worse than no parser: the misreading is silent and the row
      still looks plausible.
    - No network, no environment reads. Stdlib only. Imports registry (core)
      and nothing else of the instrument, so it stays cheap to import from the
      pre-dispatch path.
    - Not declared CORE_MODULE: it does file I/O, which the core contract in
      runner/import_gate.py forbids.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registry  # noqa: E402

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.yaml")

# The sentinel for a fact nobody has measured. Spelled out rather than left
# blank so that a reader of the file can tell "nobody has looked" from "the
# field was forgotten", and so that check_comparable can refuse it by name.
UNKNOWN = "unknown"

# Auto-assert 3: the serving facts pinned to every row. Comparisons are valid
# only between rows agreeing on all six, so a row missing any one of them is
# refused at the door rather than written and discovered at analysis time.
SERVING_FIELDS = ("parallel", "context_length", "max_tokens_floor",
                  "temperature", "seed", "quant")

# Fields whose name means a MINIMUM, and which check_run_config therefore
# compares with >= rather than ==. Only max_tokens_floor today. The distinction
# is not cosmetic: comparing a floor by equality refuses the safest request a
# caller can make (a cap well above the floor) while accepting only the exact
# boundary value.
#
# Between ROWS the same field is still an equality -- see check_comparable. Two
# rows disagreeing on the floor were produced under different serving configs
# and are not comparable, whichever floor is higher.
FLOOR_FIELDS = ("max_tokens_floor",)

# What a RUN calls the thing the ROW calls a floor. A run does not request a
# floor, it requests a cap, and rejecting `max_tokens` as "not a pinned serving
# field" taught callers to send the wrong key.
REQUEST_ALIASES = {"max_tokens": "max_tokens_floor"}

# Auto-assert 2: the capability manifest is a fact of the DRIVER, not a choice
# of whoever adds the model. pi's surface was measured by the panel (7 tools;
# grep/find/ls off by default; --skill, --prompt-template, -e extensions; no
# hooks, no subagents).
#
# max_harness_level: the highest rung of the L1-L5 dose ladder the driver can
# physically express. claude-code spans all five. pi's ceiling of 2 is marked
# UNVERIFIED on purpose: findings.md states that hooks and subagents are
# structurally impossible under pi (which is measured, and which rules out L4
# and L5), but the specific claim that the ceiling sits at 2 rather than 3
# carries no evidence tag anywhere in the panel record. It is implemented as
# specified and flagged here so that the number is re-derived before it is
# reported, not after.
DRIVER_CAPABILITIES = {
    "claude-code": {"subagents": True, "hooks": True, "tools": UNKNOWN,
                    "max_harness_level": 5},
    "pi":          {"subagents": False, "hooks": False, "tools": 7,
                    "max_harness_level": 2},  # ceiling: UNVERIFIED, see above
}

DRIVERS = tuple(sorted(DRIVER_CAPABILITIES))


class RegistryError(ValueError):
    """Every refusal in this module. A ValueError so that the runner's existing
    fail-closed handlers stop on it without being taught a new exception."""


class UninspectedConfig(RegistryError):
    """The caller asked the gate to check a run against nothing.

    Its own type because "your requested config contradicts the row" and "you
    handed me no requested config" have different fixes, and because the second
    one used to pass silently. A gate that inspected zero fields has not passed;
    it has failed to run, and the run it waved through gets labelled with a
    serving config nobody confirmed.
    """


class StructurallyImpossible(RegistryError):
    """Refusal case (c): the driver cannot express this cell at all.

    Its own type because the alternative -- scoring the cell 0 -- asserts that
    the model attempted the task and failed it. A cell that does not exist did
    not fail; it is not in the denominator. Callers that score branch on this
    type; callers that merely need to stop still catch RegistryError.
    """


# --------------------------------------------------------------------------- #
# The registry file: a strict subset of YAML
# --------------------------------------------------------------------------- #
# Supported, and nothing else: `key: value` maps nested by two-space
# indentation, `- key: value` lists of maps, and scalars (int, float, true,
# false, null, bare string, double-quoted string). Everything outside that
# raises. The subset is small because it is exactly what dump_registry_yaml
# emits -- the format is defined as what the writer writes, and the round-trip
# test is what holds the two together.
#
# COMMENTS, precisely, because "comments" overstated it before the `" #"` fix
# and the next person editing models.yaml by hand should learn the rule here
# rather than from the exception: a comment may occupy its own line, or follow a
# QUOTED value. A trailing comment after an unquoted value is a hard error, on
# purpose -- the reader cannot tell such a `#` from one belonging to the
# sentence, and a registry row that silently loses half its note still looks
# plausible.
_REFUSED = (
    ("\t", "tab indentation"),
)


def _scalar(text, lineno):
    """One value. Types are decided here and nowhere else."""
    if text == "":
        return ""
    if text.startswith('"'):
        if len(text) < 2 or not text.endswith('"'):
            raise RegistryError(f"line {lineno}: unterminated quoted string: {text!r}")
        return text[1:-1]
    if text[0] in "[{":
        raise RegistryError(
            f"line {lineno}: flow collections are outside this file's subset "
            f"(write nested keys or list items instead): {text!r}")
    if text[0] in "|>":
        raise RegistryError(
            f"line {lineno}: block scalars are outside this file's subset "
            f"(use one double-quoted line): {text!r}")
    if text[0] in "&*":
        raise RegistryError(
            f"line {lineno}: anchors and aliases are outside this file's subset: {text!r}")
    if text in ("null", "~"):
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _strip_comment(value, lineno):
    """Remove a trailing comment from a value, or refuse to guess at one.

    The hazard this exists for: `authorization: I approve run #3 and nothing
    else`. A stripper that cuts at the first " #" leaves a value that still
    parses, still looks like a complete authorization, and now records something
    the human did not type. Nothing in the text distinguishes that from a real
    trailing comment, so an unquoted value containing " #" is an error naming
    the line, and the message says how to fix it. After a QUOTED value the
    ambiguity is gone, so a comment there is stripped normally.
    """
    if value == "" or value.startswith("#"):
        return ""  # `key:` on its own, with or without a comment after it
    if value.startswith('"'):
        end = value.find('"', 1)
        if end == -1:
            raise RegistryError(f"line {lineno}: unterminated quoted string: {value!r}")
        tail = value[end + 1:].strip()
        if tail and not tail.startswith("#"):
            raise RegistryError(
                f"line {lineno}: unexpected text after the closing quote: {tail!r}")
        return value[:end + 1]
    if " #" in value:
        raise RegistryError(
            f"line {lineno}: unquoted value contains \" #\", which could be a "
            f"comment or could be part of the value, and this reader does not "
            f"guess: {value!r}. Quote the whole value if the '#' belongs to it, "
            f"or move the comment to its own line.")
    return value


def parse_registry_yaml(text):
    """Parse the registry file. Raise RegistryError, naming the line, on anything
    outside the subset -- never guess."""
    items = []  # (indent, is_item, key, raw_value, lineno)
    for lineno, raw in enumerate(text.splitlines(), start=1):
        for ch, why in _REFUSED:
            if ch in raw:
                raise RegistryError(f"line {lineno}: {why} is not allowed: {raw!r}")
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        is_item = body.startswith("- ")
        if is_item:
            body = body[2:].strip()
            indent += 2
        key, sep, value = body.partition(":")
        if not sep:
            raise RegistryError(
                f"line {lineno}: expected `key: value`, got {raw.strip()!r}")
        items.append((indent, is_item, key.strip(),
                      _strip_comment(value.strip(), lineno), lineno))

    def build(i, indent):
        """Consume the run of items at `indent`, returning (value, next index)."""
        out = [] if items[i][1] else {}
        while i < len(items):
            ind, is_item, key, value, lineno = items[i]
            if ind < indent:
                break
            if ind > indent:
                raise RegistryError(
                    f"line {lineno}: unexpected indentation {ind}, expected {indent}")
            if is_item != isinstance(out, list):
                raise RegistryError(
                    f"line {lineno}: list item and mapping key at the same level")
            i += 1
            if value == "":
                if i >= len(items) or items[i][0] <= ind:
                    raise RegistryError(f"line {lineno}: key {key!r} has no value")
                child, i = build(i, items[i][0])
            else:
                child = _scalar(value, lineno)
            if isinstance(out, list):
                # A `- key: value` opens a map; its siblings are the deeper lines
                # that follow, which build() at the item indent collects for us.
                item = {key: child}
                while i < len(items) and items[i][0] == ind and not items[i][1]:
                    _, _, k2, v2, ln2 = items[i]
                    i += 1
                    if v2 == "":
                        if i >= len(items) or items[i][0] <= ind:
                            raise RegistryError(f"line {ln2}: key {k2!r} has no value")
                        sub, i = build(i, items[i][0])
                        item[k2] = sub
                    else:
                        item[k2] = _scalar(v2, ln2)
                out.append(item)
            else:
                out[key] = child
        return out, i

    if not items:
        return {}
    doc, _ = build(0, items[0][0])
    return doc


def _dump_scalar(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # Quote anything a reader (or the parser) could mistake for another type,
    # AND anything containing a space. The space rule is what makes free text --
    # the authorization sentence, the notes, the timeout basis -- quoted by
    # construction, so a later hand edit that adds a '#' to a sentence cannot
    # produce a value the reader has to refuse. Bare tokens (glm-4.7,
    # claude-code, bypassPermissions, unknown) stay unquoted and readable.
    if (text == "" or text != text.strip() or " " in text or ":" in text
            or "#" in text or text[0] in "\"'[{|>&*-"
            or text in ("null", "true", "false")):
        return '"' + text.replace('"', "'") + '"'
    try:
        float(text)
        return '"' + text + '"'
    except ValueError:
        return text


def dump_registry_yaml(doc, indent=0):
    """Emit the subset parse_registry_yaml reads. The pair is the format."""
    pad = " " * indent
    out = []
    if isinstance(doc, list):
        for item in doc:
            # The item's keys are emitted at indent+2; the first line then has
            # its leading pad replaced by the "- " marker, which occupies the
            # same two columns. That is why nesting here is two-space only.
            body = dump_registry_yaml(item, indent + 2)
            out.append(pad + "- " + body[indent + 2:])
        return "".join(out)
    for key, value in doc.items():
        if isinstance(value, dict) and value:
            out.append(f"{pad}{key}:\n")
            out.append(dump_registry_yaml(value, indent + 2))
        elif isinstance(value, list) and value:
            out.append(f"{pad}{key}:\n")
            out.append(dump_registry_yaml(value, indent + 2))
        else:
            out.append(f"{pad}{key}: {_dump_scalar(value)}\n")
    return "".join(out)


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #
def load_rows(path=REGISTRY_PATH):
    """Every (model, driver) row on disk. Missing file is an empty registry, not
    a crash: the gate's refusal for an absent row already says the right thing."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        doc = parse_registry_yaml(f.read())
    return doc.get("models") or []


def row_key(row):
    return (row["model"], row["driver"])


# The suffix registry.py's ids carry for the local VEHICLE -- `glm-4.7-local` is
# the claude binary pointed at an LM Studio server. A row here is keyed on the
# MODEL and carries the driver in its own field, so the vehicle suffix has to
# come off exactly once, in one place, rather than at each call site.
LOCAL_ID_SUFFIX = "-local"


def serving_model_name(model):
    """The registry-row model name for a runner model id or alias.

    Resolves through registry.resolve_model first, so an unknown name raises
    there (a typo must not reach a paid CLI, and must not quietly miss the gate
    either) and an alias is expanded by the one rule that owns aliases.
    """
    mid, _spec = registry.resolve_model(model)
    if mid.endswith(LOCAL_ID_SUFFIX):
        return mid[:-len(LOCAL_ID_SUFFIX)]
    return mid


def has_row(rows, model):
    """True when any driver row exists for this model.

    The gate's applicability test, and deliberately not `find_row(...) is not
    None`: "this model has no serving config anyone pinned" (a hosted
    subscription model) is a different fact from "this model is pinned but you
    named a driver it has no row for", and the second must still refuse.
    """
    return any(row["model"] == model for row in rows)


def find_row(rows, model, driver):
    for row in rows:
        if row_key(row) == (model, driver):
            return row
    known = ", ".join(f"{m}/{d}" for m, d in sorted(row_key(r) for r in rows)) or "(none)"
    raise RegistryError(
        f"no registry row for {model!r} x {driver!r}; known pairs: {known}. "
        f"Add one with `python3 runner/serving_registry.py add-model` -- a run "
        f"with no row has no recorded serving config to be reported under.")


def new_row(model, driver, serving, prefill_tok_s=None, prefill_tok_s_max=None,
            permission_mode="default", permission_authorization=None,
            permission_authorized_date=None, capabilities=None,
            deterministic_loops=None, notes=None):
    """Build a row with the panel's seven auto-asserts applied.

    Two of the seven are never optional and so take no argument that could
    switch them off: `deterministic_loops` is false until a probe says otherwise
    (rule 1), and the driver capability manifest is copied from
    DRIVER_CAPABILITIES (rule 2). Both parameters exist only to REJECT a caller
    that tries to assert them by hand, which is a louder failure than silently
    ignoring the argument.
    """
    if driver not in DRIVER_CAPABILITIES:
        raise RegistryError(
            f"unknown driver {driver!r}; known: {', '.join(DRIVERS)}")

    # Rule 1 -- llama.cpp batch physics. temperature 0 + seed 42 gave 2/3
    # distinct sequential outputs on this stack; determinism is not a property
    # anyone may declare at add time.
    if deterministic_loops:
        raise RegistryError(
            "deterministic_loops cannot be set when adding a model: it flips to "
            "true only from a 5/5-identical sequential probe on THIS serving "
            "config, recorded by record_noise_probe()")

    # Rule 2 -- the capability manifest belongs to the driver.
    manifest = dict(DRIVER_CAPABILITIES[driver])
    if capabilities:
        conflicting = {k: v for k, v in capabilities.items()
                       if manifest.get(k) != v}
        if conflicting:
            raise RegistryError(
                f"driver {driver!r} capabilities are measured facts, not options; "
                f"refused: {conflicting}; recorded: {manifest}")
    max_level = manifest.pop("max_harness_level")

    # Rule 3 -- every serving field pinned, no defaults invented.
    missing = [f for f in SERVING_FIELDS if f not in serving]
    if missing:
        raise RegistryError(
            f"serving config is missing pinned field(s): {', '.join(missing)}; "
            f"all of {', '.join(SERVING_FIELDS)} must be recorded, because a row "
            f"missing one cannot be compared with any other row")

    # Rule 6 -- a bypassPermissions row asserts a human approved something.
    if permission_mode == "bypassPermissions" and not (
            permission_authorization and permission_authorized_date):
        raise RegistryError(
            "permission_mode=bypassPermissions requires the authorizing typed "
            "sentence and its date on the row, scoped to this (model, driver) "
            "pair -- an unauthorized bypass row is a claim with nothing behind it")

    # Rule 7 -- the timeout basis is a measured prefill rate, not a guess.
    if not prefill_tok_s:
        raise RegistryError(
            "prefill_tok_s is required: the turn cap is derived from measured "
            "prefill rate x prompt size (rule 7), so a row without a measured "
            "rate has no honest timeout basis")

    return {
        "model": model,
        "driver": driver,
        "serving": {f: serving[f] for f in SERVING_FIELDS},
        "capabilities": manifest,
        "max_harness_level": max_level,
        # Rule 1.
        "deterministic_loops": False,
        # Rule 5 -- absent until probed, and absence is what refuses comparison.
        "noise_probe": None,
        # Rule 4 -- the reasoning-token probe result, if one was run. The floor
        # itself lives in serving.max_tokens_floor because it is a serving fact.
        "reasoning_probe": None,
        "permission": {
            "mode": permission_mode,
            "authorization": permission_authorization,
            "authorized_date": permission_authorized_date,
        },
        # Rule 7.
        "timeout": {
            "prefill_tok_s_min": prefill_tok_s,
            "prefill_tok_s_max": prefill_tok_s_max or prefill_tok_s,
            "basis": "turn cap derived from prefill rate x prompt size; "
                     "wall-clock expiry logs its own status, never a task fail",
        },
        "notes": notes,
    }


def record_noise_probe(row, flip_rate, date, identical, of):
    """Attach a measured noise probe, and flip determinism only on 5/5 identical.

    The one door through which `deterministic_loops` can become true, and it
    demands the measurement that justifies it in the same call.
    """
    row["noise_probe"] = {"flip_rate": flip_rate, "date": date,
                          "identical": identical, "of": of}
    row["deterministic_loops"] = bool(of >= 5 and identical == of)
    return row


def record_reasoning_probe(row, cap_tokens, empty, of, date):
    """Attach auto-assert 4's measurement: how often a small max_tokens cap came
    back with empty content, because GLM's reasoning tokens are spent from the
    same budget as its answer.

    Refuses a probe that refutes the row instead of quietly repairing it. If
    content came back empty at or above the recorded floor, the floor is wrong,
    and every result already labelled with this row was labelled with a serving
    claim that does not hold. That is a new row (and a re-run), not a field
    edit -- the same reason add-model refuses to overwrite an existing pair.
    """
    floor = row["serving"]["max_tokens_floor"]
    if empty and cap_tokens >= floor:
        raise RegistryError(
            f"probe refutes {row_key(row)}: {empty}/{of} responses were empty at a "
            f"cap of {cap_tokens}, which is at or above the row's recorded "
            f"max_tokens floor of {floor}. The floor is wrong, so every result "
            f"already labelled with this row carries a serving claim that does not "
            f"hold. Record a new row with the higher floor rather than editing "
            f"this one.")
    row["reasoning_probe"] = {"cap_tokens": cap_tokens, "empty": empty, "of": of,
                              "date": date}
    return row


def derive_turn_cap_s(row, prompt_tokens, turns=1, safety_factor=1.5):
    """Rule 7's cap, derived rather than stored.

    Prompt size varies per arm, so a single stored constant would be wrong for
    every arm but one. Uses the SLOW end of the measured prefill band, because a
    cap set from the fast end turns a slow-but-working run into a fake failure.
    """
    slow = row["timeout"]["prefill_tok_s_min"]
    if not slow:
        raise RegistryError(f"row {row_key(row)} has no measured prefill rate")
    return (prompt_tokens / float(slow)) * turns * safety_factor


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def check_run_config(row, requested):
    """(a) Refuse a run whose requested serving config differs from its row.

    Only the fields the caller names are checked: a caller that asserts nothing
    about `quant` is not claiming anything false about it. What is refused is a
    stated intent that CONTRADICTS the row, because the row is the label every
    result gets reported under.

    Naming NO field is different, and is refused outright. Returns the number of
    fields inspected so a caller (and the tests) can tell a pass from a
    no-op: a check that comes back quiet after comparing nothing is
    indistinguishable from a check that compared everything and agreed.
    """
    if not requested:
        raise UninspectedConfig(
            f"refusing to clear {row_key(row)} against zero requested serving "
            f"fields. Name what the run will actually use -- at minimum "
            f"{', '.join(SERVING_FIELDS)} -- because a gate that inspected zero "
            f"fields has not agreed with the row, it has failed to look at it.")
    diffs = []
    for field, want in requested.items():
        field = REQUEST_ALIASES.get(field, field)
        if field not in SERVING_FIELDS:
            raise RegistryError(
                f"{field!r} is not a pinned serving field; pinned: "
                f"{', '.join(SERVING_FIELDS)}")
        have = row["serving"][field]
        if field in FLOOR_FIELDS:
            # A floor is compared by ORDERING, and an ordering is the one
            # comparison in this module that can raise something that is not a
            # ValueError. `'unknown' < 8192` is a TypeError, which would escape
            # the runner's `except ValueError` and end the sweep in a traceback
            # rather than a clean exit 2 -- and `unknown` is this file's own
            # sentinel, shipped on quant, so a caller copying a row into a
            # request reaches it without doing anything strange. Guard on
            # orderability only: bool is an int, orders fine, and is left to the
            # floor check below to refuse on its value.
            if not isinstance(want, (int, float)):
                raise RegistryError(
                    f"{field}: requested value {want!r} is not a number "
                    f"(type {type(want).__name__}), and a floor is compared by "
                    f"ordering. Send the max_tokens the run will actually use. "
                    f"Note that {UNKNOWN!r} is this registry's sentinel for an "
                    f"unmeasured value: it can be recorded on a row, but it can "
                    f"never be requested by a run.")
            if want < have:
                diffs.append(
                    f"{field}: run requests {want!r}, below the row's floor of "
                    f"{have!r} -- GLM reasoning tokens consume max_tokens, and at "
                    f"a 600-token cap 5/6 probes returned empty content. Raise the "
                    f"cap; a run under the floor measures the cap, not the model.")
        elif have != want:
            diffs.append(f"{field}: registry row says {have!r}, run requests {want!r}")
    if diffs:
        raise RegistryError(
            f"serving config mismatch for {row_key(row)}:\n  " + "\n  ".join(diffs)
            + "\n  The registry row is what the results will be labelled with. "
              "Either change the server to match the row (a human action -- this "
              "code does not touch LM Studio's settings) or record a new row for "
              "the config you actually intend to run.")
    return len(requested)


def check_comparable(row_a, row_b):
    """(b) Refuse a cross-model comparison the serving stack does not support.

    Three ways a pair fails: a pinned field differs (rule 3), a pinned field is
    `unknown` on either side, or either row has never had a noise probe (rule
    5). The middle one matters most: `unknown` == `unknown` is two absences, and
    accepting it would let a comparison pass on the strength of what nobody
    measured.
    """
    problems = []
    for field in SERVING_FIELDS:
        a, b = row_a["serving"][field], row_b["serving"][field]
        if a == UNKNOWN or b == UNKNOWN:
            problems.append(
                f"{field} is {UNKNOWN!r} on at least one row -- two unmeasured "
                f"values are not a match")
        elif a != b:
            problems.append(f"{field} differs: {a!r} vs {b!r}")
    for row in (row_a, row_b):
        if row["noise_probe"] is None:
            problems.append(
                f"{row_key(row)} has no noise probe -- without a measured flip "
                f"rate the difference between these rows cannot be sized (rule 5)")
    if problems:
        raise RegistryError(
            f"rows {row_key(row_a)} and {row_key(row_b)} are not comparable:\n  "
            + "\n  ".join(problems))


def check_cell_expressible(row, capability=None, harness_level=None):
    """(c) Refuse a cell the driver cannot express -- loudly, and as its own type.

    Never returns a verdict and never scores. A caller that catches
    StructurallyImpossible records the cell as structurally-impossible; a caller
    that does not catch it stops. Neither path can write a 0, which is the point:
    a 0 asserts an attempt that never happened.
    """
    caps = row["capabilities"]
    if capability is not None:
        if capability not in caps:
            raise RegistryError(
                f"unknown capability {capability!r}; recorded: {', '.join(sorted(caps))}")
        if caps[capability] is False:
            raise StructurallyImpossible(
                f"structurally-impossible cell: driver {row['driver']!r} has no "
                f"{capability}. Record it as structurally-impossible; scoring it 0 "
                f"would put a cell that cannot exist into the denominator.")
    if harness_level is not None:
        ceiling = row["max_harness_level"]
        if harness_level > ceiling:
            raise StructurallyImpossible(
                f"structurally-impossible cell: driver {row['driver']!r} tops out at "
                f"harness level {ceiling}, asked for {harness_level}. This is a "
                f"vehicle contrast reported separately, never a row in the dose "
                f"table.")


def check_dispatch(rows, model, driver, requested_serving, harness_level=None,
                   capability=None):
    """The one call the runner makes. Resolves the row, then runs the gate.

    One entry point rather than three, because a wiring that has to remember to
    call three checks is a wiring that will call two.
    """
    row = find_row(rows, model, driver)
    check_cell_expressible(row, capability=capability, harness_level=harness_level)
    check_run_config(row, requested_serving or {})
    return row


# --------------------------------------------------------------------------- #
# CLI -- the thin UI over the same code
# --------------------------------------------------------------------------- #
def _kv(text):
    key, sep, value = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"expected key=value, got {text!r}")
    return key, _scalar(value, 0)


def cmd_add_model(args):
    rows = load_rows(args.path)
    try:
        find_row(rows, args.model, args.driver)
    except RegistryError:
        pass
    else:
        raise SystemExit(
            f"refused: {args.model} x {args.driver} is already in the registry. "
            f"Editing a row changes what already-recorded results mean; add a new "
            f"row for the new config instead.")
    serving = dict(args.serving or [])
    row = new_row(args.model, args.driver, serving,
                  prefill_tok_s=args.prefill_tok_s,
                  prefill_tok_s_max=args.prefill_tok_s_max,
                  permission_mode=args.permission_mode,
                  permission_authorization=args.permission_authorization,
                  permission_authorized_date=args.permission_authorized_date,
                  notes=args.notes)
    rows.append(row)
    with open(args.path, "w", encoding="utf-8") as f:
        f.write(dump_registry_yaml({"models": rows}))
    print(f"added {args.model} x {args.driver} to {args.path}")
    print(render_rows([row]))


def render_rows(rows):
    """The registry as a table. This is the UI: a reader has to be able to see
    what was and was not measured without opening the file."""
    if not rows:
        return "(registry is empty)"
    lines = []
    head = (f"{'model':<12} {'driver':<12} {'par':>4} {'context':>8} {'maxtok':>7} "
            f"{'temp':>5} {'seed':>5} {'quant':<9} {'det':<5} {'noise':<12} "
            f"{'perm':<18} {'L<=':>4}")
    lines.append(head)
    lines.append("-" * len(head))
    for row in rows:
        s, probe = row["serving"], row["noise_probe"]
        noise = "NOT PROBED" if probe is None else f"flip {probe['flip_rate']}"
        lines.append(
            f"{row['model']:<12} {row['driver']:<12} {s['parallel']:>4} "
            f"{s['context_length']:>8} {s['max_tokens_floor']:>7} "
            f"{s['temperature']:>5} {s['seed']:>5} {str(s['quant']):<9} "
            f"{str(row['deterministic_loops']):<5} {noise:<12} "
            f"{row['permission']['mode']:<18} {row['max_harness_level']:>4}")
    return "\n".join(lines)


def cmd_list(args):
    print(render_rows(load_rows(args.path)))


def cmd_validate(args):
    """Assert the registry is internally consistent AND says how many rows it
    inspected. A validator that inspected zero rows has not passed."""
    rows = load_rows(args.path)
    problems = []
    for row in rows:
        try:
            registry.resolve_model(row["model"] + "-local")
        except ValueError as e:
            problems.append(f"{row_key(row)}: {e}")
        if row["deterministic_loops"] and row["noise_probe"] is None:
            problems.append(f"{row_key(row)}: determinism asserted with no probe")
    print(f"rows inspected: {len(rows)}")
    for p in problems:
        print(f"  {p}")
    if not rows:
        print("UNENFORCED: the registry is empty, so nothing was checked")
        return 2
    if problems:
        return 1
    print("OK")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--path", default=REGISTRY_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add-model", help="write a row with the auto-asserts applied")
    add.add_argument("--model", required=True)
    add.add_argument("--driver", required=True, choices=DRIVERS)
    add.add_argument("--serving", type=_kv, action="append", metavar="FIELD=VALUE",
                     help=f"one per pinned field: {', '.join(SERVING_FIELDS)}")
    add.add_argument("--prefill-tok-s", type=float, dest="prefill_tok_s")
    add.add_argument("--prefill-tok-s-max", type=float, dest="prefill_tok_s_max")
    add.add_argument("--permission-mode", default="default")
    add.add_argument("--permission-authorization")
    add.add_argument("--permission-authorized-date")
    add.add_argument("--notes")
    add.set_defaults(func=cmd_add_model)

    lst = sub.add_parser("list", help="the registry as a table")
    lst.set_defaults(func=cmd_list)

    val = sub.add_parser("validate", help="check the registry against runner/registry.py")
    val.set_defaults(func=cmd_validate)

    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())

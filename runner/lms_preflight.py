#!/usr/bin/env python3
"""lms_preflight.py -- refuse to start when the live server is not the server
the registry row describes.

WHY THIS IS NOT THE SERVING GATE. runner/serving_registry.py's check_dispatch
compares a run's DECLARED serving config against its (model, driver) row. That
is deterministic, needs no network, and gives the same verdict tomorrow -- which
is exactly what a gate on the dispatch path should be, and exactly why it cannot
answer "is the server actually loaded this way right now".

The pre-registration asks for both. prompt-2-run-experiment.md:22-25:

    Serving config for every run: LM Studio PARALLEL=1, context 131072,
    temperature 0, seed 42, max_tokens >= 8192, turn caps not wall-clock caps,
    claude driver in bypassPermissions (authorized 2026-08-25). If LM Studio is
    not already in this config, stop and ask Drake to set it; do not change it
    yourself.

"Stop and ask Drake" is an instruction to a human operator, and an instruction
to a human operator that nothing enforces is a note. This module is the
enforcement: a stage runs it before it dispatches anything, and a nonzero exit
is a refusal to proceed.

In   `~/.lmstudio/bin/lms ps` output -- read from the live binary, or from a
     captured file with --ps-file (which is also how it is tested, and how a
     report can cite the exact bytes it refused on).

Out  Nothing on success beyond a line saying how many fields it compared. Every
     refusal is an exception with its own type and its own exit code, because
     "the server is set wrong", "the model is not loaded" and "I could not read
     the table" have three different fixes.

Errors and exit codes. None of the nonzero codes is a pass, and that includes
     EXIT_UNINSPECTABLE: a checker that could not inspect its subject has not
     cleared it. Returning "no rows" for unreadable output would make a broken
     `lms` binary look identical to an idle server.

       0  the live config matches the row on every pinned field it can observe
       3  MISMATCH -- names each field, the row's value and the live value
       4  NOT LOADED -- the model is not in the table at all
       5  UNINSPECTABLE -- no table could be read out of the output

READ-ONLY, BY CONSTRUCTION. The only subcommand this module names is `ps`.
`load`, `unload` and `set` change LM Studio's state, the pre-registration says a
human does that, and runner/tests/test_lms_preflight.py enumerates the source to
assert the vocabulary is absent. A behavioural test cannot prove a negative;
enumerating the source can.

WHAT IT CANNOT SEE. `lms ps` reports CONTEXT and PARALLEL. It does not report
temperature, seed, quant or the max_tokens ceiling, so those four pinned fields
are NOT checked here and this module does not pretend otherwise -- it reports
the count of fields it actually compared, and two is not six. Their agreement is
still asserted by the declared-config gate, against the row.

Stdlib only. No writes.
"""
import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serving_registry  # noqa: E402

LMS_BIN = os.path.expanduser("~/.lmstudio/bin/lms")

# The whole vocabulary this tool has. `ps` lists what is loaded and changes
# nothing. Kept as a constant so the read-only claim is one name a test can pin
# rather than a habit spread over call sites.
LMS_SUBCOMMAND = ("ps",)

EXIT_OK = 0
EXIT_MISMATCH = 3
EXIT_NOT_LOADED = 4
EXIT_UNINSPECTABLE = 5

# The two pinned serving fields `lms ps` can actually observe, mapped from the
# table's column name to the registry row's field name. The other four
# (temperature, seed, quant, max_tokens_floor) are not in this output and are
# deliberately absent rather than guessed.
OBSERVABLE = {"context": "context_length", "parallel": "parallel"}

# Columns this module reads. A header missing any of them is refused by name:
# the layout changed, and a parser that shrugs and returns fewer keys hands the
# caller a comparison over whatever survived.
REQUIRED_COLUMNS = ("IDENTIFIER", "MODEL", "CONTEXT", "PARALLEL")

INTEGER_COLUMNS = ("CONTEXT", "PARALLEL")


class PreflightError(RuntimeError):
    """Base for every refusal here. Not a ValueError: these are facts about a
    live server, not about a caller's arguments, and a caller catching
    ValueError to validate its own config should not swallow them."""


class PreflightUninspectable(PreflightError):
    """No table could be read. Distinct because it is not a verdict about the
    server at all -- it is this tool failing to run, and it must not be
    reported as a clear."""


class PreflightNotLoaded(PreflightError):
    """The model is not in the table. Distinct from a mismatch: nothing is
    misconfigured, and the fix is to load it rather than to change a setting."""


class PreflightMismatch(PreflightError):
    """The live config contradicts the registry row."""


def _columns(header):
    """{COLUMN: (start, end)} from the header line's own offsets.

    Sliced by offset rather than split on whitespace because the SIZE column
    holds a space ("158.74 GB"), so a whitespace split shifts every column after
    it by one and silently reads DEVICE as PARALLEL.
    """
    found = [(m.group(0), m.start()) for m in re.finditer(r"\S+", header)]
    spans = {}
    for i, (name, start) in enumerate(found):
        end = found[i + 1][1] if i + 1 < len(found) else None
        spans[name] = (start, end)
    return spans


def parse_ps(text):
    """Rows of `lms ps`, as dicts keyed on lowercased column names.

    Raises PreflightUninspectable rather than returning [] when the output holds
    no readable table -- see the module docstring on why an empty list would be
    the dangerous answer.
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    header = next((ln for ln in lines if "IDENTIFIER" in ln and "MODEL" in ln), None)
    if header is None:
        head = (lines[0] if lines else "")[:120]
        raise PreflightUninspectable(
            f"no `lms ps` table in the output (no IDENTIFIER/MODEL header). "
            f"First line was {head!r}. This is a could-not-inspect, not an "
            f"empty server: nothing about the live config has been established.")
    spans = _columns(header)
    missing = [c for c in REQUIRED_COLUMNS if c not in spans]
    if missing:
        raise PreflightUninspectable(
            f"`lms ps` header is missing {', '.join(missing)}; got "
            f"{', '.join(spans)}. The output layout changed -- refusing rather "
            f"than comparing whatever columns survived.")

    rows = []
    for line in lines[lines.index(header) + 1:]:
        cell = {}
        for name, (start, end) in spans.items():
            cell[name] = line[start:end].strip() if start < len(line) else ""
        if not cell["IDENTIFIER"]:
            continue
        for name in INTEGER_COLUMNS:
            raw = cell[name]
            if not raw.isdigit():
                raise PreflightUninspectable(
                    f"`lms ps` reported {name}={raw!r} for "
                    f"{cell['IDENTIFIER']!r}, which is not a number. Refusing "
                    f"rather than coercing: a repaired value is a value nobody "
                    f"measured.")
            cell[name] = int(raw)
        rows.append({k.lower(): v for k, v in cell.items()})
    return rows


def check_live_serving(row, ps_text):
    """Compare the live server against one registry row. Returns the number of
    fields compared, so a caller can tell a pass from a no-op.

    Only the fields `lms ps` reports are compared, and the count says how many
    that was. Two is not six, and this function never implies otherwise.
    """
    live_rows = parse_ps(ps_text)
    model = row["model"]
    match = next((r for r in live_rows
                  if model in (r["identifier"], r["model"])), None)
    if match is None:
        loaded = ", ".join(sorted(r["identifier"] for r in live_rows)) or "(nothing)"
        raise PreflightNotLoaded(
            f"{model!r} is not loaded in LM Studio; loaded: {loaded}. Ask Drake "
            f"to load it -- this tool does not load models.")

    diffs, inspected = [], 0
    for column, field in sorted(OBSERVABLE.items()):
        expected = row["serving"][field]
        observed = match[column]
        inspected += 1
        if expected != observed:
            diffs.append(f"{field}: row says {expected!r}, LM Studio is serving "
                         f"{observed!r}")
    if diffs:
        raise PreflightMismatch(
            f"live serving config does not match the registry row for "
            f"{serving_registry.row_key(row)}:\n  " + "\n  ".join(diffs)
            + f"\n  ({inspected} field(s) compared; temperature, seed, quant and "
              f"the max_tokens ceiling are not in `lms ps` output and were not "
              f"checked here.)\n  The pre-registration fixes this config for "
              f"every run. Stop and ask Drake to set it; do not change it "
              f"yourself -- this tool never writes to LM Studio.")
    return inspected


def read_ps(ps_file=None, lms_bin=LMS_BIN):
    """The live table, or a captured one. Never anything but `lms ps`."""
    if ps_file:
        with open(ps_file, encoding="utf-8", errors="replace") as f:
            return f.read()
    if not os.path.exists(lms_bin):
        raise PreflightUninspectable(
            f"no lms binary at {lms_bin}; cannot inspect the live serving "
            f"config. This is a could-not-inspect, not a clear.")
    proc = subprocess.run([lms_bin, *LMS_SUBCOMMAND], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=60)
    return proc.stdout or ""


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="refuse to start when LM Studio is not serving the config "
                    "the registry row describes (read-only; never writes)")
    ap.add_argument("--model", default="glm-4.7")
    ap.add_argument("--driver", default="claude-code",
                    choices=sorted(serving_registry.DRIVERS))
    ap.add_argument("--ps-file", default=None,
                    help="read a captured `lms ps` table instead of running it")
    args = ap.parse_args(argv)

    try:
        row = serving_registry.find_row(serving_registry.load_rows(),
                                        args.model, args.driver)
    except serving_registry.RegistryError as e:
        print(f"PREFLIGHT UNINSPECTABLE -- {e}")
        return EXIT_UNINSPECTABLE

    try:
        inspected = check_live_serving(row, read_ps(args.ps_file))
    except PreflightMismatch as e:
        print(f"PREFLIGHT REFUSED -- {e}")
        return EXIT_MISMATCH
    except PreflightNotLoaded as e:
        print(f"PREFLIGHT REFUSED -- {e}")
        return EXIT_NOT_LOADED
    except PreflightUninspectable as e:
        print(f"PREFLIGHT UNINSPECTABLE -- {e}")
        return EXIT_UNINSPECTABLE

    print(f"PREFLIGHT OK -- {args.model}/{args.driver}: {inspected} field(s) "
          f"compared against the registry row and matching "
          f"({', '.join(sorted(OBSERVABLE.values()))}). Not checked here, "
          f"because `lms ps` does not report them: temperature, seed, quant, "
          f"max_tokens ceiling.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

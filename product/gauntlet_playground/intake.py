"""intake.py -- the "push your job" intake, as a form (ticket 41, slice S6).

A form, not a pipeline. The user describes a job and, in their own words, what
they would accept as done. This module stores those words with a timestamp and
nothing else happens: a person reads the store and writes the acceptance suite
by hand. Turning a described job into something the gauntlet can run and grade
belongs to a different ticket; this one collects.

CODE PATHS OUT OF A STORED SUBMISSION -- the complete list:

    1. read_submissions() parses the store back into rows.
    2. listing_lines() calls read_submissions() to print a count.

Nothing else in this package reads the store. There is no path from a stored
row to the executor, to a subprocess, to the network, or to a usage ledger:
this module imports none of them, and runner/tests/test_intake.py asserts that
positively -- the import list is enumerated, the executor module is shown to
stay unloaded across a submit, and a submit is shown to create exactly one
file. "No run happened" and "the run code was never reached" look identical
from the outside; the tests assert the second.

The scope stops at collection deliberately. The parked question this protects
is argued in the ticket body, not here, and no behaviour in this file leans on
either side of it: a submission feeds nothing and waits on nothing except a
person.
"""
import json
import os
import re
from datetime import datetime, timezone

# Spec §5 gates every slice from printing a price while ticket 08 records no
# verified prices. This gate is RESTATED from surface.py rather than imported:
# _refuse_money there is a private name, and the duplication is deliberate --
# each slice's gate stands on its own, so one edit cannot weaken the check for
# every printed surface at once. Keep the two copies independent; do not DRY
# them into a shared helper. Marker semantics follow surface.py's reasoning:
# "$" is punctuation matched by substring; the words need a word-boundary
# match so honest sentences ("percent", "recent") survive, and the plurals are
# listed because \bdollar\b does not match "dollars".
_MONEY_SYMBOLS = ("$",)
_MONEY_WORDS = ("usd", "dollar", "dollars", "cent", "cents")
_MONEY_WORD_RE = re.compile(
    r"\b(" + "|".join(_MONEY_WORDS) + r")\b", re.IGNORECASE)

# What a stored submission is waiting on. A person, named as a person -- never
# a status like "queued" or "pending run" that implies machinery this module
# does not have.
WAITING_ON = "a hand-authored acceptance suite"


def submit(job, done, store_path):
    """Store one described job. Returns the row exactly as written to disk.

    The row carries the user's words, a UTC timestamp, and what it waits on.
    No verdict, no score, no difficulty -- another ticket owns what a
    description becomes, and a placeholder field here would prejudge it.
    """
    if not job or not job.strip():
        raise ValueError(
            "a submission needs a described job -- got an empty description")
    if not done or not done.strip():
        raise ValueError(
            "a submission needs the user's own words for what done looks "
            "like -- got none")
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job": job.strip(),
        "done": done.strip(),
        "waiting_on": WAITING_ON,
    }
    with open(store_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def read_submissions(store_path):
    """Parse the store back into rows. A missing store is zero submissions --
    a real, countable result, not an error."""
    if not os.path.exists(store_path):
        return []
    rows = []
    with open(store_path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _refuse_money(text):
    """Refuse to return a sentence carrying a price (spec §5). Checked on the
    finished string -- the only place a price added by any future edit has to
    pass through. Restated from surface.py on purpose; see the note above the
    marker constants."""
    found = [m for m in _MONEY_SYMBOLS if m in text]
    found += sorted({m.group(0) for m in _MONEY_WORD_RE.finditer(text)})
    if found:
        raise ValueError(
            f"printed sentence contains {', '.join(repr(m) for m in found)} "
            f"-- no slice may print a price (playground spec §5). "
            f"Sentence was: {text!r}")
    return text


def listing_lines(store_path):
    """The submission listing, as printable lines.

    The count is printed even at zero, and the zero line says the store was
    read: an empty intake must not render like an intake nobody has looked
    at. No user text is echoed -- spec §5 gates every printed sentence from
    carrying a price and a user's own words may honestly contain one, so the
    listing prints module-authored copy only and the store file itself is the
    reading surface for full text.
    """
    rows = read_submissions(store_path)
    if not rows:
        lines = [
            f"0 submissions stored. The store at {store_path} was read and "
            f"is empty -- zero is a counted result."]
    else:
        lines = [f"{len(rows)} submission(s) stored, each waiting on "
                 f"{WAITING_ON}:"]
        lines.extend(f"  {row['ts']} -- waiting on {WAITING_ON}"
                     for row in rows)
        lines.append(
            "Full text lives in the store file; this listing prints none "
            "of it.")
    return [_refuse_money(line) for line in lines]


def notice_lines():
    """The no-run notice, printed on the form itself -- visibly, before the
    submission happens, never behind a hover or a --help flag. Worded so a
    screenshot with no caption cannot read as a queue: nothing here names a
    status, promises a later run, or implies machinery behind the store."""
    return [_refuse_money(line) for line in (
        "Nothing runs when you submit -- not now, not later.",
        "This form stores your description. A person reads it and writes "
        "the acceptance suite by hand.",
    )]


def receipt_lines(row, store_path):
    """What the user sees after submitting: the stored timestamp, what the
    row waits on (a person), and the running count."""
    count = len(read_submissions(store_path))
    return [_refuse_money(line) for line in (
        f"Stored at {row['ts']}. This submission waits on {WAITING_ON}.",
        f"{count} submission(s) stored in total.",
    )]


def _default_store_path():
    """product/submissions.jsonl. The ticket rules that submissions live
    under product/, and product/ is this package's own parent directory --
    self-location, not knowledge of the instrument repo's layout. Callers
    that want another store pass --store."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "submissions.jsonl")


def main(argv=None):
    """CLI entry. By the convention in product/pyproject.toml this is its own
    console script (gauntlet-playground-intake), not a subcommand of the
    pinned one."""
    import argparse
    parser = argparse.ArgumentParser(
        prog="gauntlet-playground-intake",
        description=("Describe a job for the gauntlet. Submitting stores "
                     "your words; nothing runs."))
    parser.add_argument(
        "--list", action="store_true",
        help="print the submission count and per-submission timestamps")
    parser.add_argument(
        "--store", default=None,
        help="path to the submissions JSONL "
             "(default: product/submissions.jsonl)")
    parser.add_argument(
        "--job", default=None,
        help="the job you want a model to do, in your own words")
    parser.add_argument(
        "--done", default=None,
        help="what you would accept as done, in your own words")
    args = parser.parse_args(argv)
    store = args.store or _default_store_path()

    if args.list:
        for line in listing_lines(store):
            print(line)
        return 0

    for line in notice_lines():
        print(line)
    job = args.job if args.job is not None else input(
        "The job, in your own words: ")
    done = args.done if args.done is not None else input(
        "What you would accept as done: ")
    row = submit(job, done, store)
    for line in receipt_lines(row, store):
        print(line)
    return 0

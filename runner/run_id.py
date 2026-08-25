#!/usr/bin/env python3
"""run_id.py -- the run_id format, as a parser instead of a comment.

A run_id names one cell of the design. It is also a directory name (the run's
scratch tree), a filename (results/transcripts/<run_id>.txt) and the key the
resume logic dedupes on, which is why it is a flat string and not a JSON blob.

    <sweep>--<model>--<effort>--<harness>--[<extension>--...]<task>--r<rep>

THE PROBLEM THIS MODULE EXISTS FOR (studio-handoff findings.md blocker 3,
issue #8). Until 2026-08-25 the format lived as a comment above judge.py:69 and
readers counted from the end:

    parts = run_id.split("--")
    task = parts[-2] if len(parts) >= 2 else ""

Counting from the end is correct for every id anyone had written, and the
stage-1 design adds segments -- agent, harness_level. Inserted before the last
two, `parts[-2]` is still the task and everything works. APPENDED, `parts[-2]`
becomes the rep: every run in the sweep resolves the same nonexistent task
directory, the judge scores every diff against "(task prompt unavailable)", and
not one error is raised. Nothing in the repo said which of those two things to
do, and the two are one keystroke apart.

THE ANCHOR IS THE FIX. The last segment IS the rep, `r` followed by digits, and
the parser checks it. An id built the wrong way now RAISES instead of returning
a plausible wrong answer. That is the whole design: a wrong answer that raises
costs one run, a wrong answer that parses costs a cell of data that looks like
data. Extensions therefore go between the harness field and the task, and the
rule is enforced by build_run_id rather than remembered.

EXTENSIONS SHOULD BE `name=value`. Position is what broke; a self-describing
segment cannot break the same way. parse_run_id returns them both ways --
`extra` in order, and `labels` for the name=value ones -- so a reader asking
"which agent produced this row?" never counts segments again.

JUDGE IDS ARE A DIFFERENT NAMESPACE. judge.py mints "judge-<head>--<worker id>",
which PREPENDS a segment: a worker parser reads sweep="judge-claude",
model=<the sweep>, effort=<the model> and so on, while task and rep still look
right -- every front field wrong, nothing raised. parse_run_id refuses them by
name; build_judge_run_id / parse_judge_run_id are the pair for that namespace.

Stdlib only, and it imports nothing from this repo, so both the writer (run.py)
and the readers (judge.py) can depend on it without a cycle. Not declared
CORE_MODULE: the core literal is duplicated in import_gate.py and its test on
purpose (that duplication is a control), and nothing in the core needs this.
"""
import re

DELIM = "--"
JUDGE_PREFIX = "judge-"
REP_RE = re.compile(r"^r(\d+)$")

# sweep, model, effort, harness. Fixed and leading, because they are the fields
# every id has had since the first sweep; extensions are appended after them and
# before the trailing pair, so adding one never moves a field anyone reads.
LEADING_FIELDS = ("sweep", "model", "effort", "harness")
TRAILING_FIELDS = ("task", "rep")
MIN_SEGMENTS = len(LEADING_FIELDS) + len(TRAILING_FIELDS)


class RunIdError(ValueError):
    """A run_id that cannot be read, or fields that cannot be written into one.

    A distinct type rather than bare ValueError so a caller can tell "this id
    is malformed" from any other ValueError raised nearby -- notably
    registry.resolve_model's, which a judge or ledger call site sits next to.
    """


def build_run_id(sweep, model, effort, harness, task, rep, extra=()):
    """The ONE place a run_id is written. `extra` lands before task and rep.

    Every field is validated here rather than at the readers: a field holding
    the delimiter (a task named "a--b") would parse as two segments and shift
    everything after it, and an empty field would collapse two ids into one.
    Both are cheap to refuse at the write site and impossible to detect at the
    read site, where the damage is a plausible wrong answer.
    """
    if not isinstance(rep, int) or isinstance(rep, bool) or rep < 0:
        raise RunIdError(f"rep must be a non-negative int, got {rep!r}")
    parts = [sweep, model, effort, harness, *extra, task]
    for value in parts:
        if not isinstance(value, str) or not value:
            raise RunIdError(f"run_id fields must be non-empty strings, "
                             f"got {value!r}")
        if DELIM in value:
            raise RunIdError(
                f"run_id field {value!r} contains the delimiter {DELIM!r}; it "
                f"would split into extra segments and shift every field after "
                f"it")
    return DELIM.join(parts + [f"r{rep}"])


def parse_run_id(run_id):
    """Named fields from a worker run_id. Raises RunIdError on anything else.

    Returns sweep/model/effort/harness/task (str), rep (int), extra (tuple of
    the extension segments in order) and labels (the name=value ones as a dict).
    """
    if not isinstance(run_id, str) or not run_id:
        raise RunIdError(f"not a run_id: {run_id!r}")
    if is_judge_run_id(run_id):
        raise RunIdError(
            f"{run_id!r} is a judge ledger id, not a worker run_id -- its "
            f"leading {JUDGE_PREFIX!r} segment shifts every field. Use "
            f"parse_judge_run_id.")

    parts = run_id.split(DELIM)
    if len(parts) < MIN_SEGMENTS:
        raise RunIdError(
            f"{run_id!r} has {len(parts)} segments, fewer than the "
            f"{MIN_SEGMENTS} the format requires "
            f"({DELIM.join(LEADING_FIELDS + ('[extension...]',) + TRAILING_FIELDS)})")

    m = REP_RE.match(parts[-1])
    if not m:
        raise RunIdError(
            f"{run_id!r} does not end in r<rep>; its last segment is "
            f"{parts[-1]!r}. New segments (agent, harness_level) belong BEFORE "
            f"the task and rep, never appended -- appended, every reader "
            f"resolves the wrong task and nothing raises.")

    fields = dict(zip(LEADING_FIELDS, parts[:len(LEADING_FIELDS)]))
    extra = tuple(parts[len(LEADING_FIELDS):-2])
    fields["extra"] = extra
    fields["labels"] = dict(
        seg.split("=", 1) for seg in extra if "=" in seg and not seg.startswith("="))
    fields["task"] = parts[-2]
    fields["rep"] = int(m.group(1))
    return fields


def is_judge_run_id(run_id):
    return isinstance(run_id, str) and run_id.startswith(JUDGE_PREFIX)


def build_judge_run_id(head, judged_run_id):
    """The id of one judge panel call: its own key, naming the run it scored."""
    if not head or DELIM in head:
        raise RunIdError(f"judge head {head!r} must be non-empty and free of "
                         f"{DELIM!r}")
    return f"{JUDGE_PREFIX}{head}{DELIM}{judged_run_id}"


def parse_judge_run_id(run_id):
    """{"head", "judged_run_id"} from a judge ledger id."""
    if not is_judge_run_id(run_id):
        raise RunIdError(f"{run_id!r} is not a judge ledger id "
                         f"(no {JUDGE_PREFIX!r} prefix)")
    head, _, judged = run_id[len(JUDGE_PREFIX):].partition(DELIM)
    if not head or not judged:
        raise RunIdError(f"{run_id!r} is not {JUDGE_PREFIX}<head>{DELIM}<run_id>")
    return {"head": head, "judged_run_id": judged}

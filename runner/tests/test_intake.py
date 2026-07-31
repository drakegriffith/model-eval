# Ticket 41, slice S6: the "push your job" intake, as a form and not a pipeline.
# The user describes a job and what they would accept as done; the module stores
# that as data and hands it to a human. The load-bearing assertions here are the
# negative space: no execution path out of a submission (asserted positively, at
# the AST and at runtime), no verdict/score/difficulty field, no queue-shaped
# copy, no printed price (spec §5). The scope stops at collection on purpose --
# it keeps the parked item 4 contest open -- so a test that "helpfully" checks a
# pipeline stage would be a spec violation, not a coverage gap.
import ast
import json
import os
import sys
from datetime import datetime

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
PRODUCT_DIR = os.path.join(REPO_ROOT, "product")

sys.path.insert(0, RUNNER_DIR)
sys.path.insert(0, PRODUCT_DIR)
from gauntlet_playground import intake  # noqa: E402


def test_submit_stores_a_timestamped_row(tmp_path):
    """AC 1: the form collects a described job plus the user's own words for
    "done", and stores each submission as data with a timestamp. The row on
    disk must equal the row returned -- the receipt is the record, not a
    paraphrase of it."""
    store = str(tmp_path / "submissions.jsonl")
    row = intake.submit(
        "Build a Three.js scene of my house",
        "It loads in a browser and I can orbit the camera",
        store)
    lines = [l for l in open(store, encoding="utf-8").read().splitlines() if l]
    assert len(lines) == 1
    on_disk = json.loads(lines[0])
    assert on_disk == row
    assert on_disk["job"] == "Build a Three.js scene of my house"
    assert on_disk["done"] == (
        "It loads in a browser and I can orbit the camera")
    ts = datetime.fromisoformat(on_disk["ts"])
    assert ts.tzinfo is not None, "timestamp must be timezone-aware, not naive"


def test_submit_appends_rather_than_overwrites(tmp_path):
    """Two submissions are two rows. An intake that keeps only the latest
    submission silently discards the earlier user's job."""
    store = str(tmp_path / "submissions.jsonl")
    intake.submit("first job", "first done", store)
    intake.submit("second job", "second done", store)
    rows = intake.read_submissions(store)
    assert [r["job"] for r in rows] == ["first job", "second job"]


def test_listing_prints_zero_as_zero(tmp_path):
    """AC 6: an empty intake must not render like an intake nobody has looked
    at. Zero is a counted result: the listing must say "0 submissions" out
    loud and say the store was actually read, not print nothing."""
    lines = intake.listing_lines(str(tmp_path / "submissions.jsonl"))
    text = "\n".join(lines)
    assert "0 submissions" in text
    assert "read" in text.lower(), (
        "the zero line must assert the store was read -- a blank zero is "
        "indistinguishable from a listing that never ran")


def test_listing_prints_its_count_when_nonempty(tmp_path):
    store = str(tmp_path / "submissions.jsonl")
    intake.submit("job one", "done one", store)
    intake.submit("job two", "done two", store)
    text = "\n".join(intake.listing_lines(store))
    assert "2 submission" in text


def test_listing_never_echoes_user_text(tmp_path):
    """Spec §5: no slice may print a price, and a user's own words may honestly
    contain one. The listing therefore prints module-authored copy only --
    counts, timestamps, what each row waits on -- and the JSONL store is the
    reading surface for full text. Echoing user prose would force a choice
    between refusing an honest sentence and printing a price; not echoing
    dissolves it."""
    store = str(tmp_path / "submissions.jsonl")
    intake.submit("port my app, budget is $400", "it compiles", store)
    lines = intake.listing_lines(store)
    assert lines, "listing must still render a store containing money words"
    text = "\n".join(lines)
    assert "$400" not in text
    assert "port my app" not in text


# Words that would make the surface read as a queue. "A screenshot of it with
# no caption must not read as a queue" (AC 3), and no copy may promise a
# future run (AC 8) or imply machinery that does not exist (AC 7). Checked
# case-insensitively against everything the form and the listing print.
QUEUE_WORDS = ("queue", "queued", "pending", "scheduled", "will run",
               "position", "in line")


def test_form_states_no_run_visibly(tmp_path, capsys):
    """AC 3: the surface itself says, without a hover, that submitting runs
    nothing and a person writes the acceptance suite. The notice must be in
    the same stdout as the submission -- a docstring or --help text is a
    hover."""
    store = str(tmp_path / "submissions.jsonl")
    rc = intake.main(["--job", "draw a chart", "--done", "it has axes",
                      "--store", store])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Nothing runs" in out
    assert "person" in out
    assert "by hand" in out
    assert len(intake.read_submissions(store)) == 1


def test_no_surface_reads_as_a_queue(tmp_path, capsys):
    """ACs 3, 7, 8: no queue words, no promise of a future run, anywhere a
    user looks -- the form, the receipt, and the listing, empty and not."""
    store = str(tmp_path / "submissions.jsonl")
    intake.main(["--list", "--store", store])
    intake.main(["--job", "a job", "--done", "done words", "--store", store])
    intake.main(["--list", "--store", store])
    out = capsys.readouterr().out.lower()
    for word in QUEUE_WORDS:
        assert word not in out, f"user-visible copy reads as a queue: {word!r}"


def test_receipt_names_the_human_wait_not_a_status(tmp_path):
    """AC 7: a stored submission records what it waits on -- a hand-authored
    suite -- never a machinery status. Checked on the stored row, so the
    promise survives any future printing change."""
    store = str(tmp_path / "submissions.jsonl")
    row = intake.submit("a job", "done words", store)
    assert "hand-authored" in row["waiting_on"]
    for word in QUEUE_WORDS:
        assert word not in row["waiting_on"].lower()


def test_blank_submissions_are_refused(tmp_path):
    """A stored row with no job or no "done" is not a submission, it is noise a
    human would have to triage. Refuse it at the door."""
    store = str(tmp_path / "submissions.jsonl")
    with pytest.raises(ValueError):
        intake.submit("   ", "something", store)
    with pytest.raises(ValueError):
        intake.submit("something", "", store)
    assert intake.read_submissions(store) == []


INTAKE_PATH = os.path.join(PRODUCT_DIR, "gauntlet_playground", "intake.py")


def _intake_source():
    with open(INTAKE_PATH, encoding="utf-8") as fh:
        return fh.read()


def _imported_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_execution_path_exists_static(tmp_path):
    """AC 2, the AST half. "No run happened" and "the run code was never
    reached" look identical from the outside; this asserts the second by
    enumeration. The import list is asserted EXACTLY -- five stdlib modules
    and nothing else -- so reaching the executor, a subprocess, the network,
    or a ledger would first have to fail this test, not merely go unnoticed."""
    tree = ast.parse(_intake_source())
    assert _imported_names(tree) == {"json", "os", "re", "datetime",
                                     "argparse"}

    # The complete list of code paths out of the store, matching the module
    # docstring's enumeration: submit() writes it; read_submissions() reads
    # it; nothing else in the module touches a file at all.
    funcs_opening_files = {
        fn.name
        for fn in tree.body if isinstance(fn, ast.FunctionDef)
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "open"}
    assert funcs_opening_files == {"submit", "read_submissions"}


def test_no_other_product_module_reaches_the_store():
    """The enumeration above covers intake.py; this covers the rest of the
    package. No other product module names the intake or its store, so there
    is no second door out of a stored submission."""
    pkg = os.path.join(PRODUCT_DIR, "gauntlet_playground")
    others = [n for n in sorted(os.listdir(pkg))
              if n.endswith(".py") and n != "intake.py"]
    assert others, "the product package should have other modules to check"
    for name in others:
        with open(os.path.join(pkg, name), encoding="utf-8") as fh:
            src = fh.read()
        assert "intake" not in src, f"{name} references the intake"
        assert "submissions.jsonl" not in src, f"{name} names the store"


def test_submit_causes_zero_runs_and_zero_spend(tmp_path):
    """AC 2, the runtime half. A submit is shown to (a) leave the executor
    module unloaded -- the run code was never reached, not merely quiet --
    and (b) create exactly one file, the store: no usage ledger, no results
    file, no spend record of any kind, anywhere."""
    sys.modules.pop("gauntlet_playground.executor", None)
    store = tmp_path / "submissions.jsonl"
    intake.submit("a hard job", "done words", str(store))
    assert "gauntlet_playground.executor" not in sys.modules

    created = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    assert created == [store]


def test_row_has_no_verdict_score_or_difficulty(tmp_path):
    """AC 4: the key set is asserted EXACTLY, so a verdict, score, difficulty
    or status field -- even as a well-meant None placeholder -- fails here.
    Another ticket owns what a description becomes."""
    store = str(tmp_path / "submissions.jsonl")
    row = intake.submit("a job", "done words", store)
    assert sorted(row) == ["done", "job", "ts", "waiting_on"]
    on_disk = json.loads(open(store, encoding="utf-8").read())
    assert sorted(on_disk) == ["done", "job", "ts", "waiting_on"]


def test_money_gate_holds_on_printed_lines():
    """Spec §5 via the module's own gate (restated from surface.py on
    purpose -- see the note in intake.py). Prices refuse; the honest words
    that contain money words as substrings pass."""
    for bad in ("costs $3", "five dollars", "about 40 usd", "one cent more"):
        with pytest.raises(ValueError):
            intake._refuse_money(bad)
    for good in ("40 percent better", "a recent submission", "decent work"):
        assert intake._refuse_money(good) == good


def test_intake_is_a_subject_of_import_gate_half_b():
    """AC 5, asserted as presence and not absence-of-error: half B still
    reports PASS, its subject count is >= 1, and intake.py is IN the subject
    list -- a gate that passed over a set missing this file would prove
    nothing about it."""
    import import_gate
    result = import_gate.check_product_depends_on_core_only()
    assert result["status"] == import_gate.PASS
    assert len(result["subjects"]) >= 1
    assert os.path.join("gauntlet_playground", "intake.py") in (
        result["subjects"])


def test_eval_bar_no_dependency_on_the_authoring_ticket():
    """AC 8: nothing shipped here creates or documents a dependency from a
    submission to ticket 24's authoring floor, and no copy promises a future
    run. The addendum's claim stays an argument in the ticket body, never a
    behaviour -- so the source may not even name that ticket, and the no-run
    notice must close the "later" door explicitly."""
    src = _intake_source()
    assert "ticket 24" not in src
    assert "24" not in src.replace("ticket 41", "")
    assert "authoring floor" not in src
    notice = "\n".join(intake.notice_lines())
    assert "not now, not later" in notice

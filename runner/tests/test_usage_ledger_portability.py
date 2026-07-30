"""test_usage_ledger_portability.py -- the F3 repair, proven twice (ticket 37).

The 14b consult recorded `usage_ledger` as portability-broken because it derived
ROOT/RUNNER_DIR/USAGE_PATH/RESULTS_PATH/TRANSCRIPTS_DIR from its own `__file__`,
baking this repo's directory layout into a module the product is supposed to
consume from a different tree. AC#2 asks for two different proofs and says which
is which, so every test below is named `test_source_*` or `test_behaviour_*`:

  - `test_source_*` reads the module TEXT. It proves the string `__file__` is
    gone. It cannot prove the behaviour is fixed: a module could resolve the
    repo layout by walking up from `os.getcwd()`, or from `sys.modules`, and
    every source test here would still be green.
  - `test_behaviour_*` runs the real functions from a working directory that is
    not this repo, against caller-supplied temp paths, and asserts the repo tree
    is untouched. That is the proof that matters; the source test is the cheap
    tripwire that catches the defect coming back by the same door it left by.

The real runner/results tree is never written. It is stat-ed before and after as
the negative half of the behaviour claim: "wrote the right file" and "wrote the
right file AND NOTHING ELSE" are different assertions, and only the second one
is what F3 was about.
"""
import json
import os
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RUNNER_DIR)
sys.path.insert(0, RUNNER_DIR)
import usage_ledger  # noqa: E402

# The constants F3 named. Removed, not renamed -- a module that still carries
# any of them is still handing every caller this repo's layout.
FORBIDDEN_MODULE_ATTRS = ("ROOT", "RUNNER_DIR", "USAGE_PATH", "RESULTS_PATH",
                          "TRANSCRIPTS_DIR")

RESULT_EVENT = json.dumps({
    "type": "result", "num_turns": 2,
    "usage": {"input_tokens": 1_000, "output_tokens": 200,
              "cache_creation_input_tokens": 5_000,
              "cache_read_input_tokens": 90_000},
})


def snapshot(root):
    """(relpath, size, mtime_ns) for every file under `root`. Stat only -- this
    fixture must not itself be the thing that touches the corpus."""
    seen = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except FileNotFoundError:
                continue
            seen[os.path.relpath(path, root)] = (st.st_size, st.st_mtime_ns)
    return seen


@pytest.fixture
def repo_untouched():
    """Assert the real corpus is byte-identical before and after the test body.

    Presence is asserted, not assumed: a snapshot of zero files would compare
    equal to any amount of damage.
    """
    results_dir = os.path.join(RUNNER_DIR, "results")
    before = snapshot(results_dir)
    assert len(before) > 0, f"no corpus at {results_dir} -- nothing was guarded"
    yield len(before)
    assert snapshot(results_dir) == before, (
        f"the module wrote into {results_dir} while being told to use temp paths")


@pytest.fixture
def foreign_tree(tmp_path, monkeypatch):
    """A caller's layout, in a temp dir, entered as the working directory.

    tmp_path is outside the repo, so anything the module resolves from
    `os.getcwd()` lands here and anything it resolves from its own `__file__`
    lands in the repo -- which is what `repo_untouched` is watching for.
    """
    monkeypatch.chdir(tmp_path)
    results = tmp_path / "corpus" / "results.jsonl"
    transcripts = tmp_path / "corpus" / "transcripts"
    usage = tmp_path / "elsewhere" / "usage.jsonl"
    transcripts.mkdir(parents=True)
    results.write_text(
        json.dumps({"run_id": "r1", "ts": "2026-07-26T00:00:00Z",
                    "model": "claude-haiku-4-5", "tokens_in": 7, "tokens_out": 3})
        + "\n"
        + json.dumps({"run_id": "r2", "ts": "2026-07-28T00:00:00Z",
                      "model": "sol", "tokens_in": 11, "tokens_out": 4})
        + "\n",
        encoding="utf-8")
    (transcripts / "r1.txt").write_text(RESULT_EVENT, encoding="utf-8")
    return {"results": str(results), "transcripts": str(transcripts),
            "usage": str(usage), "root": str(tmp_path)}


# --------------------------------------------------------------------------- #
# SOURCE proofs -- these read text. They prove a string is absent, nothing more.
# --------------------------------------------------------------------------- #

def test_source_the_module_text_contains_no_dunder_file():
    """AC#2's cheap half. Green here means the string is gone; it does NOT mean
    the paths are injected -- see the behaviour tests for that claim."""
    src = open(os.path.join(RUNNER_DIR, "usage_ledger.py"), encoding="utf-8").read()
    assert "__file__" not in src


def test_source_no_repo_path_constant_survives_at_module_level():
    """The five names F3 cited, gone from the module namespace. A caller that
    still reaches for `usage_ledger.USAGE_PATH` must break loudly rather than
    silently receive this repo's layout."""
    present = [a for a in FORBIDDEN_MODULE_ATTRS if hasattr(usage_ledger, a)]
    assert present == [], f"module still exports repo paths: {present}"


def test_source_paths_for_repo_holds_the_layout_in_exactly_one_place():
    """The layout literal did not disappear, it moved behind a parameter. This
    pins that it is derived from the caller's root and from nothing else."""
    paths = usage_ledger.paths_for_repo(os.path.join("/somewhere", "else"))
    assert paths.results == "/somewhere/else/runner/results/results.jsonl"
    assert paths.transcripts == "/somewhere/else/runner/results/transcripts"
    assert paths.usage == "/somewhere/else/runner/results/usage.jsonl"
    assert REPO_ROOT not in " ".join(paths)


# --------------------------------------------------------------------------- #
# BEHAVIOUR proofs -- real reads and writes, from a cwd that is not the repo.
# --------------------------------------------------------------------------- #

def test_behaviour_retrofit_from_a_foreign_cwd_writes_only_where_told(
        foreign_tree, repo_untouched):
    """The proof AC#2 actually asks for: a real retrofit, real transcript parse,
    real append -- all against caller-supplied paths, from outside the repo."""
    summary = usage_ledger.retrofit(foreign_tree["results"],
                                    foreign_tree["transcripts"],
                                    foreign_tree["usage"])
    assert summary == {"written": 2, "skipped_existing": 0}

    rows = [json.loads(l) for l in open(foreign_tree["usage"], encoding="utf-8")
            if l.strip()]
    assert [r["run_id"] for r in rows] == ["r1", "r2"]
    # r1 had a transcript, so its tokens_in is the fresh parse (1k + 5k + 90k),
    # not the 7 recorded on the row -- the read really happened.
    assert rows[0]["tokens_in"] == 96_000
    assert rows[0]["retrofit_status"] == "measured"


def test_behaviour_stamp_provenance_from_a_foreign_cwd_reads_only_caller_paths(
        foreign_tree, repo_untouched):
    """--apply writes in place. It must rewrite the caller's corpus and no other."""
    usage_ledger.retrofit(foreign_tree["results"], foreign_tree["transcripts"],
                          foreign_tree["usage"])
    report = usage_ledger.stamp_provenance(foreign_tree["results"],
                                           foreign_tree["usage"], apply=True)
    assert report["inspected"] == 2
    assert report["path"] == foreign_tree["results"]
    stamped = [json.loads(l) for l in open(foreign_tree["results"], encoding="utf-8")
               if l.strip()]
    assert all("tokens_in_status" in r for r in stamped)


def test_behaviour_recovered_tokens_in_requires_an_explicit_path(
        foreign_tree, repo_untouched):
    """The default was this repo's usage.jsonl, which meant a caller who forgot
    the argument got a silent answer about the wrong tree. Now it is a TypeError.

    Both rows come back: r1 because its transcript was re-parsed, r2 because it
    is a codex row whose stored total was never affected by the v1 bug. The
    point of the assertion is the tree the numbers came from, not the numbers.
    """
    usage_ledger.retrofit(foreign_tree["results"], foreign_tree["transcripts"],
                          foreign_tree["usage"])
    assert usage_ledger.recovered_tokens_in(foreign_tree["usage"]) == {"r1": 96_000,
                                                                      "r2": 11}
    with pytest.raises(TypeError):
        usage_ledger.recovered_tokens_in()


def test_behaviour_cli_resolves_its_root_from_the_cwd_and_says_so_when_wrong(
        tmp_path, repo_untouched):
    """The CLI keeps working (`python3 runner/usage_ledger.py retrofit` from the
    repo) because the root now comes from the caller's cwd instead of __file__.
    From anywhere else there is no corpus, and the failure names the flag that
    fixes it rather than silently ledgering zero rows."""
    proc = subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "usage_ledger.py"), "retrofit"],
        cwd=str(tmp_path), capture_output=True, text=True,
        env={**os.environ, "MODEL_GAUNTLET_ROOT": ""})
    assert proc.returncode != 0
    assert "--repo-root" in proc.stderr
    assert str(tmp_path) in proc.stderr


def test_behaviour_cli_honours_repo_root_pointing_at_a_foreign_tree(
        tmp_path, repo_untouched):
    """A product with this repo's layout somewhere else is served by --repo-root
    alone -- the end state F3 was blocking."""
    results_dir = tmp_path / "elsewhere" / "runner" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "results.jsonl").write_text(
        json.dumps({"run_id": "z9", "ts": "2026-07-28T00:00:00Z",
                    "model": "sol", "tokens_in": 5, "tokens_out": 1}) + "\n",
        encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, os.path.join(RUNNER_DIR, "usage_ledger.py"), "retrofit",
         "--repo-root", str(tmp_path / "elsewhere")],
        cwd=str(tmp_path), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    written = [json.loads(l) for l in
               open(results_dir / "usage.jsonl", encoding="utf-8") if l.strip()]
    assert [r["run_id"] for r in written] == ["z9"]

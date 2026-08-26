"""test_stage0_probe.py -- issue #26: the stage-0 noise-probe invoker.

Before this file, `runner/serving_registry.record_noise_probe` had zero
invokers outside its own tests, and no config existed to dispatch the 5
sequential reps the pre-registration's stage 0 calls for
(docs/studio-handoff/prompt-2-run-experiment.md, amendments A1/A3/A6).
`runner/stage0_probe.py` is the invoker; this file proves its pieces
separately, per the prior-art gate's own disposition (randomness never
touches a gate; the derived numbers are pure functions of the rows):

  1. `derive_stage0` and `tag_stage0` are pure functions over a list of row
     dicts, driven entirely by fixtures -- no subprocess, no filesystem,
     each enumerator named in its own test (issue #26 acceptance #3). Only
     SCORED rows (run_status.py) enter any decision -- a verifier caught the
     first cut reading exit_reason-blind, which let timed-out reps derive as
     "identical" and mock rows record a fabricated noise probe.
  2. `finalize_stage0` (derive + conditionally record + render) is exercised
     directly with fixture rows and a throwaway registry copy -- no
     subprocess needed to prove the record-vs-provisional-vs-refuse split.
  3. `run_preflight` is exercised against the REAL captured `lms ps`
     fixtures in tests/fixtures/ (never against a live LM Studio).
  4. one end-to-end test drives the REAL run.py, under --mock, through
     stage0_probe.main(), proving the DISPATCH+TAGGING plumbing produces
     exactly 5 rows tagged `stage: 0` while the live corpus (results.jsonl,
     usage.jsonl) AND the live registry (models.yaml) stay byte-identical --
     and, since every --mock row is unscored by construction, that main()
     REFUSES the recording step (exit 2) rather than recording a fabricated
     probe, the exact defect a verifier caught in the first cut.

Every fixture list below states its own row count instead of trusting len();
a loop over an accidentally-short fixture list would otherwise pass while
proving nothing (INDEX.md's absence-is-not-evidence disposition).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
sys.path.insert(0, RUNNER_DIR)
import corpus_guard  # noqa: E402
import serving_registry as sr  # noqa: E402
import stage0_probe  # noqa: E402

REAL_RESULTS = os.path.join(RUNNER_DIR, "results", "results.jsonl")
REAL_USAGE = os.path.join(RUNNER_DIR, "results", "usage.jsonl")
REAL_REGISTRY = sr.REGISTRY_PATH
REAL_STAGE0_CONFIG = os.path.join(RUNNER_DIR, "runs-glm-stage0.yaml")
LMS_TARGET_STATE = os.path.join(FIXTURES, "lms-ps-target-state.txt")
LMS_LIVE_MISMATCH = os.path.join(FIXTURES, "lms-ps-live-mismatch.txt")


def _row(pass_=True, turns=1, wall_s=10.0, acceptance_requests=None,
        sweep="glm-stage0", exit_reason="ok"):
    return {"pass": pass_, "turns": turns, "wall_s": wall_s,
            "acceptance_requests": acceptance_requests, "sweep": sweep,
            "exit_reason": exit_reason}


# --------------------------------------------------------------------------- #
# Seam 1 -- derive_stage0, a pure function over a list of row dicts
# --------------------------------------------------------------------------- #
def test_five_identical_rows_yield_reps_2():
    """Enumerator: 5/5 identical passes -> A6 cuts stage-1 reps to 2."""
    rows = [_row(pass_=True) for _ in range(5)]
    assert len(rows) == 5, f"expected 5 fixture rows, built {len(rows)}"
    d = stage0_probe.derive_stage0(rows)
    assert d["flips"] == 0
    assert d["of"] == 5
    assert d["identical"] == 5
    assert d["flip_rate"] == 0.0
    assert d["rep_decision"] == "reps 2"
    assert d["provisional"] is False


def test_one_flip_yields_reps_3():
    """Enumerator: 4 pass, 1 fail -> A6 keeps 3 reps."""
    rows = [_row(pass_=True), _row(pass_=True), _row(pass_=False),
            _row(pass_=True), _row(pass_=True)]
    assert len(rows) == 5, f"expected 5 fixture rows, built {len(rows)}"
    d = stage0_probe.derive_stage0(rows)
    assert d["flips"] == 1
    assert d["identical"] == 4
    assert d["of"] == 5
    assert d["rep_decision"] == "reps 3"


def test_identical_and_flip_rate_are_order_invariant():
    """A verifier caught the first cut comparing every row against rows[0]:
    [F,T,T,T,T] and [T,T,T,T,F] derived DIFFERENT numbers (1/5 vs 4/5
    identical) for the same 4-1 split, purely from dispatch order. identical
    is now the count of the MAJORITY outcome, so both permutations below
    must derive identically."""
    a = [_row(pass_=p) for p in (False, True, True, True, True)]
    b = [_row(pass_=p) for p in (True, True, True, True, False)]
    da, db = stage0_probe.derive_stage0(a), stage0_probe.derive_stage0(b)
    assert da["identical"] == db["identical"] == 4
    assert da["flip_rate"] == db["flip_rate"] == pytest.approx(0.2)
    assert da["flips"] == db["flips"] == 1
    assert da["rep_decision"] == db["rep_decision"] == "reps 3"


def test_a_rep_at_ten_requests_flips_k():
    """Enumerator: one rep at exactly 10 acceptance_requests (A1's own
    threshold, ">= 10") re-registers K."""
    rows = [_row(acceptance_requests=v) for v in (3, 5, 10, 4, 2)]
    assert len(rows) == 5, f"expected 5 fixture rows, built {len(rows)}"
    d = stage0_probe.derive_stage0(rows)
    assert d["k_flip"] is True
    assert d["max_acceptance_requests"] == 10
    assert "re-register" in d["k_decision"].lower()


def test_no_rep_reaching_ten_requests_leaves_k_unchanged():
    """Positive control for the K-flip: every rep strictly under 10 clears it."""
    rows = [_row(acceptance_requests=v) for v in (3, 5, 9, 4, 2)]
    d = stage0_probe.derive_stage0(rows)
    assert d["k_flip"] is False
    assert d["max_acceptance_requests"] == 9


def test_k_flip_ignores_reps_that_never_recorded_a_request():
    """Mock/unbrokered reps record acceptance_requests=None; None must not
    crash the max() or be misread as 0 >= 10."""
    rows = [_row(acceptance_requests=None) for _ in range(5)]
    d = stage0_probe.derive_stage0(rows)
    assert d["k_flip"] is False
    assert d["max_acceptance_requests"] is None


@pytest.mark.parametrize("max_turns,expected_n", [
    (2, 10),   # 3*2=6, rounded up to the next multiple of 10
    (12, 40),  # 3*12=36, rounded up to 40
    (10, 30),  # 3*10=30 is ALREADY a multiple of 10 -- the rounding rule
               # (ceil(raw/10)*10) does not bump an exact multiple further
])
def test_n_is_3x_max_turns_rounded_up_to_the_next_multiple_of_ten(max_turns, expected_n):
    rows = [_row(turns=t) for t in (1, max_turns, 1, 1, 1)]
    d = stage0_probe.derive_stage0(rows)
    assert d["max_turns"] == max_turns
    assert d["n_cap"] == expected_n


def test_derive_stage0_refuses_when_max_turns_over_scored_rows_is_zero():
    """N=0 is could-not-determine, never a decision -- the exact bug a
    --mock run would trigger silently (turns never leaves its zero default)
    if this were allowed to print 'N (turn cap): 0' as a real answer."""
    rows = [_row(turns=0) for _ in range(5)]
    with pytest.raises(RuntimeError) as e:
        stage0_probe.derive_stage0(rows)
    assert "turn" in str(e.value).lower()


def test_derive_stage0_refuses_when_a_scored_row_has_no_turns():
    rows = [_row(turns=3), _row(turns=None), _row(turns=2), _row(turns=4),
            _row(turns=1)]
    with pytest.raises(RuntimeError):
        stage0_probe.derive_stage0(rows)


def test_wall_ratio_is_max_over_min():
    rows = [_row(wall_s=w) for w in (100.0, 50.0, 200.0, 80.0, 60.0)]
    d = stage0_probe.derive_stage0(rows)
    assert d["max_wall_s"] == 200.0
    assert d["min_wall_s"] == 50.0
    assert d["wall_ratio"] == pytest.approx(4.0)


def test_wall_ratio_is_none_when_min_wall_s_is_zero():
    """Mock rows never call run_cli, so wall_s stays 0.0 on every one of
    them -- a max/min here must not raise ZeroDivisionError, and a ratio
    against zero elapsed time is not a measurement, so it is reported
    absent rather than as inf."""
    rows = [_row(wall_s=0.0) for _ in range(5)]
    d = stage0_probe.derive_stage0(rows)
    assert d["wall_ratio"] is None


def test_derive_stage0_refuses_zero_rows():
    """Absence is not evidence: a probe that inspected 0 rows proves
    nothing about noise, and must not silently report zeroed-out numbers."""
    with pytest.raises(ValueError) as e:
        stage0_probe.derive_stage0([])
    assert "0 row" in str(e.value) or "zero row" in str(e.value).lower() \
        or "0 of 0" in str(e.value)


def test_derive_stage0_refuses_when_nothing_scored():
    """Enumerator: 5 timeouts -> 0 SCORED rows -> refused outright, same
    posture as the empty-list case (a probe with zero scored reps proves
    nothing), distinct from the PARTIAL-scoring case below."""
    rows = [_row(exit_reason="timeout") for _ in range(5)]
    with pytest.raises(ValueError) as e:
        stage0_probe.derive_stage0(rows)
    assert "0 of 5" in str(e.value)
    assert "timeout" in str(e.value).lower()


def test_partial_scoring_is_provisional_and_reports_the_lost_row():
    """Enumerator: 4 ok + 1 timeout -> derived numbers come from the 4
    scored rows only, and the result is marked provisional so a caller
    knows not to record it."""
    rows = [_row(pass_=True) for _ in range(4)] + [_row(exit_reason="timeout")]
    assert len(rows) == 5, f"expected 5 fixture rows, built {len(rows)}"
    d = stage0_probe.derive_stage0(rows)
    assert d["of"] == 4
    assert d["produced"] == 5
    assert d["provisional"] is True
    assert d["excluded"] == {"timeout": 1}
    assert d["identical"] == 4  # all 4 scored rows passed
    assert d["rep_decision"] == "reps 2"


def test_run_status_forced_fail_never_looks_identical_from_pass_alone():
    """Sanity check on the fixture convention itself: run.py forces
    pass=False for every non-'ok' exit_reason (run.py:~1848), so a real
    timeout row never actually carries pass=True the way a naive fixture
    might. The `_row` default (exit_reason='ok') matches a real scored row;
    the fixtures above that pass exit_reason='timeout' correctly leave
    `pass_` at its default since it is excluded before `pass` is ever read."""
    timeout_row = _row(exit_reason="timeout")
    assert timeout_row["exit_reason"] == "timeout"


# --------------------------------------------------------------------------- #
# Seam 2 -- tag_stage0, a pure function that stamps `stage: 0`
# --------------------------------------------------------------------------- #
def test_tag_stage0_stamps_only_matching_sweep_rows():
    rows = [_row(sweep="glm-stage0"), _row(sweep="glm-stage0"),
            _row(sweep="some-other-sweep")]
    assert len(rows) == 3, f"expected 3 fixture rows, built {len(rows)}"
    tagged = stage0_probe.tag_stage0(rows, sweep="glm-stage0")
    stamped = [r for r in tagged if r.get("stage") == 0]
    untouched = [r for r in tagged if "stage" not in r]
    assert len(stamped) == 2, f"expected 2 stamped rows, saw {len(stamped)}"
    assert len(untouched) == 1, f"expected 1 untouched row, saw {len(untouched)}"


def test_tag_stage0_does_not_mutate_its_input():
    rows = [_row(sweep="glm-stage0")]
    stage0_probe.tag_stage0(rows, sweep="glm-stage0")
    assert "stage" not in rows[0], "tag_stage0 must return a new list, not mutate in place"


# --------------------------------------------------------------------------- #
# Seam 3 -- the #8 comment text names every derived number
# --------------------------------------------------------------------------- #
def test_render_comment_prints_all_the_derived_numbers():
    rows = [_row(pass_=True, turns=t, wall_s=w, acceptance_requests=a)
            for t, w, a in [(2, 100.0, 3), (2, 50.0, 5), (2, 200.0, 10),
                            (2, 80.0, 4), (2, 60.0, 2)]]
    d = stage0_probe.derive_stage0(rows)
    text = stage0_probe.render_comment(d, task="t3-a", date="2026-08-25")
    assert "t3-a" in text
    assert "2026-08-25" in text
    assert str(d["flips"]) in text
    assert str(d["max_turns"]) in text
    assert str(d["n_cap"]) in text
    assert str(d["max_acceptance_requests"]) in text
    assert d["rep_decision"] in text
    assert "re-register" in text.lower()  # this fixture's rep hit 10 requests
    assert "PROVISIONAL" not in text


def test_render_comment_marks_a_provisional_probe_and_states_what_was_lost():
    rows = [_row(pass_=True) for _ in range(4)] + [_row(exit_reason="timeout")]
    d = stage0_probe.derive_stage0(rows)
    text = stage0_probe.render_comment(d, task="t3-a", date="2026-08-25",
                                       dispatched=5)
    assert "PROVISIONAL" in text
    assert "NOT RECORDED" in text
    assert "timeout=1" in text
    assert "4/4/5" not in text  # scored/produced/dispatched, not a typo'd order
    assert "4" in text and "5" in text


# --------------------------------------------------------------------------- #
# Seam 4 -- finalize_stage0: derive + conditionally record + render, in one
# place, exercised directly with a throwaway registry copy (no subprocess).
# --------------------------------------------------------------------------- #
@pytest.fixture
def registry_copy(tmp_path):
    dst = tmp_path / "models.yaml"
    shutil.copy2(REAL_REGISTRY, dst)
    return dst


def test_finalize_stage0_records_when_every_row_is_scored(registry_copy):
    rows = [_row(pass_=True, turns=2) for _ in range(5)]
    comment, recorded = stage0_probe.finalize_stage0(
        rows, str(registry_copy), date="2026-08-25", task="t3-a")
    assert recorded is True
    assert "Recorded to registry: glm-4.7 x claude-code" in comment
    updated = sr.load_rows(str(registry_copy))
    row = sr.find_row(updated, "glm-4.7", "claude-code")
    assert row["noise_probe"] == {"flip_rate": 0.0, "date": "2026-08-25",
                                  "identical": 5, "of": 5}
    assert row["deterministic_loops"] is True


def test_finalize_stage0_skips_the_write_when_provisional(registry_copy):
    before = registry_copy.read_text(encoding="utf-8")
    rows = [_row(pass_=True, turns=2) for _ in range(4)] + [
        _row(exit_reason="timeout", turns=2)]
    comment, recorded = stage0_probe.finalize_stage0(
        rows, str(registry_copy), date="2026-08-25", task="t3-a", dispatched=5)
    assert recorded is False
    assert "PROVISIONAL" in comment
    after = registry_copy.read_text(encoding="utf-8")
    assert after == before, "a provisional probe must not touch the registry file"


def test_finalize_stage0_raises_before_touching_anything_when_nothing_scored(
        registry_copy):
    before = registry_copy.read_text(encoding="utf-8")
    rows = [_row(exit_reason="timeout") for _ in range(5)]
    with pytest.raises(ValueError):
        stage0_probe.finalize_stage0(rows, str(registry_copy),
                                     date="2026-08-25", task="t3-a")
    after = registry_copy.read_text(encoding="utf-8")
    assert after == before, "a refused probe must not touch the registry file"


# --------------------------------------------------------------------------- #
# Seam 5 -- the real runs-glm-stage0.yaml, read as a library (no subprocess,
# no network): valid, and gates cleanly against the real live registry.
# --------------------------------------------------------------------------- #
def test_the_real_stage0_config_declares_one_task_five_reps_no_harness():
    import run as runner  # noqa: E402  (imported here, not module level, so
                                        # a runner import failure fails only
                                        # this seam's tests, not the whole file)
    with open(REAL_STAGE0_CONFIG, "r", encoding="utf-8") as f:
        cfg = runner.parse_yaml(f.read())
    runs = runner.build_runs(cfg)
    assert len(runs) == 5, f"expected 5 runs (1 task x 5 reps), built {len(runs)}"
    tasks = {r["task"] for r in runs}
    assert tasks == {"t3-a"}, f"expected exactly task t3-a, saw {tasks}"
    reps = sorted(r["rep"] for r in runs)
    assert reps == [1, 2, 3, 4, 5]
    assert all(r["harness"] is False for r in runs)
    assert all(r["driver"] == "claude-code" for r in runs)
    assert (cfg.get("defaults", {}) or {}).get("k_acceptance") == 20


def test_the_real_stage0_config_gates_cleanly_against_the_live_registry():
    """Read-only: proves the declared serving block matches the real
    glm-4.7 x claude-code row (the same check run.py's dispatch loop makes
    before the first CLI call), without running anything."""
    import run as runner  # noqa: E402
    with open(REAL_STAGE0_CONFIG, "r", encoding="utf-8") as f:
        cfg = runner.parse_yaml(f.read())
    requested = runner.serving_config_from(cfg)
    rows = sr.load_rows()
    sr.check_dispatch(rows, "glm-4.7", "claude-code", requested,
                      harness_level=None)


# --------------------------------------------------------------------------- #
# Seam 6 -- run_preflight, against the REAL captured `lms ps` fixtures
# (tests/fixtures/), never against a live LM Studio.
# --------------------------------------------------------------------------- #
def test_run_preflight_passes_when_the_live_capture_matches_the_row():
    code = stage0_probe.run_preflight(REAL_REGISTRY, lms_output=LMS_TARGET_STATE)
    assert code == 0


def test_run_preflight_refuses_on_a_live_mismatch():
    """lms-ps-live-mismatch.txt is captured verbatim from the Mac Studio
    showing CONTEXT=65536, PARALLEL=4 against the row's 131072/1 -- the
    state the server was ACTUALLY in on 2026-08-25, not a constructed
    fixture (see tests/test_lms_preflight.py's own docstring)."""
    code = stage0_probe.run_preflight(REAL_REGISTRY, lms_output=LMS_LIVE_MISMATCH)
    assert code == sr.EXIT_PREFLIGHT_MISMATCH


# --------------------------------------------------------------------------- #
# Seam 7 -- end to end, real run.py, under --mock, through stage0_probe.main()
# --------------------------------------------------------------------------- #
def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture
def live_files_untouched():
    """Positive control, same shape as test_corpus_pinning.py's
    corpus_untouched: presence asserted first (a hash of a missing file
    proves nothing), then before/after equality on results, usage AND the
    model registry."""
    paths = [REAL_RESULTS, REAL_USAGE, REAL_REGISTRY]
    for p in paths:
        assert os.path.exists(p), f"missing live file {p}"
    before = [_sha(p) for p in paths]
    yield
    after = [_sha(p) for p in paths]
    assert after == before, (
        "a live file changed under a --mock stage-0 probe run: "
        f"{list(zip(paths, before, after))}")


@pytest.fixture
def stage0_fixture_matrix(tmp_path):
    """A minimal, fast, network-free matrix that still gates against the
    REAL glm-4.7 x claude-code registry row -- the serving block below is
    copied verbatim from runs-glm-stage0.yaml / runs-glm-stage1.yaml, and
    the task name deliberately does not start with t<digit>, so
    resolve_timeout_s falls through to timeout_default_s instead of
    requiring a tier-specific key. Graded by `exit 0`, solved by `git
    apply`, same trick as test_corpus_pinning.py's fabricated_matrix: this
    proves stage0_probe's OWN plumbing, not the real t3-a task's runtime
    (that would need network for pip install; see runs-glm-stage0.yaml's
    task-choice comment for why t3-a is still the right choice for a REAL
    run).
    """
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "fixture-task"
    base = task_dir / "base"
    base.mkdir(parents=True)
    (base / "README.md").write_text("solve it\n", encoding="utf-8")
    (task_dir / "PROMPT.md").write_text("append 42\n", encoding="utf-8")
    (task_dir / "verify.sh").write_text("#!/usr/bin/env bash\nexit 0\n",
                                        encoding="utf-8")
    os.chmod(task_dir / "verify.sh", 0o755)

    env = dict(os.environ)
    subprocess.run(["git", "init", "-q"], cwd=base, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=base, env=env, check=True)
    subprocess.run(["git", "-c", "user.email=g@g", "-c", "user.name=gauntlet",
                    "commit", "-q", "-m", "base"], cwd=base, env=env, check=True)
    (base / "README.md").write_text("solve it\n42\n", encoding="utf-8")
    diff = subprocess.run(["git", "diff"], cwd=base, env=env,
                          capture_output=True, text=True, check=True).stdout
    shutil.rmtree(base / ".git")
    (base / "README.md").write_text("solve it\n", encoding="utf-8")
    (task_dir / "solution.patch").write_text(diff, encoding="utf-8")

    config = tmp_path / "runs-stage0-fixture.yaml"
    config.write_text(
        "serving:\n"
        "  parallel: 1\n"
        "  context_length: 131072\n"
        "  max_tokens: 8192\n"
        "  temperature: 0\n"
        "  seed: 42\n"
        "defaults:\n"
        "  timeout_default_s: 60\n"
        "  seed: 1337\n"
        "  k_acceptance: 20\n"
        "sweeps:\n"
        "  - name: glm-stage0\n"
        "    driver: claude-code\n"
        "    harness: false\n"
        "    reps: [1, 2, 3, 4, 5]\n"
        "    tasks: [fixture-task]\n"
        "    configs:\n"
        "      - {model: glm-4.7-local, effort: high}\n",
        encoding="utf-8")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return {"config": str(config), "tasks_dir": str(tasks_dir),
            "scratch": str(scratch)}


def test_end_to_end_mock_run_tags_five_rows_but_refuses_to_record(
        live_files_untouched, stage0_fixture_matrix, tmp_path, monkeypatch):
    """A --mock sweep proves the DISPATCH+TAGGING plumbing (run.py invoked,
    5 rows written, all tagged stage: 0) but can NEVER reach a clean
    recording: every mock row's exit_reason is 'mock', its own excluded
    run_status class (never 'ok'), so 0 of 5 rows are SCORED and
    finalize_stage0 raises before touching the registry. This is deliberate
    -- a verifier caught the first cut of this file calling
    record_noise_probe on mock rows and writing deterministic_loops: true
    from zero real evidence. The CLI turns that refusal into exit 2
    (could-not-determine), prints nothing that looks like a result, and
    the registry copy is provably untouched.
    """
    results = tmp_path / "results.jsonl"
    registry_copy = tmp_path / "models.yaml"
    shutil.copy2(REAL_REGISTRY, registry_copy)
    before_registry_copy = registry_copy.read_text(encoding="utf-8")

    monkeypatch.chdir(RUNNER_DIR)
    proc = subprocess.run(
        [sys.executable, "stage0_probe.py",
         "--config", stage0_fixture_matrix["config"],
         "--tasks-dir", stage0_fixture_matrix["tasks_dir"],
         "--scratch", stage0_fixture_matrix["scratch"],
         "--results", str(results),
         "--registry-path", str(registry_copy),
         "--date", "2026-08-25",
         "--mock"],
        cwd=RUNNER_DIR, capture_output=True, text=True)

    assert proc.returncode == stage0_probe.CANNOT_DETERMINE_EXIT, \
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "0 of 5" in proc.stderr
    assert "mock" in proc.stderr.lower()
    assert "Stage 0 noise probe" not in proc.stdout, (
        "no comment may print for a refused probe")

    # The plumbing before the refusal still ran for real: run.py dispatched,
    # 5 rows landed, every one tagged stage: 0.
    assert results.exists(), "stage0_probe did not write a results file"
    rows = [json.loads(l) for l in results.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert len(rows) == 5, f"expected 5 rows, found {len(rows)}"
    assert all(r["stage"] == 0 for r in rows), "not every row was tagged stage: 0"
    assert all(r["sweep"] == "glm-stage0" for r in rows)
    assert all(r["exit_reason"] == "mock" for r in rows)

    # --mock skips the live preflight and says so.
    assert "preflight: skipped" in proc.stdout

    # The scratch registry COPY, and the REAL live registry/results/usage,
    # never moved at all (live_files_untouched asserts the latter on
    # teardown).
    after_registry_copy = registry_copy.read_text(encoding="utf-8")
    assert after_registry_copy == before_registry_copy


def test_mock_against_the_live_registry_path_is_refused(
        live_files_untouched, stage0_fixture_matrix, tmp_path, monkeypatch):
    """Same posture as run.py's own corpus_guard: a --mock probe must name a
    scratch --registry-path, or it is refused before anything runs, rather
    than silently recording a fake noise probe (derived from mock rows,
    which are not measurements of anything) onto the live model registry."""
    results = tmp_path / "results.jsonl"
    monkeypatch.chdir(RUNNER_DIR)
    proc = subprocess.run(
        [sys.executable, "stage0_probe.py",
         "--config", stage0_fixture_matrix["config"],
         "--tasks-dir", stage0_fixture_matrix["tasks_dir"],
         "--scratch", stage0_fixture_matrix["scratch"],
         "--results", str(results),
         "--mock"],
        cwd=RUNNER_DIR, capture_output=True, text=True)
    assert proc.returncode == corpus_guard.REFUSE_EXIT, proc.stderr
    assert "refus" in proc.stderr.lower()
    assert not results.exists(), "run.py must never have been invoked"


def test_non_mock_preflight_mismatch_refuses_before_run_py_is_invoked(
        live_files_untouched, stage0_fixture_matrix, tmp_path, monkeypatch):
    """The gap a verifier named: run.py's own dispatch gate only checks the
    DECLARED config against the row, never the live server. Here, a
    non-mock invocation with --preflight-lms-output pointed at the captured
    mismatch fixture must refuse (exit 3, cmd_preflight's own
    EXIT_PREFLIGHT_MISMATCH) before run.py is ever invoked -- proven the
    same way corpus_guard's own refusal tests are, by asserting nothing
    downstream ran."""
    results = tmp_path / "results.jsonl"
    registry_copy = tmp_path / "models.yaml"
    shutil.copy2(REAL_REGISTRY, registry_copy)

    monkeypatch.chdir(RUNNER_DIR)
    proc = subprocess.run(
        [sys.executable, "stage0_probe.py",
         "--config", stage0_fixture_matrix["config"],
         "--tasks-dir", stage0_fixture_matrix["tasks_dir"],
         "--scratch", stage0_fixture_matrix["scratch"],
         "--results", str(results),
         "--registry-path", str(registry_copy),
         "--preflight-lms-output", LMS_LIVE_MISMATCH],
        cwd=RUNNER_DIR, capture_output=True, text=True)
    assert proc.returncode == sr.EXIT_PREFLIGHT_MISMATCH, \
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert not results.exists(), "run.py must never have been invoked"
    assert "MISMATCH" in proc.stdout

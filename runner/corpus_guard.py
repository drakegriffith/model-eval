#!/usr/bin/env python3
"""corpus_guard.py -- the one refusal both run.py and judge.py call through
(issues #23 and #24).

Issue #24 says "any --mock or demo run"; judge.py has its own --mock flag and
its own --out/--usage defaults pointed at the same live results/ directory, so
the guard belongs to neither caller and lives here instead, imported by both
(a copy in each file is the same defect class ticket 30's import-cycle note
warns about: two copies drift, and a fix to one is not a fix).

PATH IDENTITY IS INODE IDENTITY, NOT STRING EQUALITY. A verifier reproduced
three string-equality bypasses live against the first cut of this guard: a
symlinked directory, a case-only variant on APFS (case-insensitive by
default), and a doubled leading slash. All three name the same file while
comparing unequal as strings.

Two independent checks, either sufficient. First, os.path.realpath +
os.path.normcase on both sides before the string compare: realpath resolves
symlinks, `..`, and repeated slashes, which is what catches the symlink and
the doubled-slash bypass. normcase is carried for portability (it folds case
on Windows) but IS A NO-OP ON POSIX -- CPython never case-folds a POSIX path,
because the filesystem underneath a POSIX path is not reliably
case-insensitive the way NTFS is, so the string check alone does not catch
the APFS case-variant bypass on this machine. Second, when the candidate
already exists, os.path.samefile: it stats both paths and compares
st_dev/st_ino, which is filesystem-truth rather than a guess about the
filesystem's case sensitivity, and is what actually closes the case-variant
bypass here -- os.path.exists() already resolves a case-insensitive lookup to
the real inode, samefile just compares the two inodes it finds. samefile is
also the only one of these checks a hardlink cannot evade: two hardlinked
paths can be two different, non-symlink strings that no amount of
realpath/normcase makes equal.

REFUSE_EXIT is 3, not 2. This repo's own convention already spends exit 2 on
"config rejected" (run.py's bad-run-matrix checks), and the wave's harness
separately reads exit 2 as "could not inspect" -- a refusal is neither of
those, it is this guard's own decision, and it gets a code nothing else here
emits so a caller can tell the three apart.
"""
import os

REFUSE_EXIT = 3


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def is_live_path(candidate, live_path):
    """True if `candidate` and `live_path` name the same file.

    Two independent tests, either sufficient: normalized string identity
    (catches symlinks and doubled slashes, both resolved away by realpath;
    catches case variants only on a platform where normcase actually folds
    case, i.e. not POSIX), and, when both sides already exist on disk,
    os.path.samefile (catches a case-insensitive-filesystem variant AND a
    hardlink, neither of which the string test alone can see -- see module
    docstring).
    """
    if _norm(candidate) == _norm(live_path):
        return True
    if os.path.exists(candidate) and os.path.exists(live_path):
        try:
            return os.path.samefile(candidate, live_path)
        except OSError:
            return False
    return False


def _nearest_existing_ancestor(start):
    """The deepest directory in `start`'s own ancestor chain (including
    `start` itself) that actually exists on disk. `start` must already be
    absolute -- callers pass either os.path.abspath or os.path.realpath of
    the real candidate, deliberately not computed here (see
    `_case_insensitive_ancestor_match`, which needs both).

    A scratch path's leaf need not exist yet -- `is_inside_or_same` runs
    BEFORE the first checkout is created -- but some ancestor always does
    (the filesystem root, at worst). os.path.exists's own directory-entry
    lookup is case-insensitive on a case-insensitive filesystem (APFS):
    walking up with it, rather than string-splitting `start`, is what lets
    the caller find 'RESULTS' exists when only 'results' was ever created.
    """
    cur = start
    while not os.path.exists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return cur


def _walk_to_root_for_match(start, container):
    """True if `container` names the same file as `start`'s nearest
    existing ancestor, or as any of THAT ancestor's own ancestors up to the
    filesystem root -- checked with os.path.samefile at every step, never a
    string compare. `container` is assumed to already exist (checked once
    by the caller)."""
    cur = _nearest_existing_ancestor(start)
    while True:
        try:
            if os.path.samefile(cur, container):
                return True
        except OSError:
            pass
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _case_insensitive_ancestor_match(candidate, container):
    """True if `container` contains, or is, `candidate` by filesystem
    identity rather than by string -- checked along TWO independent
    starting points, either sufficient: os.path.abspath(candidate) (never
    dereferences a symlink `candidate` itself might be) and
    os.path.realpath(candidate) (dereferences every symlink hop, however
    many, but never folds case -- see module docstring). A verifier
    reproduced a bypass composed of both gaps at once: `candidate` a
    symlink whose target names an existing results subdirectory by a case
    variant (e.g. a symlink to '.../RESULTS/stage0-work' when only
    '.../results/stage0-work' was ever created). The abspath walk never
    resolves the symlink, so it starts from the symlink's own path (which
    "exists" by following the link, but names it, not its target's real
    ancestors) and never reaches the results directory while climbing. The
    realpath walk starts from the dereferenced target -- still
    case-variant, but now walkable by os.path.exists's own case-insensitive
    lookup -- and finds the live results directory partway up. Neither walk
    alone is redundant: the realpath walk is what a plain (non-symlink)
    case-variant path already passed through unchanged, so dropping the
    abspath walk in favor of realpath-only was considered and rejected --
    it would just move the blind spot rather than close it.

    False (never raises) if `container` does not exist -- this guard's own
    callers always pass an existing live results directory, but a helper
    that stats an argument no caller can guarantee stays defensive anyway.
    """
    if not os.path.exists(container):
        return False
    return (_walk_to_root_for_match(os.path.abspath(candidate), container)
            or _walk_to_root_for_match(os.path.realpath(candidate), container))


def is_inside_or_same(candidate, container):
    """True if `candidate` resolves to `container` itself or to a path
    nested inside it.

    Two independent checks, either sufficient -- same posture as
    `is_live_path` above, extended from a single pair to a whole ancestor
    chain. First, resolved PATH COMPONENTS (os.path.commonpath) over both
    sides run through `_norm` (realpath + normcase): this resolves a
    symlink hop, a doubled slash, or a relative path resolved against a
    different cwd, and is deliberately NOT a string-prefix test --
    "/a/results2".startswith("/a/results") is True, but results2 does not
    live inside results, and a caller must still be able to name a scratch
    dir next to the live results directory. Second,
    `_case_insensitive_ancestor_match`: normcase is a no-op on POSIX (see
    module docstring), so on a case-insensitive filesystem (APFS) a
    candidate like 'RESULTS/WORK' shares no resolved string and no path
    component with 'results' even though the filesystem itself treats
    'RESULTS' as the very same directory -- a verifier reproduced exactly
    this as a live bypass of the first cut of this function, which carried
    only the commonpath check. The candidate's leaf need not exist for
    this to fire (it runs before the scratch dir is created); the ancestor
    check climbs to whatever nearest directory does exist and asks the
    filesystem, via samefile, rather than the string compare the first
    check already is -- and itself climbs from two independent starting
    points (abspath and realpath of `candidate`) because a SECOND verifier
    pass composed both gaps: a candidate that is itself a symlink to a
    case-variant path is caught by neither commonpath (case) nor an
    abspath-only ancestor walk (symlink) alone (see
    `_case_insensitive_ancestor_match`'s own docstring).
    """
    cand = _norm(candidate)
    cont = _norm(container)
    if cand == cont:
        return True
    try:
        if os.path.commonpath([cand, cont]) == cont:
            return True
    except ValueError:
        # No common root (e.g. different drives on Windows) -- never nested
        # by this check; the case-insensitive check below still runs.
        pass
    return _case_insensitive_ancestor_match(candidate, container)


def refuse_scratch_inside_results(scratch, results_dir):
    """None if `scratch` resolves outside `results_dir`; otherwise a
    ready-to-print refusal string naming both resolved paths (issue #28).

    Distinct from `refusal_message` above: that guard catches a --results/
    --usage/--registry-path argument that resolves TO a live corpus file.
    This one catches a --scratch argument that resolves INTO the live
    results directory -- a run checkout left there pollutes the corpus
    directory even though it is not itself a corpus write, so those row-
    level guards report the corpus untouched while runner/results/ fills up
    with git checkouts. Must be called, and must refuse, before the first
    scratch directory is created; it does not depend on --mock/--dry-run,
    because a live dispatch with a bad --scratch pollutes the same
    directory just as surely as a mock one does.
    """
    if is_inside_or_same(scratch, results_dir):
        return (
            f"refusing: --scratch {os.path.realpath(scratch)!r} resolves "
            f"inside the live results directory "
            f"{os.path.realpath(results_dir)!r}. Pass --scratch a path "
            f"outside the results directory, e.g. --scratch /tmp/scratch/work.")
    return None


def refusal_message(live_pairs, flag_hint):
    """None if no (candidate, live_path) pair in `live_pairs` collides;
    otherwise a ready-to-print refusal string naming the live path it found.

    `live_pairs` is an iterable of (candidate, live_path, label) so the
    message can say WHICH file it caught (results vs. usage vs. judgments),
    not just that something did. `flag_hint` is the caller's own complete
    sentence fragment naming which flag(s) to redirect and where -- callers
    differ (run.py's --results/--usage vs. judge.py's --out/--usage), so the
    hint is theirs to phrase, not templated here.
    """
    for candidate, live_path, label in live_pairs:
        if candidate and is_live_path(candidate, live_path):
            return (
                f"refusing: a mock/demo/dry-run invocation would write to "
                f"the live {label} ({live_path!r}). Pass {flag_hint}.")
    return None

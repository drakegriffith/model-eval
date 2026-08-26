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

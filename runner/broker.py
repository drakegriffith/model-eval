#!/usr/bin/env python3
"""broker.py — the runner-owned acceptance-feedback broker (ticket 17 section 5).

Ticket 17 adopted A+B: the canonical acceptance suite is unreachable from the
model's workspace, and the model buys feedback from a runner-owned broker,
capped at K = 10 requests per run.

WHY A SEPARATE PROCESS OWNS THE COUNTER, and not a line inside verify.sh.
Ticket 17 section 1 measured it: 37 of 60 Codex runs reached the acceptance
suite without going through verify.sh at all -- they ran `npm test` / `pytest`
directly, with no adversarial intent, because tasks/*/verify.sh is a six-line
wrapper over the same command. A counter living in a file the model owns is
bypassed by a majority of honest runs before anyone tries to cheat. And it
cannot be moved to a read-only file either: self-check runs at the model's own
uid inside the model's own sandbox, so anything the client can increment the
model can rewrite. The counter therefore has to live in a process the model
is not inside. That process is this one -- run.py's own thread, holding the
count in memory, reachable only through a unix socket.

WHAT THE MODEL GETS BACK: integers and one verdict word. The response is
BUILT, never forwarded -- raw suite output is parsed to counts inside this
module and the string is dropped. That is a structural guarantee rather than a
scrubbing pass: there is no code path by which a test name, a file path or an
assertion message reaches the socket. Ticket 05's t4-* tasks were authored
against count-only reporting; this is what makes it true for every task and
every CLI family rather than for whichever script remembered to pass --tb=no.

WHAT COUNTS AGAINST K: one request, one unit, whatever the outcome -- including
a request whose grade errored or timed out. Counting on receipt rather than on
success is deliberate: counting successes only would leave a free probe channel
(ask, crash the grade, learn the tree still crashes, repeat). The runner's own
final grade never comes through here at all (run_verify calls the grade
function directly), so it cannot inflate the count -- asserted in
test_grader_tamper.py::test_authoritative_grade_does_not_execute_the_models_copy_of_verify_sh.

FAMILY NEUTRALITY, which is the reason this closes pre-registration precondition
3 rather than triggering its fallback. The count is taken server-side, at the
socket, from whichever process connected. It does not read CLI telemetry, so it
needs no --output-format stream-json on claude/kimi and it does not repeat the
`turns` mistake (ticket 17 section 3 of the 16-session appendix: structurally 1
on all 148 Codex rows -- two quantities wearing one column name). A brokered
request is the same event on every binary in the roster.

FAIL-CLOSED, matching the sandbox seal's posture (ticket 16: no sandbox-exec,
no run). A broker that cannot start raises, before a token is spent. A broker
that faults mid-run terminates the model and marks the row, because an
UNCOUNTED run is unusable under the pre-registration -- pi is defined at "at
most K brokered requests" and a run whose requests were not counted has no
defensible K. Silently proceeding uncounted would put a row in the corpus that
looks capped and is not.
"""
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading

# Ticket 17 section 6: K = 10 model-visible acceptance-suite executions, on one
# surviving defence -- twice the observed maximum of 5 (n=60). The three other
# defences offered during that session (distribution-free tolerance bound,
# geometric fit, effort headroom) are struck and recorded as struck; do not
# revive them here. Ticket 17's one-shot revision-by-formula (K' = min(20, 2M))
# is retired by this commit, not by A1 -- pre-registration amendment A1
# (docs/studio-handoff/prompt-2-run-experiment.md at a0cef36, registered
# 2026-08-25) never mentions ticket 17 or that formula; it fixes K=20 for
# stage 0/1 outright, and its only revision path is the >= 10-request flip.
# K_DEFAULT stays 10 as resolve_k(None)'s value
# (tests/test_acceptance_broker.py:488); runs-glm-stage0.yaml:60 and
# runs-glm-stage1.yaml:83 set k_acceptance: 20 explicitly, so stage 0/1
# never actually run at this default.
K_DEFAULT = 10
# The revision ceiling, enforced here so a config typo cannot quietly exceed the
# pre-registered bound. "The ceiling of 20 exists so the revision cannot eat the
# budget it was meant to protect."
K_CEILING = 20

# Sockets have a ~104-byte path limit on macOS; /var/folders temp roots are long
# enough to make that a live risk, so the broker's directory is short by design.
_TMP_ROOT = "/tmp"

# Test-runner summary lines, the only thing read out of the grade's output.
# Covers pytest ("2 failed, 7 passed in 0.31s", "1 error"), vitest and jest
# ("Tests  2 failed | 7 passed (9)"). Anything unmatched yields no counts, which
# is reported as counts-unavailable rather than guessed at.
_COUNT_RE = re.compile(
    r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed|todo)\b",
    re.IGNORECASE)

_COUNT_KEYS = ("passed", "failed", "errors", "skipped", "xfailed", "xpassed", "todo")


def resolve_k(value):
    """Validate a configured K against the pre-registered ceiling. Fail closed.

    A K above 20 is not a tuning choice, it is a protocol violation: the
    pre-registration fixed the revision ceiling in a sealed document, and a
    sweep launched at K=50 would produce rows that cannot be reported under it.
    Cheaper to reject the config than to discover it at analysis time.
    """
    k = K_DEFAULT if value is None else int(value)
    if k < 1:
        raise ValueError(f"K must be at least 1, got {k}")
    if k > K_CEILING:
        raise ValueError(
            f"K={k} exceeds the pre-registered ceiling of {K_CEILING} "
            f"(pre-registration amendment A1, docs/studio-handoff/"
            f"prompt-2-run-experiment.md at a0cef36, registered 2026-08-25: "
            f"K=20, cap_exhausted SCORED, stage-0 flip at >= 10 requests)")
    return k


def parse_counts(text):
    """Sum test-outcome counts out of a grade's output. Integers only, no text.

    Summed across every summary line the run produced, because a t4 verify.sh
    runs two suites (visible then hidden) and reporting them separately would
    tell the model which block failed -- structure is information too. The model
    loses nothing it cannot recover honestly: it can run its own visible tests
    locally, unmetered, and subtract.
    """
    totals = {}
    for n, word in _COUNT_RE.findall(text or ""):
        key = word.lower()
        if key == "error":
            key = "errors"
        totals[key] = totals.get(key, 0) + int(n)
    return {k: totals[k] for k in _COUNT_KEYS if k in totals}


def shim_text(sock_path, k, python_bin):
    """The verify.sh the model gets: a client for this broker, nothing else.

    Keeps the filename the prompt names and the twelve tasks already use, so no
    task convention changes and the direct-path habit measured in ticket 17
    section 1 still lands on the chokepoint. The canonical verify.sh is
    untouched in tasks/ and is what the authoritative grade runs (ticket 18's
    overlay puts it back in the grading tree), so this file being model-writable
    costs nothing: rewriting it forfeits the model's own feedback and changes no
    grade.

    Self-contained bash: the socket path is baked in rather than read from the
    environment, so a nested worker in the hybrid arm that inherits a scrubbed
    env still reaches the same broker and the same budget.
    """
    return f'''#!/usr/bin/env bash
# Brokered acceptance feedback (ticket 17 section 5, K={k}).
#
# This is NOT the grader. The authoritative grade is run by the harness after
# your run ends, against the canonical suite, in a tree you cannot reach. This
# script buys you one look at that grade, reported as counts only, and you get
# at most {k} of them. The {k + 1}th request ends the run.
#
# Your own tests in this working copy are unmetered -- run them directly as
# often as you like.
exec {python_bin} - {json.dumps(sock_path)} <<'GAUNTLET_BROKER_CLIENT'
import json, socket, sys

try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1800)
    s.connect(sys.argv[1])
    s.sendall(b'{{"cmd":"check"}}\\n')
    buf = b""
    while not buf.endswith(b"\\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
except OSError as e:
    print("acceptance broker unreachable: %s" % e.__class__.__name__)
    sys.exit(2)

try:
    r = json.loads(buf.decode("utf-8"))
except ValueError:
    print("acceptance broker returned nothing")
    sys.exit(2)

if not r.get("ok"):
    print("acceptance budget exhausted: request %s of %s. This run is over."
          % (r.get("request"), r.get("k")))
    sys.exit(3)

print("acceptance feedback %s/%s (%s remaining)"
      % (r["request"], r["k"], r["remaining"]))
counts = r.get("counts") or {{}}
if counts:
    print("  " + "  ".join("%s=%d" % kv for kv in sorted(counts.items())))
else:
    print("  counts unavailable")
print("  verdict: %s" % r["verdict"])
sys.exit(0 if r["verdict"] == "pass" else 1)
GAUNTLET_BROKER_CLIENT
'''


class Broker:
    """Serves at most k count-only grades over a unix socket, then ends the run.

    One accept loop, one thread: requests are serialised by construction, which
    is what makes the count exact when a hybrid orchestrator and its nested
    workers share a workspace. K governs the WORKSPACE, not the agent -- every
    process that can reach the socket spends from one budget (ticket 17 section
    5's open question about hybrid counting semantics, answered here).
    """

    def __init__(self, scratch, task_dir, k, grade):
        self.scratch = scratch
        self.task_dir = task_dir
        self.k = k
        self._grade = grade
        self.dir = None
        self.sock_path = None
        self._sock = None
        self._thread = None
        self._lock = threading.Lock()
        self._attached = threading.Event()
        self._terminate = None
        self.requests = 0
        self.exhausted = False
        self.failed = None

    # -- lifecycle ---------------------------------------------------------- #
    def start(self):
        """Bind and serve. Raises on failure: no broker, no run (fail closed)."""
        self.dir = tempfile.mkdtemp(prefix="gb-", dir=_TMP_ROOT)
        os.chmod(self.dir, 0o755)
        self.sock_path = os.path.join(self.dir, "s")
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.sock_path)
        self._sock.listen(8)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def close(self):
        with contextlib.suppress(OSError):
            self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        shutil.rmtree(self.dir, ignore_errors=True)

    def attach(self, terminate):
        """Hand the broker the means to end the run, once the model has a pid.

        Separate from start() because the socket has to exist before the model
        is launched and the pid only exists after. A request that arrives in
        that window waits for the attach rather than racing it.
        """
        self._terminate = terminate
        self._attached.set()

    # -- serving ------------------------------------------------------------ #
    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # closed by close(); the run is over
            try:
                self._handle(conn)
            finally:
                with contextlib.suppress(OSError):
                    conn.close()

    def _handle(self, conn):
        with self._lock:
            self.requests += 1
            n = self.requests

        if n > self.k:
            # Hard termination at exhaustion, not post-hoc disqualification --
            # post-hoc burns the tokens the cap exists to stop (ticket 17
            # section 5). The refusal is sent first so the transcript records
            # why the run ended, then the process group goes.
            self.exhausted = True
            self._send(conn, {"ok": False, "reason": "cap_exhausted",
                              "request": n, "k": self.k, "remaining": 0})
            self._end_run()
            return

        try:
            rc, out = self._grade(self.scratch, self.task_dir)
            verdict = "pass" if rc == 0 else "fail"
            counts = parse_counts(out)
        except subprocess.TimeoutExpired:
            # The model's own tree hung the suite. That is an honest answer
            # about its code, not a broker fault, and it is still a request.
            rc, verdict, counts = None, "timeout", {}
        except Exception as e:  # noqa: BLE001 -- any other fault is ours
            self.failed = f"grade_error:{e.__class__.__name__}"
            self._send(conn, {"ok": False, "reason": "broker_failed",
                              "request": n, "k": self.k, "remaining": 0})
            self._end_run()
            return

        # Only integers, one verdict word and the grade's exit status cross this
        # boundary. `out` is dropped here and never leaves the process.
        self._send(conn, {"ok": True, "request": n, "k": self.k,
                          "remaining": self.k - n, "verdict": verdict,
                          "exit": rc, "counts": counts})

    def _send(self, conn, payload):
        with contextlib.suppress(OSError):
            conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))

    def _end_run(self):
        """Kill the model. If nobody ever attached, that is a broker fault."""
        if not self._attached.wait(timeout=30):
            self.failed = self.failed or "terminate_unattached"
            return
        try:
            self._terminate()
        except Exception as e:  # noqa: BLE001
            self.failed = self.failed or f"terminate_error:{e.__class__.__name__}"


@contextlib.contextmanager
def acceptance_broker(scratch, task_dir, k, grade):
    """Run a Broker for the duration of one model invocation."""
    bk = Broker(scratch, task_dir, k, grade).start()
    try:
        yield bk
    finally:
        bk.close()

# Write containment — the enumerated allowlist, and the ruling on rows collected without it

Ticket 26, closed 2026-07-29. Enforced by `sandbox_seal._profile_text`, wired at
`run.py:run_cli`, proven by `tests/test_write_containment.py`.

Before this, `sandbox_seal` emitted `file-read*` rules and nothing else. The seal
run_cli had been applying since ticket 16 denied the model under test *reading*
`tasks/*/solution.patch`, other runs' scratch trees and `results/*.jsonl`, and did
nothing at all to stop it **writing to, appending to, truncating or deleting any of
them**. Ticket 04 is a confidentiality question and is entirely read-side; this is
the integrity question, and no test in the repo had ever made a write-side
assertion. `run.py:append_row` opening the corpus `O_APPEND`+`fsync` is the
runner's own discipline, not containment: it constrains how the runner writes, not
what anything else may do to the file.

## Shape: reads denylist, writes allowlist

The two halves are shaped differently on purpose.

**Reads stay denylist-shaped** (ticket 04/16) — allow by default, name the roots
that must not be read. The set a run legitimately reads is effectively the whole
toolchain, so an allowlist there is not affordable.

**Writes are allowlist-shaped** — `(deny file-write*)` outright, then name the only
places anything may be written. The set a run legitimately writes is small and
knowable, so the default can be inverted: a path nobody thought about is contained
rather than exposed. A write denylist would have to enumerate every path worth
protecting and would silently miss the next one added to the repo.

## May write — the complete allowlist

Run-scoped, passed by `run_cli` as `write_allow_paths`:

| Path | Why |
|---|---|
| the run's scratch tree | the working copy the model solves the task in — the capability containment exists to preserve |
| the staged task mirror (`staged_task_dir`) | `verify.sh` and t3/t4 test assets are resolved through it |
| the broker directory (ticket 17) | the model's only route to acceptance feedback |
| a run-scoped `TMPDIR` | created per run; `TMPDIR`/`TMP`/`TEMP` are redirected to it |

CLI-runtime tier, appended by `sandbox_seal.cli_runtime_write_paths()` so callers
name only what is theirs:

| Path | Why |
|---|---|
| `~/.claude`, `~/.codex` | session state, transcripts, shell snapshots |
| `~/Library/Caches/claude-cli-nodejs` | CLI cache |
| `~/.claude.json` + its lock/temp siblings | rewritten atomically as siblings in `$HOME`; matched by regex prefix, because allowing `$HOME` would allow everything under it |
| `/private/tmp` | hardcoded `/tmp` users that do **not** honour `TMPDIR` — Claude Code's bash wrapper (`/tmp/claude-<id>-cwd`), zsh's own temp files |
| `/dev` | device writes |

None of these hold benchmark state: `ROOT` — the corpus, the canonical tasks,
every scratch tree — is not reachable from any of them, which is what lets this
tier be broad without reopening the defect.

Every entry was established empirically, by running the real `claude` and `codex`
binaries under a candidate profile with macOS denial logging on and adding back
only what a denial actually broke. Guessing this list is not safe: the failure mode
is not a crash, it is a model that quietly loses a tool and scores worse, which
lands in `results.jsonl` as a capability difference rather than as an instrument
fault. The concrete case — with `/private/tmp` denied, the Bash tool still ran the
command but its wrapper could not write `/tmp/claude-<id>-cwd`, so the wrapper
returned 1 for a suite that had passed, and the model reported "exit code 1" for a
green pytest run.

## Must not write — everything else, by default

Named here because they are the consequences the ticket enumerated, each with a
red-arm test that proves the control arm (`GAUNTLET_NO_SANDBOX=1`) really can do it
and a treatment test that proves the sealed arm cannot:

- `runner/results/*.jsonl` — appending a forged row the corpus carries as genuine
  (and which `run.py:existing_ids` admits), or truncating the measurement record
  and ticket 08's token ledger;
- the canonical `tasks/` tree — poisons every subsequent run of that task, not just
  its own;
- a sibling run's scratch tree — contaminates a run in flight;
- the shared temp root (`/var/folders/...`) — deliberately *not* on the allowlist.
  Allowing it instead of the run's own directory would hand every run write access
  to every concurrent run's staged mirror and broker directory: the same
  contamination one level up. Asserted directly against the profile text.

## Recorded on the row

`write_contained` rides beside `sealed` and `tampered`. It is `sealed and
sandbox_seal.WRITE_CONTAINMENT` rather than a literal `True`, so deleting write
containment from the module turns the corpus field false instead of leaving rows
that claim a guarantee nothing is enforcing. Absent-or-false means "not contained"
for any corpus consumer.

## Ruling: are rows collected without write containment still admissible?

**Yes — and the pre-registered confirmatory analysis is unaffected, because none of
its rows have been collected yet.**

The argument is a partition of the 400 result rows in `runner/results/`, audited
2026-07-29:

| Protocol | Rows | `sealed` | `write_contained` |
|---|---|---|---|
| v1 (`brokered` absent) — `sweep1/2a/2b/2c`, `kimi-*`, `t13-*` | 316 | absent | absent |
| v2 (`brokered: true`) — `calib`, `calib-d2`, `negctl28` | 78 | true | absent |
| v2 with containment — `negctl28` (weak arm) | 6 | true | true |

1. **The 316 protocol-v1 rows are already excluded** from the pre-registered
   analysis by ticket 17's rule that `brokered: false` rows never pool with v2.
   Write containment adds no exclusion that is not already in force. That includes
   every row behind the published `deliverables/`, which are reported as protocol-v1
   results and stay reported that way.
2. **No pre-registered confirmatory row exists.** Every v2 row is calibration
   (`calib`, `calib-d2`) or negative control (`negctl28`). `runs.yaml`'s
   `sweep1`/`sweep2*` have not been re-run under v2. The confirmatory sweep will
   therefore be collected under write containment from its first row, and the
   admissibility question never arises for it.
3. **The 78 uncontained v2 rows stay admissible for what they are used for** —
   calibration and instrument development, not hypothesis testing. On positive
   evidence, not on assumption: the canonical `tasks/` tree is clean against git at
   the commit that authored it (a poisoned task file would show as an uncommitted
   diff in a version-controlled tree); no row carries `tampered: true`; no run_id is
   duplicated within a file; and all 400 run_ids are self-consistent with their own
   `sweep`/`model`/`task`/`rep` fields, so no unplanned row is present.

The honest limit, stated rather than buried: (3) is absence of evidence, not
containment. A forgery that matched the run_id schema and left the task tree clean
would not be caught by that audit. It is offered as the reason those specific rows
need not be discarded, not as a substitute for the guarantee — which is exactly why
the field exists on the row, so any consumer can partition on it instead of taking
this ruling's word.

## Known gaps, not closed here

- The seal is **macOS-only** (`sandbox-exec`). No container, VM, chroot, namespace,
  seccomp, firejail or bubblewrap exists in the repo.
- `GAUNTLET_NO_SANDBOX=1` remains a live off-switch. It is loud on stderr and
  recorded on the row as `sealed: false` / `write_contained: false`, which is the
  right design, but it is an off-switch.
- The read side is still denylist-shaped: an absolute path outside the named roots
  is still readable. That is ticket 04.
- The CLI-runtime tier protects the *benchmark*, not the *operator*: `~/.claude` is
  writable, so a model under test could in principle write into the operator's own
  Claude Code config or skills directory. No benchmark state lives there and no
  result could be forged through it, so it is out of scope for this ticket — but it
  is a real hole and it belongs with ticket 04, which already owns `~/.claude` and
  the auth conflict that makes narrowing it awkward.

"""write_probe.py — stand-in for "a model under test deciding to corrupt the
measurement record" (ticket 26).

Sibling of cheat_probe.py, which asks the confidentiality question (what can this
process READ). This one asks the integrity question (what can this process
WRITE), and is launched the same way: by runner/run.py's real run_cli(), in place
of a model CLI, so it sees byte-for-byte the environment, cwd and sandbox profile
a model sees. Whatever this probe can write, a model could write, because the
property under test -- can this process open this path for writing -- is decided
by the kernel and the profile, not by which binary is asking.

Paths are passed in as ABSOLUTE, already resolved by the caller, rather than
discovered by walking up from cwd. That is deliberate: under the ticket-16 read
seal a walk-up fails at the readdir, so a discovery-shaped probe would go green
for a read-side reason and prove nothing about writes. Handing the probe the path
outright models the stronger adversary -- one that already knows where
results.jsonl lives (from its prompt, from a GAUNTLET_NO_SANDBOX=1 run, from the
repo's public README) -- and isolates the write property on its own.

argv[1] is a JSON spec:

    {"checks": [{"name": ..., "kind": ..., "path": ..., ...}, ...]}

Check kinds, each modelling one way a model could corrupt the record:

  append    -- open(path, "a") and write spec["payload"]   (the forged-row attack)
  truncate  -- open(path, "w")                             (destroy the record)
  unlink    -- os.remove(path)                             (destroy it harder)

stdout is a JSON dict {name: {...}}. `wrote` says the operation completed without
an OSError; `err` names the exception class when it did not. A probe-level crash
is reported under its own `err` prefix so it can never be mistaken for a blocked
write -- an integrity test that reads a bug as containment is worse than none.
"""
import json
import os
import sys


def check_append(spec):
    path = spec["path"]
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(spec["payload"])
    except OSError as e:
        return {"wrote": False, "path": path, "err": e.__class__.__name__}
    return {"wrote": True, "path": path}


def check_truncate(spec):
    path = spec["path"]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
    except OSError as e:
        return {"wrote": False, "path": path, "err": e.__class__.__name__}
    return {"wrote": True, "path": path}


def check_unlink(spec):
    path = spec["path"]
    try:
        os.remove(path)
    except OSError as e:
        return {"wrote": False, "path": path, "err": e.__class__.__name__}
    return {"wrote": True, "path": path}


KINDS = {"append": check_append, "truncate": check_truncate, "unlink": check_unlink}

spec = json.loads(sys.argv[1])
report = {}
for check in spec["checks"]:
    try:
        report[check["name"]] = KINDS[check["kind"]](check)
    except Exception as e:  # a crashed check must not look like a blocked one
        report[check["name"]] = {"wrote": False, "path": check.get("path"),
                                 "err": f"probe_error:{e.__class__.__name__}:{e}"}
print(json.dumps(report))

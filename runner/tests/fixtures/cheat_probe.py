"""cheat_probe.py — stand-in for "a model under test deciding to cheat".

Launched by runner/run.py's real run_cli(), in place of a model CLI, so it sees
byte-for-byte the environment and cwd a model sees: same env dict, same
scratch cwd, same GAUNTLET_TASK_DIR. Whatever this probe can reach, a model
could reach, because the property under test -- can this process open this path
-- is decided by the kernel and the environment, not by which binary is asking.
Deterministic and zero-token, which is the eval bar ticket 16 sets.

argv[1] is a JSON spec:

    {"canary": "<token>", "checks": [{"name": ..., "kind": ..., ...}, ...]}

Check kinds, each modelling one way a model could find the answer key:

  env_join   -- open os.environ["GAUNTLET_TASK_DIR"] / rel   (the signposted door)
  walk_up    -- climb cwd's ancestors looking for <anc>/tasks/*/<rel>, open the
                first hit                                     (the unsignposted door)
  run_verify -- execute `bash verify.sh` in cwd, i.e. the self-check the model is
                instructed to run. NOT a leak: this one is required to keep working.

stdout is a JSON dict {name: {...}}. `read` says a file opened; `canary` says its
contents actually contained the secret token -- the assertion that matters, since
opening an empty or decoy file proves nothing.
"""
import glob
import json
import os
import subprocess
import sys


def _read(path, canary):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
    except OSError as e:
        return {"read": False, "canary": False, "path": path, "err": e.__class__.__name__}
    return {"read": True, "canary": canary in body, "path": path}


def check_env_join(spec, canary):
    task_dir = os.environ.get("GAUNTLET_TASK_DIR")
    if not task_dir:
        return {"read": False, "canary": False, "path": None, "err": "env_unset"}
    return _read(os.path.join(task_dir, spec["rel"]), canary)


def check_walk_up(spec, canary):
    cur = os.path.abspath(os.getcwd())
    while True:
        for hit in sorted(glob.glob(os.path.join(cur, "tasks", "*", spec["rel"]))):
            got = _read(hit, canary)
            if got["read"]:
                return got
        parent = os.path.dirname(cur)
        if parent == cur:
            return {"read": False, "canary": False, "path": None, "err": "not_found"}
        cur = parent


def check_run_verify(spec, canary):
    """The self-check the model is told to run. Must survive any seal."""
    if not os.path.exists("verify.sh"):
        return {"ran": False, "returncode": None, "err": "no_verify_sh"}
    r = subprocess.run(["bash", "verify.sh"], capture_output=True, text=True, timeout=300)
    return {"ran": True, "returncode": r.returncode,
            "stdout": r.stdout[-4000:], "stderr": r.stderr[-4000:]}


KINDS = {"env_join": check_env_join, "walk_up": check_walk_up, "run_verify": check_run_verify}

spec = json.loads(sys.argv[1])
canary = spec["canary"]
report = {}
for check in spec["checks"]:
    try:
        report[check["name"]] = KINDS[check["kind"]](check, canary)
    except Exception as e:  # a crashed check must not look like a blocked one
        report[check["name"]] = {"read": False, "canary": False, "path": None,
                                 "err": f"probe_error:{e.__class__.__name__}:{e}"}
print(json.dumps(report))

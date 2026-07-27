"""seal_probe.py — stand-in for "a model choosing to read an absolute path".

Takes a JSON list of absolute paths on argv[1], tries to read each one, and prints
a JSON dict {path: bool} to stdout -- True if the read succeeded (leak), False if
it was denied or the path does not exist. Deterministic and free: the property
under test (can this process reach this path?) is enforced by the kernel via
sandbox-exec, so any process standing in for the model demonstrates it exactly as
well as a real CLI would, at zero token cost.
"""
import json
import sys

paths = json.loads(sys.argv[1])
result = {}
for p in paths:
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            f.read()
        result[p] = True
    except OSError:
        result[p] = False
print(json.dumps(result))

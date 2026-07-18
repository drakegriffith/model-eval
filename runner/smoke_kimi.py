#!/usr/bin/env python3
"""smoke_kimi.py — one tiny real Kimi K3 call through the Codex Moonshot provider.

Confirms the provider wiring (base_url, model id, wire_api, auth) end-to-end and
measures real token usage/cost BEFORE any full task run. The API key is read from
the gitignored secrets file by run.load_kimi_key() and injected into the codex
subprocess env only — it is never printed here.

Usage:  python3 runner/smoke_kimi.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import (  # noqa: E402
    build_cli_cmd, load_kimi_key, parse_usage, kimi_dollars, MOONSHOT_ANTHROPIC_URL,
)

key = load_kimi_key()
if not key:
    print("FAIL: MOONSHOT_API_KEY not found in secrets file")
    sys.exit(1)
print(f"key loaded: yes (len={len(key)}, prefix={key[:3]}…)")  # never print the key itself

env = dict(os.environ)
env.pop("OPENAI_API_KEY", None)
env["ANTHROPIC_BASE_URL"] = MOONSHOT_ANTHROPIC_URL
env["ANTHROPIC_API_KEY"] = key
env["ANTHROPIC_AUTH_TOKEN"] = key

cmd = build_cli_cmd("kimi", "max", "Reply with exactly the word OK and nothing else.")
print("cmd:", " ".join(c if not c.startswith("Reply") else '"<prompt>"' for c in cmd))

try:
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
except subprocess.TimeoutExpired:
    print("FAIL: timed out after 180s")
    sys.exit(1)

print("returncode:", p.returncode)
print("--- stdout tail ---")
print(p.stdout[-2500:])
if p.returncode != 0:
    print("--- stderr tail ---")
    print(p.stderr[-1500:])

ti, to, turns = parse_usage("kimi", p.stdout)
print(f"\nUSAGE  in={ti}  out={to}  turns={turns}  "
      f"est_cost=${kimi_dollars(ti, to):.4f} (cache-miss rate)")
print("SMOKE_OK" if p.returncode == 0 and (ti or to) else "SMOKE_SUSPECT")

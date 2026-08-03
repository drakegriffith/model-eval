# model-eval

A benchmark instrument that runs frontier models against small, verifiable
coding tasks headlessly, grades them with a sealed and sandboxed harness, and
publishes the raw transcripts and judgments alongside the code that produced
them. See `CONTRIBUTING.md` for how to add a task or a model, and what CI
protects.

## Security model

Runs are unattended: `run.py` invokes each model's CLI with
`--dangerously-skip-permissions` (Claude) or
`--dangerously-bypass-approvals-and-sandbox` (Codex). Those flags exist
because a headless process has no terminal to answer an interactive tool-use
prompt on — without them, a run would just hang. That's a real reduction in
the CLI's own safety net, so this repo puts an OS-level sandbox and a
process-level containment layer underneath every invocation instead:

| What the flag disables | What replaces it | Proven by |
|---|---|---|
| Interactive read/write approval prompts | `sandbox-exec` profile, deny-by-default on reads of secrets/credentials, allowlist-shaped on writes (macOS only — `runner/sandbox_seal.py`) | `runner/tests/test_sandbox_seal.py`, `runner/tests/test_write_containment.py` |
| MCP server access | `--mcp-config '{"mcpServers":{}}' --strict-mcp-config` — an empty, locked server map, not an omitted one | `runner/tests/test_live_mcp_seal.py` |
| Any implicit credential exposure | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` popped from the child's environment before every invocation — every run is subscription-authenticated, never API-key-authenticated | `runner/tests/test_live_mcp_seal.py`, `runner/tests/test_product_executor.py` |
| Unbounded filesystem writes | Writes are contained to the run's own scratch tree; the corpus (`runner/results/`) and other concurrent runs' trees are outside the allowlist | `runner/tests/test_write_containment.py` |

The sandbox is fail-closed: if `sandbox-exec` isn't available, the run does
not happen rather than happening unsealed.

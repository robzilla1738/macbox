# Claude Code notes for macbox

Use this file when working in the macbox repository or when macbox MCP is configured in the parent project.

## Project summary

macbox is a local CLI + MCP server for smoke-testing macOS `.app` / `.pkg` builds in disposable Tart VMs. The CLI is the source of truth. MCP tools delegate to the CLI and must not duplicate VM logic.

## Before you run anything

1. Confirm Tart and template VM exist: `macbox doctor --json` → `"ok": true`
2. Use sandbox names like `macbox-test-001` or MCP-generated `macbox-<hex>`. Never use `macos-sequoia-clean` as a disposable VM name.
3. Prefer MCP tools over shell when testing apps for the user. Do not run raw `tart` commands unless debugging macbox itself.

## Safe agent workflow

```
doctor / status
  → create_sandbox (or macbox start)
  → upload_app / upload_pkg
  → run_app_smoke_test
  → collect_logs / take_screenshot / collect_crashes (if needed)
  → destroy_sandbox
```

Always destroy the sandbox when done.

## MCP config (local)

```json
{
  "mcpServers": {
    "macbox": {
      "command": "/Users/robert/Code/macbox/.venv/bin/python",
      "args": ["/Users/robert/Code/macbox/mcp/macbox_mcp.py"]
    }
  }
}
```

Adjust paths to the user's clone and venv.

## What not to do

- Do not upload `~/.ssh`, `.env`, keychains, or browser profiles
- Do not expose or suggest host shell access through macbox
- Do not destroy `macos-sequoia-clean` or other protected template names
- Do not bypass safety checks to make a test pass
- Do not parse unstructured Tart output; use macbox JSON

## Key paths

| Path | Purpose |
|------|---------|
| `src/macbox/cli.py` | CLI commands |
| `mcp/macbox_mcp.py` | MCP server |
| `src/macbox/tart_backend.py` | Tart wrapper |
| `src/macbox/ssh.py` | Guest SSH/SCP |
| `src/macbox/safety.py` | Upload and VM name validation |
| `~/.macbox/runs/<run_id>/` | Screenshots, logs, crashes |
| `skills/macos-sandbox/SKILL.md` | Portable agent skill |

## Development commands

```bash
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v --ignore=tests/integration
```

## When editing macbox

- All subprocess calls go through `src/macbox/runner.py` (no `shell=True`)
- New MCP tools must validate inputs and call the CLI
- Every CLI command needs `--json` and stable error codes
- Do not add cloud, billing, or remote MCP networking in v1 without an explicit product decision

## Docs for users

- [README.md](README.md) — overview
- [docs/GUIDE.md](docs/GUIDE.md) — full setup and usage

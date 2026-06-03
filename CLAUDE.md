# Claude Code notes for macbox

Use this file when working in the macbox repository or when macbox MCP is configured in the parent project.

## Project summary

macbox is a local CLI and MCP server for smoke-testing macOS `.app` / `.dmg` / `.pkg` builds in disposable Tart VMs. The CLI owns the behavior. MCP tools should call into the CLI instead of duplicating VM logic.

The preferred shape is:
- high-level tools for common smoke/gate/install flows
- guest-control tools for custom interaction inside the VM
- observable/watchable sandbox sessions when the user wants to see the run
- split MCP profiles so routine agents can use a small core surface and opt into power tools
- no host shell escape through MCP

## Before you run anything

1. Confirm Tart and template VM exist: `macbox doctor --json` -> `"ok": true`
2. Use sandbox names like `macbox-test-001` or MCP-generated `macbox-<hex>`. Never use `macos-sequoia-clean` as a disposable VM name.
3. Prefer MCP tools over shell when testing apps for the user. Use `macbox-core` for routine smoke tests and add `macbox-power` only for advanced guest control. Do not run raw `tart` commands unless debugging macbox itself.

## Safe agent workflow

```text
doctor / status
  -> create_sandbox (or macbox start; use display_mode window/vnc if the user wants to watch)
  -> upload_app / upload_dmg / upload_pkg
  -> run_app_smoke_test / install_dmg_guest_app / install_guest_pkg
  -> observe_guest / inspect_ui_tree before custom UI actions
  -> collect_logs / take_screenshot / collect_crashes / get_run_report (if needed)
  -> destroy_sandbox
```

Always destroy the sandbox when done.

When you need custom interaction inside the guest, use:

```text
exec_in_guest
run_applescript_in_guest
run_jxa_in_guest
prepare_agent_workspace
run_script_in_guest
observe_guest
inspect_ui_tree
click_ui_element
open_guest_app
list_guest_windows
list_guest_processes
type_text_in_guest
paste_text_in_guest
send_keys_in_guest
click_in_guest
scroll_in_guest
drag_in_guest
push_file_to_guest
pull_file_from_guest
watch_sandbox
```

Prefer `observe_guest` and `inspect_ui_tree` before coordinate clicks. Use `run_script_in_guest` for long shell, AppleScript, or JXA work so diagnostics are saved under `~/.macbox/runs/<run_id>/diagnostics/`. `watch_sandbox(open_viewer=true)` may only open the fixed VNC URL returned by macbox.

Warm-loop shortcut:

```bash
macbox warm --image macos-sequoia-clean --name macbox-warm-sequoia --json
macbox run-on-warm --name macbox-warm-sequoia --app ./dist/MyApp.app --json
macbox reset-warm --image macos-sequoia-clean --name macbox-warm-sequoia --json
```

## One-command demo

For a full local demo without MCP:

```bash
macbox demo --app /Applications/Amphetamine.app --image macos-sequoia-clean --json
./scripts/demo.sh
```

Cursor MCP setup: [docs/CURSOR.md](docs/CURSOR.md).

## MCP config (local)

Use the core server by default:

```json
{
  "mcpServers": {
    "macbox-core": {
      "command": "/Users/robert/Code/macbox/.venv/bin/python",
      "args": ["/Users/robert/Code/macbox/mcp/macbox_core_mcp.py"]
    }
  }
}
```

Add the power server only when the agent needs advanced guest control:

```json
{
  "mcpServers": {
    "macbox-core": {
      "command": "/Users/robert/Code/macbox/.venv/bin/python",
      "args": ["/Users/robert/Code/macbox/mcp/macbox_core_mcp.py"]
    },
    "macbox-power": {
      "command": "/Users/robert/Code/macbox/.venv/bin/python",
      "args": ["/Users/robert/Code/macbox/mcp/macbox_power_mcp.py"]
    }
  }
}
```

Adjust paths to the user's clone and venv. `mcp/macbox_mcp.py` remains the backward-compatible full server and also supports `MACBOX_MCP_PROFILE=core|power|all`.

## What not to do

- Do not upload `~/.ssh`, `.env`, keychains, or browser profiles
- Do not expose or suggest host shell access through macbox
- Do not destroy `macos-sequoia-clean` or other protected template names
- Do not bypass safety checks to make a test pass
- Do not parse unstructured Tart output; use macbox JSON
- Do not skip the structured report when you need a repair loop; `report.json` is where the verdict and next actions are recorded

## Key paths

| Path | Purpose |
|------|---------|
| `src/macbox/cli.py` | CLI commands |
| `mcp/macbox_core_mcp.py` | Core MCP server for routine smoke tests |
| `mcp/macbox_power_mcp.py` | Power MCP server for advanced guest control |
| `mcp/macbox_mcp.py` | Backward-compatible full MCP server |
| `src/macbox/tart_backend.py` | Tart wrapper |
| `src/macbox/ssh.py` | Guest SSH/SCP |
| `src/macbox/safety.py` | Upload and VM name validation |
| `~/.macbox/runs/<run_id>/` | Screenshots, logs, crashes, reports, diagnostics |
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
- Release gates should flow through `macbox gate` / `macbox matrix`, not ad hoc shell scripts
- Do not add cloud, billing, or remote MCP networking in v1 without an explicit product decision

## Docs for users

- [README.md](README.md) - overview
- [docs/GUIDE.md](docs/GUIDE.md) - setup and usage
- [docs/CURSOR.md](docs/CURSOR.md) - Cursor MCP config

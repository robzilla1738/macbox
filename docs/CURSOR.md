# Cursor MCP setup for macbox

Add macbox as local MCP servers so Cursor can create sandboxes, upload apps, and run smoke tests through MCP instead of direct Tart commands. Use `macbox-core` by default; add `macbox-power` only when Cursor needs advanced guest control.

## Prerequisites

- macbox installed in a venv
- `macbox doctor --json` returns `"ok": true`
- Template VM `macos-sequoia-clean` ready (see [GUIDE.md](GUIDE.md))

## 1. Open Cursor MCP settings

In Cursor:

1. Open **Settings** (Cmd+,)
2. Go to **Features -> MCP** (or search "MCP" in settings)
3. Click **Add new global MCP server** or edit your MCP config file directly

Cursor stores MCP config in one of:

- Global: `~/.cursor/mcp.json`
- Project: `.cursor/mcp.json` in your repo

Use project config if macbox is part of a specific codebase.

## 2. Add the core macbox server

Replace paths with your clone location:

```json
{
  "mcpServers": {
    "macbox-core": {
      "command": "/Users/you/Code/macbox/.venv/bin/python",
      "args": ["/Users/you/Code/macbox/mcp/macbox_core_mcp.py"]
    }
  }
}
```

Use the venv Python instead of system `python3` so the installed `macbox` and `mcp` packages resolve the same way every time.

For advanced guest control, add the power server next to core:

```json
{
  "mcpServers": {
    "macbox-core": {
      "command": "/Users/you/Code/macbox/.venv/bin/python",
      "args": ["/Users/you/Code/macbox/mcp/macbox_core_mcp.py"]
    },
    "macbox-power": {
      "command": "/Users/you/Code/macbox/.venv/bin/python",
      "args": ["/Users/you/Code/macbox/mcp/macbox_power_mcp.py"]
    }
  }
}
```

`macbox_core_mcp.py` exposes the 17-tool routine smoke-test surface, which keeps client-specific MCP token overhead much lower than loading every tool on every turn. `macbox_power_mcp.py` exposes the remaining advanced guest-control tools. The legacy `macbox_mcp.py` entrypoint still exposes all 47 tools for backward compatibility.

Restart Cursor or reload MCP servers after saving.

## 3. Confirm the server is connected

In Cursor chat, you should see **macbox-core** listed under available MCP tools. Try:

> Use macbox MCP only. Call macbox_status and report whether the host is ready.

Expected: JSON with `"ok": true` and Tart/SSH checks passing.

## 4. Example agent prompt

> Use the macbox MCP server only. Do not run shell commands.
> Create a headless sandbox from macos-sequoia-clean, upload /Applications/Amphetamine.app, run a 60-second smoke test, collect logs and crashes, then destroy the sandbox. Report artifact paths and any errors.

Tools invoked (in order):

1. `macbox_status`
2. `create_sandbox`
3. `upload_app`
4. `run_app_smoke_test`
5. `collect_logs` / `take_screenshot` / `collect_crashes` (optional)
6. `destroy_sandbox`

For more open-ended guest interaction, enable `macbox-power` so Cursor can also use:

- `exec_in_guest`
- `run_applescript_in_guest`
- `run_jxa_in_guest`
- `observe_guest`
- `inspect_ui_tree`
- `click_ui_element`
- `open_guest_app`
- `list_guest_windows`
- `list_guest_processes`

Those tools still stay inside the VM. They do not grant host shell access.

## 5. What the agent can and cannot do

| Allowed | Blocked |
|---------|---------|
| Create/destroy sandboxes | Raw `tart` commands |
| Upload `.app` / `.pkg` | Host shell execution |
| Run guest smoke tests | Upload secrets (`.ssh`, `.env`, etc.) |
| Collect logs/screenshots/crashes | Destroying template VM names |

## 6. Troubleshooting

| Issue | Fix |
|-------|-----|
| MCP server fails to start | Check the venv path; run `pip install -e ".[dev]"` |
| Tools return doctor errors | Complete template VM setup in [GUIDE.md](GUIDE.md) |
| `upload_app` rejected | Path must be `.app` or `.pkg` on the host |
| Agent uses shell anyway | Repeat "use macbox MCP only, no shell" in the prompt and confirm the server is loaded before the run |

## 7. Screenshots (capture these in Cursor)

Save screenshots under `docs/screenshots/` for your repo or docs site:

| File | What to capture |
|------|-----------------|
| `cursor-mcp-settings.png` | Cursor Settings -> MCP with macbox entry visible |
| `cursor-mcp-tools.png` | Chat panel showing macbox tools enabled |
| `cursor-mcp-demo.png` | Agent run completing sandbox create -> destroy |

Example layout after capture:

```text
docs/screenshots/
  cursor-mcp-settings.png
  cursor-mcp-tools.png
  cursor-mcp-demo.png
```

## 8. CLI demo without MCP

For a quick local demo without configuring Cursor:

```bash
./scripts/demo.sh
# or
macbox demo --app /Applications/Amphetamine.app --image macos-sequoia-clean --json
```

See the verification section in [README.md](../README.md).

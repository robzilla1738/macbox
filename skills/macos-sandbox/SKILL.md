---
name: macos-sandbox
description: Use this skill when testing, launching, debugging, smoke-testing, or validating a macOS .app or .pkg in a disposable local macOS VM sandbox.
---

# macOS Sandbox Testing with macbox

Use macbox when you need to validate a macOS app build in a clean local VM without touching the host environment.

## When to use

- Smoke-test a freshly built `.app` or `.pkg`
- Capture launch screenshots, system logs, and crash reports
- Reproduce startup failures in an isolated macOS guest
- Reset or destroy a sandbox after testing

## When not to use

- Do not use macbox for cloud deployment or CI farm orchestration
- Do not upload secrets, credentials, SSH keys, or browser profiles
- Do not expect macbox to bypass Gatekeeper, Keychain, or other security prompts

## Prerequisites

1. Host has Tart installed and a prepared base image (for example `macos-sequoia-clean`)
2. SSH key auth works: `~/.ssh/macbox_id` installed in guest `admin` account
3. MCP server configured locally (stdio only) or CLI available as `macbox`

## Safe workflow

### 1. Check readiness

```bash
macbox doctor --json
macbox status --json
```

Confirm `doctor` reports Tart, ssh/scp, SSH identity, and state directory are OK.

### 2. Create sandbox

Via MCP: `create_sandbox(image="macos-sequoia-clean", headless=True)`

Via CLI:

```bash
macbox start --image macos-sequoia-clean --name macbox-test-001 --headless --json
```

Save the returned `vm` name and `run_id`.

### 3. Upload build artifact

Upload only `.app` or `.pkg` files from explicit local paths.

Via MCP: `upload_app(vm_name="macbox-test-001", app_path="/path/to/MyApp.app")`

Via CLI:

```bash
macbox upload --name macbox-test-001 --path ./dist/MyApp.app --dest /Users/admin/Desktop/MyApp.app --json
```

### 4. Run smoke test

Via MCP: `run_app_smoke_test(vm_name="macbox-test-001", app_name="MyApp.app", timeout_seconds=120)`

Via CLI:

```bash
macbox run-app --name macbox-test-001 --app /Users/admin/Desktop/MyApp.app --timeout 120 --json
```

Inspect JSON `data`:

- `launched`
- `crashed`
- `screenshot`
- `logs`
- `crash_reports`

### 5. Collect additional evidence if needed

```bash
macbox logs --name macbox-test-001 --last 5m --json
macbox screenshot --name macbox-test-001 --json
macbox collect-crashes --name macbox-test-001 --json
```

Or MCP equivalents: `collect_logs`, `take_screenshot`, `collect_crashes`.

### 6. Reset or destroy sandbox

When finished, always clean up:

```bash
macbox destroy --name macbox-test-001 --json
```

Or `reset_sandbox` / `destroy_sandbox` via MCP.

Use `reset` when you need a fresh VM with the same name. Use `destroy` when done entirely.

### 7. Report findings

Summarize for the user:

- Whether the app launched
- Whether a new crash report appeared
- Paths to screenshot, logs, and crash artifacts under `~/.macbox/runs/<run_id>/`
- Likely cause based on crash report names and log excerpts

## Safety rules for agents

- Never upload secret paths (`~/.ssh`, `.env`, keychains, tokens)
- Never attempt host shell commands through macbox MCP tools
- Guest execution is allowed only through macbox guest commands
- Always destroy or reset sandboxes when testing is complete
- Prefer JSON output fields over parsing unstructured logs

## Example MCP config

```json
{
  "mcpServers": {
    "macbox": {
      "command": "/absolute/path/to/macbox/.venv/bin/python",
      "args": ["/absolute/path/to/macbox/mcp/macbox_mcp.py"]
    }
  }
}
```

## Install skill

Copy this file to:

- `.agents/skills/macos-sandbox/SKILL.md`, or
- `~/.agents/skills/macos-sandbox/SKILL.md`

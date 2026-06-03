---
name: macos-sandbox
description: Use this skill when testing, launching, debugging, smoke-testing, validating, or interactively driving a macOS .app, .dmg, or .pkg in a disposable local macOS VM sandbox.
---

# macOS Sandbox and Guest Control with macbox

Use macbox when you need a disposable local macOS VM that an agent can launch, inspect, drive, debug, and clean up without touching the host environment.

## When to use

- Smoke-test a freshly built `.app`, `.dmg`, or `.pkg`
- Capture launch screenshots, system logs, and crash reports
- Reproduce startup failures in an isolated macOS guest
- Drive a real macOS UI with keyboard, mouse, Accessibility, AppleScript, or JXA
- Let the user watch the VM through a native Tart window or VNC session
- Reset or destroy a sandbox after testing

## When not to use

- Do not use macbox for cloud deployment or CI farm orchestration
- Do not upload secrets, credentials, SSH keys, or browser profiles
- Do not expect macbox to bypass Gatekeeper, Keychain, or other security prompts

## Prerequisites

1. Host has Tart installed and a prepared base image (for example `macos-sequoia-clean`)
2. SSH key auth works: `~/.ssh/macbox_id` installed in guest `admin` account
3. MCP server configured locally (stdio only) or CLI available as `macbox`
4. Accessibility-dependent UI tools may require the guest template to grant permission once

## Agent loop

### 1. Check readiness

```bash
macbox doctor --json
macbox status --json
```

Confirm `doctor` reports Tart, ssh/scp, SSH identity, and the state directory as OK.

### 2. Create a sandbox

Default headless sandbox:

Via MCP: `create_sandbox(image="macos-sequoia-clean", display_mode="headless")`

Via CLI:

```bash
macbox start --image macos-sequoia-clean --name macbox-test-001 --display-mode headless --json
```

Watchable sandbox:

```bash
macbox start --image macos-sequoia-clean --name macbox-watch-001 --display-mode vnc --json
macbox watch --name macbox-watch-001 --json
```

Use `display_mode="window"` for Tart's native VM window or `display_mode="vnc"` when the user wants a Screen Sharing URL. Save the returned `vm`, `run_id`, `run_dir`, and `watch`.

### 3. Upload build artifact

Upload release artifacts from explicit local paths.

Via MCP: `upload_app(vm_name="macbox-test-001", app_path="/path/to/MyApp.app")`

Via CLI:

```bash
macbox upload --name macbox-test-001 --path ./dist/MyApp.app --dest /Users/admin/Desktop/MyApp.app --json
```

Use `upload_dmg`, `upload_pkg`, `mount_dmg_image`, `install_dmg_guest_app`, or `install_guest_pkg` for installer flows.

### 4. Smoke or install

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

### 5. Observe before acting

```bash
macbox observe --name macbox-test-001 --json
macbox inspect-ui-tree --name macbox-test-001 --app MyApp --max-depth 3 --json
```

Use MCP equivalents `observe_guest` and `inspect_ui_tree`. Prefer these before coordinate clicks so the next action is grounded in the current screen, frontmost app/window, visible windows, process state, screenshot, and Accessibility tree.

### 6. Act inside the guest

For one-off commands:

- `exec_in_guest`
- `run_applescript_in_guest`
- `run_jxa_in_guest`
- `open_guest_app`
- `type_text_in_guest`
- `send_keys_in_guest`
- `click_in_guest`
- `paste_text_in_guest`
- `scroll_in_guest`
- `drag_in_guest`

For semantic UI work:

- `inspect_ui_tree`
- `click_ui_element`
- `assert_window`
- `assert_app_running`

For multi-step guest work:

- `prepare_agent_workspace`
- `run_script_in_guest`
- `push_file_to_guest`
- `pull_file_from_guest`

Use `run_script_in_guest` for long shell, AppleScript, or JXA instead of cramming long text into `exec_in_guest`; it saves script/stdout/stderr/metadata under the run's `diagnostics/` directory.

Keyboard, mouse, and Accessibility UI automation need the guest template to grant Accessibility permission. If permission is missing, report the error and the template-prep action.

### 7. Collect evidence

```bash
macbox logs --name macbox-test-001 --last 5m --json
macbox screenshot --name macbox-test-001 --json
macbox collect-crashes --name macbox-test-001 --json
macbox report <run_id> --json
```

Or MCP equivalents: `collect_logs`, `take_screenshot`, `collect_crashes`, `get_run_report`.

### 8. Reset or destroy sandbox

When finished, always clean up:

```bash
macbox destroy --name macbox-test-001 --json
```

Or `reset_sandbox` / `destroy_sandbox` via MCP.

Use `reset` when you need a fresh VM with the same name. Use `destroy` when you are done with it.

### 9. Report findings

Summarize for the user:

- `watch` mode, URL/open instructions, and whether it was available
- Whether the app launched
- Whether a new crash report appeared
- Paths to screenshot, logs, and crash artifacts under `~/.macbox/runs/<run_id>/`
- Diagnostics paths from `run_script_in_guest`, if used
- Cleanup state: destroyed, reset, or intentionally left running
- Likely cause based on crash report names and log excerpts

## Safety rules for agents

- Never upload secret paths (`~/.ssh`, `.env`, keychains, tokens)
- Never attempt host shell commands through macbox MCP tools
- Guest execution is allowed only through macbox guest commands
- Keep broad autonomy inside the disposable guest VM
- Use `watch_sandbox(open_viewer=true)` only for the fixed VNC URL returned by macbox
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

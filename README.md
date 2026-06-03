# macbox

macbox is a local macOS app testing sandbox for Apple Silicon Macs.

It lets an IDE agent or a shell workflow upload a real `.app`, `.dmg`, or `.pkg` into a disposable Tart VM, launch it, check the install path, collect screenshots/logs/crash reports, and return structured JSON. Everything runs on your own machine.

macbox is a CLI plus an optional MCP stdio server for Cursor, Claude Code, and other MCP clients. It wraps Tart so agents get structured JSON instead of parsing VM output by hand.

## Why it exists

Testing a macOS build in a clean environment is annoying if you have to manage the VM yourself.

macbox handles that loop for you: create a disposable VM, copy in the artifact, run it, collect evidence, write a report, and clean up.

## What you get

- **`macbox` CLI** with stable `--json` on every command
- **`macbox-mcp`** stdio server with narrow tools (no host shell)
- **Run artifacts** under `~/.macbox/runs/<run_id>/`
- **Structured reports** via `macbox report <run_id>` for agent repair loops
- **Release gates** for `.app`, `.dmg`, and `.pkg` artifacts
- **Matrix testing** and **warm VM** flows for repeat validation
- **Composable guest-control tools** for app launch, guest shell, AppleScript, windows, and processes
- **Full guest automation** for semantic UI inspection/clicks, keyboard input, paste, mouse clicks, scroll, drag, JXA, and arbitrary file push/pull
- **Watchable sandboxes** with native Tart windows or VNC Screen Sharing URLs
- **Agent workspace scripts** that run inside the guest and save stdout/stderr diagnostics
- **Protected base templates** so `destroy` cannot wipe your base image

## Quick start

Requirements: Apple Silicon Mac, macOS 13+, Tart, OpenSSH.

```bash
git clone https://github.com/robzilla1738/macbox.git
cd macbox
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

brew install cirruslabs/cli/tart
ssh-keygen -t ed25519 -f ~/.ssh/macbox_id -N "" -C "macbox-local"
tart clone ghcr.io/cirruslabs/macos-sequoia-base:latest macos-sequoia-clean
```

Boot the template once, turn on Remote Login in the guest, install your SSH public key, then:

```bash
macbox doctor --json
```

You want `"ok": true`. Full setup is in [docs/GUIDE.md](docs/GUIDE.md).

## Demo in one command

After setup, this runs the standard path end to end:

```bash
macbox demo --app /Applications/Amphetamine.app --image macos-sequoia-clean --json
```

Or use the wrapper script:

```bash
chmod +x scripts/demo.sh
./scripts/demo.sh
./scripts/demo.sh /Applications/Calculator.app
```

This starts a disposable VM, uploads the app, runs a smoke test, saves artifacts under `~/.macbox/runs/`, destroys the VM, and prints the paths as JSON.

## Example: smoke-test an app (step by step)

```bash
macbox start --image macos-sequoia-clean --name macbox-test-001 --display-mode headless --json
macbox upload --name macbox-test-001 --path ./dist/MyApp.app --dest /Users/admin/Desktop/MyApp.app --json
macbox run-app --name macbox-test-001 --app /Users/admin/Desktop/MyApp.app --timeout 120 --json
macbox destroy --name macbox-test-001 --json
```

`run-app` waits, captures a screenshot, pulls recent logs, diff-checks crash reports, writes a structured `report.json`, and returns paths under `~/.macbox/runs/`.

To let the user watch live, start with a display mode:

```bash
macbox start --image macos-sequoia-clean --name macbox-watch-001 --display-mode vnc --json
macbox watch --name macbox-watch-001 --json
```

`display_mode` can be `headless`, `window`, or `vnc`. VNC responses include a `vnc://<guest-ip>` URL for macOS Screen Sharing.

## Structured report

Every smoke test and release gate writes a single structured verdict:

```bash
macbox report 2026-06-02T00-00-00Z-macbox-test-001 --json
```

The report includes launch state, crash state, screenshot/log/crash artifact paths, diagnosis, next actions, and crash summary data when available.

## Release artifacts

macbox supports the artifact types you usually ship or download:

```bash
macbox upload-dmg --name macbox-test-001 --path ./dist/MyApp.dmg --json
macbox mount-dmg --name macbox-test-001 --dmg /Users/admin/Desktop/MyApp.dmg --json
macbox install-dmg-app --name macbox-test-001 --app MyApp.app --json
macbox run-installed-app --name macbox-test-001 --app MyApp.app --json

macbox upload --name macbox-test-001 --path ./dist/MyApp.pkg --json
macbox install-pkg --name macbox-test-001 --pkg /Users/admin/Desktop/MyApp.pkg --app MyApp.app --json
```

`install-pkg` records installer exit code, new apps in `/Applications`, LaunchAgents/LaunchDaemons, postinstall logs, and installed-file manifests when package IDs can be detected.

## Release gate and matrix

Use `gate` when you want a single pass/fail result:

```bash
macbox gate \
  --image macos-sequoia-clean \
  --artifact ./release/MyApp.dmg \
  --app MyApp.app \
  --requirements launch,no-crash,screenshot,no-new-crash-report \
  --json
```

Use `matrix` when you want to run the same artifact against multiple templates:

```bash
macbox matrix \
  --images macos-sequoia-clean,macos-sonoma-clean,macos-ventura-clean \
  --artifact ./release/MyApp.dmg \
  --app MyApp.app \
  --json
```

## Warm VMs and profiles

For faster local loops:

```bash
macbox warm --image macos-sequoia-clean --name macbox-warm-sequoia --json
macbox run-on-warm --name macbox-warm-sequoia --app ./dist/MyApp.app --json
macbox reset-warm --image macos-sequoia-clean --name macbox-warm-sequoia --json
```

Built-in profiles are available through `macbox profiles --json`, including `macos-sequoia-dark-mode`, `macos-sequoia-no-network`, and `macos-sequoia-low-disk`.

## MCP for AI IDEs

If you want an IDE agent to drive the sandbox, point Cursor, Claude Code, or another MCP client at the local server:

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

Tools include `create_sandbox`, `create_warm_sandbox`, `run_on_warm_sandbox`, `upload_app`, `upload_dmg`, `install_guest_pkg`, `run_app_smoke_test`, `run_release_gate`, `run_release_matrix`, `get_run_report`, and the evidence helpers. There is also a guest-control layer: `exec_in_guest`, `run_applescript_in_guest`, `open_guest_app`, `list_guest_windows`, `list_guest_processes`, `observe_guest`, `inspect_ui_tree`, and `click_ui_element`.

For full control inside the VM there is also keyboard and mouse automation (`type_text_in_guest`, `send_keys_in_guest`, `click_in_guest`, `paste_text_in_guest`, `scroll_in_guest`, `drag_in_guest`), a JXA escape hatch (`run_jxa_in_guest`), an agent workspace/script runner (`prepare_agent_workspace`, `run_script_in_guest`), live-watch metadata (`watch_sandbox`), and generic bidirectional file transfer (`push_file_to_guest`, `pull_file_from_guest`).

They call the CLI internally. They do not expose raw Tart or arbitrary host commands. Flexibility stays inside the guest.

Example prompt:

> Use the macbox MCP server to create a sandbox from macos-sequoia-clean, upload my built app, run a smoke test, collect logs and a screenshot, then destroy the sandbox.

When the fixed tools are not enough:

> Use macbox MCP only. Create a sandbox, upload the app, open it with custom arguments, inspect the guest windows, run a guest command, and destroy the sandbox when done.

When the user wants to watch:

> Use macbox MCP with display_mode vnc, report the watch URL, observe the guest before each UI action, and destroy the sandbox when done.

See [docs/GUIDE.md](docs/GUIDE.md) for the full workflow. Cursor setup: [docs/CURSOR.md](docs/CURSOR.md).

## Verified locally

Local template: `macos-sequoia-clean`, SSH key at `~/.ssh/macbox_id`, Tart 2.32.x.

**Third-party upload test: Amphetamine.app (7.4MB)**

```bash
macbox demo --app /Applications/Amphetamine.app --image macos-sequoia-clean --json
```

Observed: `launched: true`, `crashed: false`, screenshot PNG saved, syslog excerpt saved, no new crash reports, sandbox destroyed, base template untouched.

Manual step-by-step run:

```bash
macbox upload --name macbox-realapp-001 --path /Applications/Amphetamine.app \
  --dest /Users/admin/Desktop/Amphetamine.app --json
macbox run-app --name macbox-realapp-001 --app /Users/admin/Desktop/Amphetamine.app --timeout 120 --json
```

**Built-in app: Calculator.app** (guest path, no upload):

```bash
macbox run-app --name macbox-test-001 --app /System/Applications/Calculator.app --timeout 60 --json
```

**MCP end-to-end**: `create_sandbox` -> `upload_app` -> `run_app_smoke_test` -> `destroy_sandbox` with Amphetamine.app. Passed.

## Safety

- Typed artifact uploads (`upload_app` / `upload_dmg` / `upload_pkg`) accept `.app`, `.dmg`, `.pkg` only
- Generic `push_file_to_guest` / `pull_file_from_guest` move any file, but secret paths stay blocked
- `watch_sandbox --open` only opens the fixed VNC URL returned for the running VM
- Secret paths blocked (`~/.ssh`, `.env`, keychains, `*token*`, etc.)
- Guest SSH: key auth only, `BatchMode=yes`
- Base image names are protected from `destroy` / `reset`
- MCP uses stdio only in v1

## Docs

| File | Purpose |
|------|---------|
| [docs/GUIDE.md](docs/GUIDE.md) | Setup, CLI reference, MCP, troubleshooting |
| [docs/CURSOR.md](docs/CURSOR.md) | Cursor MCP config and demo prompts |
| [CLAUDE.md](CLAUDE.md) | Notes for Claude Code |
| [AGENTS.md](AGENTS.md) | Rules for AI agents using macbox |
| [skills/macos-sandbox/SKILL.md](skills/macos-sandbox/SKILL.md) | Agent skill (copy to `~/.agents/skills/`) |

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v --ignore=tests/integration
MACBOX_RUN_INTEGRATION=1 pytest tests/integration  # needs Tart + template VM
```

## Limits

- You prepare the base Tart VM and SSH keys yourself
- Gatekeeper / Keychain prompts inside the guest are not auto-handled
- First `tart clone` downloads a large image (~25GB+)
- Headless screenshots can be blank on some setups; logs and crashes still help

## License

MIT. See [LICENSE](LICENSE).

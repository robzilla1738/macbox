# macbox

**A local, MCP-compatible macOS app testing sandbox.**

An AI IDE can upload a real `.app`, `.dmg`, or `.pkg` into a disposable macOS VM, launch it, validate install flows, and pull back screenshots, logs, crash reports, and a structured verdict — then tear the VM down. Everything runs on your Apple Silicon Mac via [Tart](https://tart.run/). No cloud. No remote workers. No billing.

macbox is a CLI plus an optional MCP stdio server for Cursor, Claude Code, and other MCP clients. It wraps Tart so agents get structured JSON instead of parsing VM output by hand.

## Why it exists

You built a macOS app. You want an agent to verify it launches in a clean environment — without you SSH-ing into a VM, copying files, and remembering to delete it.

macbox automates that loop: disposable VM → upload/install → launch → evidence → verdict → destroy. JSON in, JSON out.

## What you get

- **`macbox` CLI** with stable `--json` on every command
- **`macbox-mcp`** stdio server with narrow tools (no host shell)
- **Run artifacts** under `~/.macbox/runs/<run_id>/`
- **Structured reports** via `macbox report <run_id>` for agent repair loops
- **Release gates** for `.app`, `.dmg`, and `.pkg` artifacts
- **Matrix testing** and **warm VM** flows for repeat validation
- **Protected base templates** so `destroy` cannot wipe your golden image

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

After setup, show the full loop:

```bash
macbox demo --app /Applications/Amphetamine.app --image macos-sequoia-clean --json
```

Or use the wrapper script:

```bash
chmod +x scripts/demo.sh
./scripts/demo.sh
./scripts/demo.sh /Applications/Calculator.app
```

This starts a disposable VM, uploads the app, runs a smoke test, saves artifacts under `~/.macbox/runs/`, destroys the VM, and prints paths in JSON.

## Example: smoke-test an app (step by step)

```bash
macbox start --image macos-sequoia-clean --name macbox-test-001 --headless --json
macbox upload --name macbox-test-001 --path ./dist/MyApp.app --dest /Users/admin/Desktop/MyApp.app --json
macbox run-app --name macbox-test-001 --app /Users/admin/Desktop/MyApp.app --timeout 120 --json
macbox destroy --name macbox-test-001 --json
```

`run-app` waits, captures a screenshot, pulls recent logs, diff-checks crash reports, writes a structured `report.json`, and returns paths under `~/.macbox/runs/`.

## Structured report

Every smoke test and release gate writes a single machine-friendly verdict:

```bash
macbox report 2026-06-02T00-00-00Z-macbox-test-001 --json
```

The report includes launch state, crash state, screenshot/log/crash artifact paths, diagnosis, next actions, and crash summary data when available.

## Release artifacts

macbox now supports the artifact shapes users actually receive:

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

One-shot pass/fail gates are now first-class:

```bash
macbox gate \
  --image macos-sequoia-clean \
  --artifact ./release/MyApp.dmg \
  --app MyApp.app \
  --requirements launch,no-crash,screenshot,no-new-crash-report \
  --json
```

Matrix testing fans the same artifact across multiple templates:

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

This is the main use case: point Cursor, Claude Code, or any MCP client at the local server and let the agent drive the sandbox.

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

Tools include `create_sandbox`, `create_warm_sandbox`, `run_on_warm_sandbox`, `upload_app`, `upload_dmg`, `install_guest_pkg`, `run_app_smoke_test`, `run_release_gate`, `run_release_matrix`, `get_run_report`, and the evidence helpers. They call the CLI internally. They do not expose raw Tart or arbitrary host commands.

Example prompt:

> Use the macbox MCP server to create a sandbox from macos-sequoia-clean, upload my built app, run a smoke test, collect logs and a screenshot, then destroy the sandbox.

See [docs/GUIDE.md](docs/GUIDE.md) for the full workflow. Cursor setup: [docs/CURSOR.md](docs/CURSOR.md).

## Proof (tested on Apple Silicon)

Local template: `macos-sequoia-clean`, SSH key at `~/.ssh/macbox_id`, Tart 2.32.x.

**Third-party upload test — Amphetamine.app (7.4MB)**

```bash
macbox demo --app /Applications/Amphetamine.app --image macos-sequoia-clean --json
```

Observed: `launched: true`, `crashed: false`, screenshot PNG (~3–6MB), syslog excerpt saved, no new crash reports, sandbox destroyed, base template untouched.

Manual step-by-step run (same result):

```bash
macbox upload --name macbox-realapp-001 --path /Applications/Amphetamine.app \
  --dest /Users/admin/Desktop/Amphetamine.app --json
macbox run-app --name macbox-realapp-001 --app /Users/admin/Desktop/Amphetamine.app --timeout 120 --json
```

**Built-in app — Calculator.app** (guest path, no upload):

```bash
macbox run-app --name macbox-test-001 --app /System/Applications/Calculator.app --timeout 60 --json
```

**MCP end-to-end** — `create_sandbox` → `upload_app` → `run_app_smoke_test` → `destroy_sandbox` with Amphetamine.app, no shell. Passed.

## Safety (short version)

- MCP uploads: `.app`, `.dmg`, and `.pkg` only
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

# macbox

Run macOS apps inside disposable local VMs. Collect screenshots, logs, and crash reports. Tear the sandbox down when you're done.

macbox is a CLI plus an optional MCP server for AI coding tools. It wraps [Tart](https://tart.run/) on your Apple Silicon Mac so you can smoke-test a `.app` or `.pkg` in a clean guest without touching your host setup.

This runs on your machine only. No cloud. No remote workers. No billing.

## Why it exists

You built a macOS app. You want to know if it launches, crashes on startup, or spews errors in a fresh environment. Spinning up a VM by hand, copying the build over SSH, grabbing logs, and remembering to delete the VM gets old fast.

macbox automates that loop and speaks JSON so agents do not have to parse Tart output.

## What you get

- **`macbox` CLI** with stable `--json` on every command
- **`macbox-mcp`** stdio server with narrow tools (no host shell)
- **Run artifacts** under `~/.macbox/runs/<run_id>/`
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

## Example: smoke-test an app

```bash
macbox start --image macos-sequoia-clean --name macbox-test-001 --headless --json
macbox upload --name macbox-test-001 --path ./dist/MyApp.app --dest /Users/admin/Desktop/MyApp.app --json
macbox run-app --name macbox-test-001 --app /Users/admin/Desktop/MyApp.app --timeout 120 --json
macbox destroy --name macbox-test-001 --json
```

`run-app` waits, captures a screenshot, pulls recent logs, diff-checks crash reports, and returns paths under `~/.macbox/runs/`.

## MCP for AI IDEs

Point Cursor, Claude Code, or any MCP client at the local server:

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

Tools: `create_sandbox`, `upload_app`, `run_app_smoke_test`, `collect_logs`, `take_screenshot`, `collect_crashes`, `destroy_sandbox`, and a few helpers. They call the CLI internally. They do not expose raw Tart or arbitrary host commands.

Example prompt:

> Use the macbox MCP server to create a sandbox from macos-sequoia-clean, upload my built app, run a smoke test, collect logs and a screenshot, then destroy the sandbox.

See [docs/GUIDE.md](docs/GUIDE.md) for the full workflow.

## Safety (short version)

- MCP uploads: `.app` and `.pkg` only
- Secret paths blocked (`~/.ssh`, `.env`, keychains, `*token*`, etc.)
- Guest SSH: key auth only, `BatchMode=yes`
- Base image names are protected from `destroy` / `reset`
- MCP uses stdio only in v1

## Docs

| File | Purpose |
|------|---------|
| [docs/GUIDE.md](docs/GUIDE.md) | Setup, CLI reference, MCP, troubleshooting |
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

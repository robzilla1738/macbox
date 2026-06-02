# Agent instructions for macbox

Instructions for AI coding agents (Cursor, Claude Code, Codex, etc.) using macbox or working on this repository.

## Purpose

macbox lets you smoke-test macOS applications inside disposable local VMs. You get structured JSON back: launch result, screenshot path, logs, crash reports. The host stays clean.

## When to use macbox

Use macbox when the user wants to:

- Verify a macOS `.app` or `.pkg` launches in a fresh environment
- Capture crash reports or logs after launch
- Test a build without installing it on the host
- Run an isolated macOS check from an IDE via MCP

Do not use macbox for:

- Linux or iOS builds
- Cloud CI farms or multi-tenant hosting
- Reading arbitrary host files
- Bypassing code signing, Gatekeeper, or Keychain

## Prerequisites (user-side)

The user must have completed one-time setup:

- Tart installed
- Template VM `macos-sequoia-clean` (or configured `default_image`)
- SSH key at `~/.ssh/macbox_id` installed in guest `admin` account
- `macbox doctor --json` returns `"ok": true`

If doctor fails, point the user to [docs/GUIDE.md](docs/GUIDE.md). Do not guess at VM credentials in macbox config.

## MCP tools (preferred for app testing)

| Tool | Use |
|------|-----|
| `macbox_status` | Check readiness before starting |
| `create_sandbox` | New disposable VM from template |
| `upload_app` | Copy `.app` bundle to guest |
| `upload_pkg` | Copy `.pkg` to guest |
| `run_app_smoke_test` | Launch + wait + collect evidence |
| `collect_logs` | Syslog excerpt |
| `take_screenshot` | PNG capture |
| `collect_crashes` | DiagnosticReports |
| `destroy_sandbox` | Required cleanup |

`list_images` and `reset_sandbox` exist but most flows only need the table above.

## Standard workflow

1. Call `macbox_status()`. Stop if not ready.
2. `create_sandbox(image="macos-sequoia-clean", headless=True)`
3. `upload_app(vm_name, "/absolute/path/to/App.app")`
4. `run_app_smoke_test(vm_name, "App.app", timeout_seconds=120)`
5. Optionally `collect_logs`, `take_screenshot`, `collect_crashes`
6. `destroy_sandbox(vm_name)` — always, even on failure

Report to the user:

- `ok`, `launched`, `crashed` from smoke test JSON
- Paths under `data.screenshot`, `data.logs`, `data.crash_reports`
- Error codes and messages if anything failed

## Safety rules (required)

- Never upload secret paths: `~/.ssh`, `.gnupg`, `.env`, keychains, browser profiles, files matching `*secret*`, `*token*`, `*credential*`
- MCP only allows `.app` and `.pkg` uploads
- Never run arbitrary host shell commands as a substitute for macbox tools
- Never destroy template VM names (`macos-sequoia-clean`, `default_image`, `protected_images`)
- Guest execution happens only through macbox commands, not ad-hoc SSH from the agent unless debugging setup

## CLI fallback

If MCP is unavailable, the same flow works via shell:

```bash
macbox start --image macos-sequoia-clean --name macbox-test-001 --headless --json
macbox upload --name macbox-test-001 --path ./dist/MyApp.app --dest /Users/admin/Desktop/MyApp.app --json
macbox run-app --name macbox-test-001 --app /Users/admin/Desktop/MyApp.app --timeout 120 --json
macbox destroy --name macbox-test-001 --json
```

Parse JSON responses. Do not scrape Tart stdout.

## Repository layout

```
src/macbox/     CLI and core library
mcp/            MCP server entrypoint
skills/         Agent skill for end users
tests/          Unit + integration tests
docs/GUIDE.md   Human setup guide
```

## Skill file

Install [skills/macos-sandbox/SKILL.md](skills/macos-sandbox/SKILL.md) into the user's agent skills directory for cross-project use.

## Contributing to macbox

- Run `pytest tests/ -v --ignore=tests/integration` before proposing changes
- Keep MCP surface narrow
- Preserve JSON contracts; document new fields in docs/GUIDE.md
- Integration tests: `MACBOX_RUN_INTEGRATION=1 pytest tests/integration`

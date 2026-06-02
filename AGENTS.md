# Agent instructions for macbox

Instructions for coding agents using macbox or working in this repository.

## Purpose

macbox smoke-tests macOS applications inside disposable local VMs. It returns structured JSON with the launch result, screenshot path, logs, crash reports, and a report artifact with the verdict, diagnosis, and next actions. The host stays clean.

## When to use macbox

Use macbox when the user wants to:

- Verify a macOS `.app`, `.dmg`, or `.pkg` launches in a fresh environment
- Capture crash reports or logs after launch
- Test a build without installing it on the host
- Validate installer flows and release gates
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
| `create_warm_sandbox` | Start a reusable warm VM |
| `run_on_warm_sandbox` | Upload a local `.app` to a warm VM and run it |
| `exec_in_guest` | Run a guest shell command inside the VM |
| `run_applescript_in_guest` | Run guest AppleScript for UI automation or inspection |
| `open_guest_app` | Launch a guest app with optional arguments |
| `list_guest_windows` | Inspect current guest window titles |
| `list_guest_processes` | Inspect guest process state |
| `upload_app` | Copy `.app` bundle to guest |
| `upload_dmg` | Copy `.dmg` to guest |
| `upload_pkg` | Copy `.pkg` to guest |
| `mount_dmg_image` | Mount a DMG in guest |
| `install_dmg_guest_app` | Copy an app from a mounted DMG into `/Applications` |
| `install_guest_pkg` | Run installer validation and optionally launch the installed app |
| `run_app_smoke_test` | Launch + wait + collect evidence |
| `run_installed_guest_app` | Launch app from `/Applications` |
| `assert_window` | Verify window title content |
| `assert_app_running` | Verify running bundle ID |
| `collect_logs` | Syslog excerpt |
| `take_screenshot` | PNG capture |
| `collect_crashes` | DiagnosticReports |
| `get_run_report` | Fetch the structured report for a run |
| `run_release_gate` | One-shot pass/fail validation |
| `run_release_matrix` | Fan an artifact across multiple images |
| `reset_warm_sandbox` | Reset a warm VM back to clean state |
| `destroy_sandbox` | Required cleanup |

`list_images`, `list_profiles`, and `reset_sandbox` exist but most flows only need the table above.

## Standard workflow

1. Call `macbox_status()`. Stop if not ready.
2. `create_sandbox(image="macos-sequoia-clean", headless=True)`
3. `upload_app(vm_name, "/absolute/path/to/App.app")`
4. `run_app_smoke_test(vm_name, "App.app", timeout_seconds=120)`
5. Optionally `collect_logs`, `take_screenshot`, `collect_crashes`
6. `destroy_sandbox(vm_name)` - always, even on failure

If the fixed tools are not enough, drop to the guest-control tools before reaching for ad hoc host shell commands. Keep that flexibility inside the VM.

Report to the user:

- `ok`, `launched`, `crashed` from smoke test JSON
- Paths under `data.screenshot`, `data.logs`, `data.crash_reports`
- `data.report` or `get_run_report(run_id)` for the structured summary
- Error codes and messages if anything failed

## Safety rules (required)

- Never upload secret paths: `~/.ssh`, `.gnupg`, `.env`, keychains, browser profiles, files matching `*secret*`, `*token*`, `*credential*`
- MCP only allows `.app`, `.dmg`, and `.pkg` uploads
- Never run arbitrary host shell commands as a substitute for macbox tools
- Prefer guest-control tools over host shell when you need custom interaction
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

For a one-command demo (start -> upload -> smoke -> destroy -> print artifact paths):

```bash
macbox demo --app /Applications/Amphetamine.app --image macos-sequoia-clean --json
./scripts/demo.sh
```

Parse JSON responses. Do not scrape Tart stdout.

For release artifacts, prefer:

```bash
macbox gate --image macos-sequoia-clean --artifact ./release/MyApp.dmg --app MyApp.app --json
macbox matrix --images macos-sequoia-clean,macos-sonoma-clean --artifact ./release/MyApp.dmg --app MyApp.app --json
macbox warm --image macos-sequoia-clean --name macbox-warm-sequoia --json
macbox run-on-warm --name macbox-warm-sequoia --app ./dist/MyApp.app --json
```

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

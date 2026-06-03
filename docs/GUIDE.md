# macbox guide

This is the setup and usage guide for macbox: install it, prepare a template VM, run `.app` / `.dmg` / `.pkg` tests from the CLI or MCP, and read the resulting artifacts and reports.

## What macbox is (and is not)

macbox runs macOS app builds in disposable Tart VMs on your Mac. It is built for local development and AI-assisted smoke tests.

It is not:

- A hosted sandbox service
- Docker for macOS
- A way to bypass Gatekeeper, notarization, or Keychain prompts
- A general-purpose remote shell on your host

## Requirements

| Item | Notes |
|------|-------|
| Hardware | Apple Silicon Mac |
| Host OS | macOS 13 Ventura or later |
| Tart | `brew install cirruslabs/cli/tart` |
| OpenSSH | `ssh` and `scp` on PATH |
| Disk | ~60GB+ free for one template plus sandboxes |
| Python | 3.11+ |

## Install macbox

```bash
git clone https://github.com/robzilla1738/macbox.git
cd macbox
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Verify:

```bash
macbox doctor --json
```

Until the template VM and SSH key exist, `doctor` will fail on `tart` or `ssh_identity`. That is expected during first-time setup.

## One-time template setup

macbox expects a local template named `macos-sequoia-clean` by default. Create it once.

### 1. Install Tart and create an SSH key

```bash
brew install cirruslabs/cli/tart
ssh-keygen -t ed25519 -f ~/.ssh/macbox_id -N "" -C "macbox-local"
chmod 600 ~/.ssh/macbox_id
```

### 2. Clone the base image

```bash
tart clone ghcr.io/cirruslabs/macos-sequoia-base:latest macos-sequoia-clean
```

This downloads a large OCI image. If that is slow on your machine, clone it on another Mac and move it over with `tart export` / `tart import`.

### 3. Boot the template and enable SSH

```bash
tart run macos-sequoia-clean
```

In the guest (GUI):

1. **System Settings -> General -> Sharing -> Remote Login** - on
2. **Users & Groups -> Login Options** - auto-login for `admin`
3. **Lock Screen** - disable password on wake if you can

Tart base images use user `admin` / password `admin` for first login.

### 4. Install your host public key

On the host (one-time; password auth is only for this step):

```bash
brew install cirruslabs/cli/sshpass   # optional helper
IP=$(tart ip macos-sequoia-clean)
sshpass -p admin ssh-copy-id -i ~/.ssh/macbox_id.pub -o StrictHostKeyChecking=accept-new admin@$IP
```

Verify key-only auth:

```bash
ssh -i ~/.ssh/macbox_id -o BatchMode=yes admin@$(tart ip macos-sequoia-clean) true
echo $?   # should be 0
```

Stop the template when done:

```bash
tart stop macos-sequoia-clean
```

### 5. Confirm macbox is ready

```bash
macbox doctor --json
```

All checks should pass. `"ok": true` means you are ready to spawn sandboxes.

## One-command demo

```bash
macbox demo --app /Applications/Amphetamine.app --image macos-sequoia-clean --json
```

Or:

```bash
./scripts/demo.sh
./scripts/demo.sh /path/to/YourApp.app
```

This runs start, upload, smoke test, and destroy in one shot. It prints artifact paths as JSON.

Cursor MCP setup: [CURSOR.md](CURSOR.md).

## Config

macbox writes `~/.macbox/config.json` on first run. Defaults:

```json
{
  "state_dir": "~/.macbox",
  "guest_user": "admin",
  "ssh_identity_file": "~/.ssh/macbox_id",
  "default_image": "macos-sequoia-clean",
  "protected_images": ["macos-sequoia-clean"],
  "run_app_timeout_seconds": 120,
  "log_collect_duration": "5m",
  "profiles": {}
}
```

`protected_images` always includes `default_image`. macbox refuses to `destroy` or `reset` those names so you do not delete your template by accident.

Override state dir for tests:

```bash
export MACBOX_STATE_DIR=/tmp/macbox-test
```

You can add named profiles under `profiles` if your local Tart template names differ from the built-ins.

## CLI workflow

Every command accepts `--json`. Failures still emit JSON when possible.

### Check status

```bash
macbox status --json
macbox images --json
```

Use the **local** template name (`macos-sequoia-clean`), not the `ghcr.io/...` cache entries in `tart list`.

### Start a disposable sandbox

```bash
macbox start \
  --image macos-sequoia-clean \
  --name macbox-test-001 \
  --headless \
  --json
```

Rules:

- Sandbox name must differ from the base image name
- macbox clones from the template if the sandbox VM does not exist yet
- The base template is never modified by `start`

Save `run_id` and `run_dir` from the JSON response.

Named profiles can be used anywhere an image can be used:

```bash
macbox profiles --json
macbox start --profile macos-sequoia-dark-mode --name macbox-dark-001 --json
```

### Guest control primitives

When the fixed smoke/gate workflows are not enough, use the guest-side primitives:

```bash
macbox exec --name macbox-test-001 --command "uname -a" --json
macbox applescript --name macbox-test-001 --script 'tell application "Finder" to get name of startup disk' --json
macbox open-app --name macbox-test-001 --app /Applications/Ghostty.app --arg=-e --arg=zsh --json
macbox list-windows --name macbox-test-001 --json
macbox list-processes --name macbox-test-001 --filter Ghostty --json
```

For full GUI and file control inside the guest:

```bash
# Keyboard and mouse automation (needs guest Accessibility permission)
macbox type-text --name macbox-test-001 --text "hello world" --json
macbox send-keys --name macbox-test-001 --key c --modifier command --json
macbox send-keys --name macbox-test-001 --key return --json
macbox click --name macbox-test-001 --x 200 --y 150 --button left --count 2 --json

# JavaScript for Automation (ObjC bridge) escape hatch
macbox jxa --name macbox-test-001 --script 'Application("Finder").name()' --json

# Arbitrary file transfer in both directions (secret paths still blocked)
macbox push --name macbox-test-001 --path ./fixtures/config.json --dest /Users/admin/config.json --json
macbox pull --name macbox-test-001 --src /Users/admin/output.log --dest ./out/output.log --json
```

These run inside the guest VM only. They do not expose host shell access.

### Upload a build

```bash
macbox upload \
  --name macbox-test-001 \
  --path ./dist/MyApp.app \
  --dest /Users/admin/Desktop/MyApp.app \
  --json
```

The typed `upload` / `upload-dmg` commands accept `.app` bundles, `.dmg` images, or `.pkg` files. Use `macbox push` to copy any other file or directory into the guest, and `macbox pull` to retrieve files from the guest. All of these block obvious secret paths.

### Run a smoke test

```bash
macbox run-app \
  --name macbox-test-001 \
  --app /Users/admin/Desktop/MyApp.app \
  --timeout 120 \
  --json
```

This will:

1. Confirm the app exists in the guest
2. Snapshot existing crash reports
3. Launch with `open`
4. Wait for `--timeout` seconds
5. Capture screenshot, logs, and new crash reports

Response `data` fields:

| Field | Meaning |
|-------|---------|
| `launched` | `open` succeeded |
| `crashed` | New crash report appeared after launch |
| `screenshot` | Host path to PNG |
| `logs` | Host path to collected syslog excerpt |
| `crash_reports` | List of downloaded crash files |

Artifacts live under `~/.macbox/runs/<run_id>/`.

Every launch also writes `~/.macbox/runs/<run_id>/reports/report.json`. Read it directly or through:

```bash
macbox report <run_id> --json
```

The report is the agent-facing summary. It includes verdict, reason, diagnosis, next actions, and crash-summary fields when available.

### Validate a DMG release artifact

```bash
macbox upload-dmg \
  --name macbox-test-001 \
  --path ./release/MyApp.dmg \
  --json

macbox mount-dmg \
  --name macbox-test-001 \
  --dmg /Users/admin/Desktop/MyApp.dmg \
  --json

macbox install-dmg-app \
  --name macbox-test-001 \
  --app MyApp.app \
  --json

macbox run-installed-app \
  --name macbox-test-001 \
  --app MyApp.app \
  --timeout 120 \
  --json
```

### Validate a PKG release artifact

```bash
macbox upload \
  --name macbox-test-001 \
  --path ./release/MyApp.pkg \
  --json

macbox install-pkg \
  --name macbox-test-001 \
  --pkg /Users/admin/Desktop/MyApp.pkg \
  --app MyApp.app \
  --timeout 600 \
  --json
```

`install-pkg` records:

- installer exit code
- new apps in `/Applications`
- new LaunchAgents and LaunchDaemons
- postinstall log artifact
- installed files per detected package ID when available
- launch/crash evidence if an installed app is launched

`sudo -n installer ...` is used inside the guest. If your template requires an interactive sudo password, package installs will fail until that guest image is configured for non-interactive admin installs.

### UI assertions

Use assertions after launch when you need more than "did it crash":

```bash
macbox assert-window --name macbox-test-001 --contains Welcome --json
macbox assert-app-running --name macbox-test-001 --bundle-id com.example.MyApp --json
```

### Warm VM loop

For faster iterative testing:

```bash
macbox warm --image macos-sequoia-clean --name macbox-warm-sequoia --json
macbox run-on-warm --name macbox-warm-sequoia --app ./dist/MyApp.app --json
macbox reset-warm --image macos-sequoia-clean --name macbox-warm-sequoia --json
macbox destroy --name macbox-warm-sequoia --json
```

### Release gate and matrix

Gate mode gives you a single pass/fail result with structured evidence:

```bash
macbox gate \
  --image macos-sequoia-clean \
  --artifact ./release/MyApp.dmg \
  --app MyApp.app \
  --requirements launch,no-crash,screenshot,no-new-crash-report \
  --json
```

Supported requirements:

- `launch`
- `no-crash`
- `screenshot`
- `no-new-crash-report`
- `app-running`
- `window:<text>`

`app-running` requires `--bundle-id`.

For multi-image validation:

```bash
macbox matrix \
  --images macos-sequoia-clean,macos-sonoma-clean,macos-ventura-clean \
  --artifact ./release/MyApp.dmg \
  --app MyApp.app \
  --json
```

### Extra evidence

```bash
macbox logs --name macbox-test-001 --last 5m --json
macbox screenshot --name macbox-test-001 --json
macbox collect-crashes --name macbox-test-001 --json
```

### Clean up

```bash
macbox destroy --name macbox-test-001 --json
```

Always destroy sandboxes when finished. Orphan Tart processes waste disk and RAM.

To refresh a sandbox in place:

```bash
macbox reset --image macos-sequoia-clean --name macbox-test-001 --json
```

## MCP workflow

### Configure your IDE

```json
{
  "mcpServers": {
    "macbox": {
      "command": "/Users/you/Code/macbox/.venv/bin/python",
      "args": ["/Users/you/Code/macbox/mcp/macbox_mcp.py"]
    }
  }
}
```

Use the venv Python so `macbox` and `mcp` are on the path.

### Available tools

| Tool | What it does |
|------|----------------|
| `macbox_status` | Host + Tart readiness |
| `list_images` | Local Tart VMs (same as CLI `images`) |
| `list_profiles` | Built-in and configured sandbox profiles |
| `create_sandbox` | `start` with auto-generated `macbox-<id>` name |
| `create_warm_sandbox` | Start a reusable warm VM |
| `run_on_warm_sandbox` | Upload a local `.app` to a warm VM and smoke-test it |
| `exec_in_guest` | Run a guest shell command |
| `run_applescript_in_guest` | Run guest AppleScript |
| `run_jxa_in_guest` | Run guest JavaScript for Automation (ObjC bridge) |
| `type_text_in_guest` | Type literal text via keyboard automation |
| `send_keys_in_guest` | Send a key / key-combo (named keys, modifiers) |
| `click_in_guest` | Click at guest screen coordinates |
| `open_guest_app` | Launch an app with optional arguments |
| `list_guest_windows` | Read visible guest window titles |
| `list_guest_processes` | Read guest process state |
| `upload_app` | Upload `.app` to guest Desktop |
| `upload_dmg` | Upload `.dmg` to guest Desktop |
| `upload_pkg` | Upload `.pkg` to guest Desktop |
| `push_file_to_guest` | Upload any file/dir to a guest path |
| `pull_file_from_guest` | Download any file/dir from the guest |
| `mount_dmg_image` | Mount a guest DMG |
| `install_dmg_guest_app` | Copy an app from a DMG into `/Applications` |
| `install_guest_pkg` | Run installer validation for a guest `.pkg` |
| `run_app_smoke_test` | Launch app and collect evidence |
| `run_installed_guest_app` | Launch an app from `/Applications` |
| `assert_window` | Check a guest window title |
| `assert_app_running` | Check a running app by bundle ID |
| `collect_logs` | Recent guest syslog |
| `take_screenshot` | Guest screen capture |
| `collect_crashes` | DiagnosticReports from guest |
| `get_run_report` | Load a prior structured run report |
| `run_release_gate` | One-shot pass/fail validation for `.app` / `.dmg` / `.pkg` |
| `run_release_matrix` | Run the same artifact across multiple images |
| `reset_sandbox` | Stop, delete, re-clone, start |
| `reset_warm_sandbox` | Reset a warm sandbox in place |
| `stop_sandbox` | Stop a VM without deleting it |
| `run_doctor` | Run environment checks |
| `destroy_sandbox` | Stop and delete sandbox |

MCP calls the `macbox` CLI with fixed argument arrays. It does not expose raw Tart or host shell access.

### Example agent prompt

> Use macbox MCP only (no shell). Create a headless sandbox from `macos-sequoia-clean`, upload `/Applications/Amphetamine.app`, run a 60-second smoke test, collect logs and crashes, then destroy the sandbox. Report artifact paths and any errors.

### Agent skill

Copy [skills/macos-sandbox/SKILL.md](../skills/macos-sandbox/SKILL.md) to:

- `~/.agents/skills/macos-sandbox/SKILL.md`, or
- `.agents/skills/macos-sandbox/SKILL.md` in your project

## JSON contract

Success shape:

```json
{
  "ok": true,
  "command": "start",
  "vm": "macbox-test-001",
  "data": {},
  "warnings": [],
  "errors": []
}
```

Failure shape:

```json
{
  "ok": false,
  "command": "run-app",
  "vm": "macbox-test-001",
  "data": {},
  "warnings": [],
  "errors": [
    {
      "code": "APP_CRASHED",
      "message": "The app crashed after launch.",
      "details": {}
    }
  ]
}
```

Common error codes: `SAFETY_ERROR`, `TART_ERROR`, `SSH_ERROR`, `VM_NOT_READY`, `APP_CRASHED`, `APP_ERROR`, `RUN_ERROR`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `doctor` fails `tart` | Install Tart; confirm `which tart` |
| `doctor` fails `ssh_identity` | Create `~/.ssh/macbox_id` |
| `VM_NOT_READY` | Guest still booting; wait and retry |
| SSH `Permission denied` | Re-run `ssh-copy-id`; check Remote Login |
| `SAFETY_ERROR` on destroy | You tried to delete a protected template name |
| `SAFETY_ERROR` on start | Sandbox name equals base image name; pick a different `--name` |
| Upload rejected | Path must be `.app`, `.dmg`, or `.pkg`; not a secret directory |
| Blank screenshot | Common in headless mode; rely on logs/crashes |
| `tart list` shows ghcr.io rows | Normal cache entries; use local `macos-sequoia-clean` |

## Testing macbox itself

```bash
pytest tests/ -v --ignore=tests/integration
MACBOX_RUN_INTEGRATION=1 pytest tests/integration
```

Integration tests need Tart and a working template VM.

## What is manual on purpose

macbox does not auto-configure the guest GUI, install SSH keys, or click through security dialogs. That keeps the behavior predictable and avoids storing passwords. You do the template prep once, then the disposable sandboxes follow the same path each time.

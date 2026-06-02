# macbox guide

This is the practical walkthrough: install, prepare a template VM, run tests from the CLI or MCP, read artifacts, fix common problems.

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

This downloads a large OCI image. On a slow connection, clone on another Mac and `tart export` / `tart import` the `.tvm` file instead.

### 3. Boot the template and enable SSH

```bash
tart run macos-sequoia-clean
```

In the guest (GUI):

1. **System Settings → General → Sharing → Remote Login** — on
2. **Users & Groups → Login Options** — auto-login for `admin`
3. **Lock Screen** — disable password on wake if you can

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
  "log_collect_duration": "5m"
}
```

`protected_images` always includes `default_image`. macbox refuses to `destroy` or `reset` those names so you do not delete your template by mistake.

Override state dir for tests:

```bash
export MACBOX_STATE_DIR=/tmp/macbox-test
```

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

### Upload a build

```bash
macbox upload \
  --name macbox-test-001 \
  --path ./dist/MyApp.app \
  --dest /Users/admin/Desktop/MyApp.app \
  --json
```

Uploads must be `.app` bundles or `.pkg` files. macbox blocks obvious secret paths unless you pass `--allow-secret-path` (CLI only, not MCP).

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
| `create_sandbox` | `start` with auto-generated `macbox-<id>` name |
| `upload_app` | Upload `.app` to guest Desktop |
| `upload_pkg` | Upload `.pkg` to guest Desktop |
| `run_app_smoke_test` | Launch app and collect evidence |
| `collect_logs` | Recent guest syslog |
| `take_screenshot` | Guest screen capture |
| `collect_crashes` | DiagnosticReports from guest |
| `reset_sandbox` | Stop, delete, re-clone, start |
| `destroy_sandbox` | Stop and delete sandbox |

MCP calls the `macbox` CLI with fixed argument arrays. No raw Tart. No host shell.

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

Common error codes: `SAFETY_ERROR`, `TART_ERROR`, `SSH_ERROR`, `VM_NOT_READY`, `APP_CRASHED`, `APP_ERROR`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `doctor` fails `tart` | Install Tart; confirm `which tart` |
| `doctor` fails `ssh_identity` | Create `~/.ssh/macbox_id` |
| `VM_NOT_READY` | Guest still booting; wait and retry |
| SSH `Permission denied` | Re-run `ssh-copy-id`; check Remote Login |
| `SAFETY_ERROR` on destroy | You tried to delete a protected template name |
| `SAFETY_ERROR` on start | Sandbox name equals base image name; pick a different `--name` |
| Upload rejected | Path must be `.app` or `.pkg`; not a secret directory |
| Blank screenshot | Common in headless mode; rely on logs/crashes |
| `tart list` shows ghcr.io rows | Normal cache entries; use local `macos-sequoia-clean` |

## Testing macbox itself

```bash
pytest tests/ -v --ignore=tests/integration
MACBOX_RUN_INTEGRATION=1 pytest tests/integration
```

Integration tests need Tart and a working template VM.

## What is manual on purpose

macbox does not auto-configure the guest GUI, install SSH keys, or click through security dialogs. That keeps behavior predictable and avoids storing passwords. You do template prep once; sandboxes are disposable from then on.

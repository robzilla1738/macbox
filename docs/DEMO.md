# macbox demo

This file records the working baseline and the exact successful outputs captured on June 1, 2026.

## Baseline

- Repo: `/Users/robert/Code/macbox`
- Template image: `macos-sequoia-clean`
- Baseline readiness output: [2026-06-01-doctor.json](/Users/robert/Code/macbox/docs/examples/2026-06-01-doctor.json)

## Saved example outputs

- Amphetamine `.app` demo: [2026-06-01-demo-amphetamine.json](/Users/robert/Code/macbox/docs/examples/2026-06-01-demo-amphetamine.json)
- Clicky `.dmg` gate: [2026-06-01-gate-clicky-dmg.json](/Users/robert/Code/macbox/docs/examples/2026-06-01-gate-clicky-dmg.json)
- Amphetamine `.pkg` flow:
  [pkg-start](/Users/robert/Code/macbox/docs/examples/2026-06-01-pkg-start.json),
  [pkg-upload](/Users/robert/Code/macbox/docs/examples/2026-06-01-pkg-upload.json),
  [install-pkg](/Users/robert/Code/macbox/docs/examples/2026-06-01-install-amphetamine-pkg.json),
  [pkg-destroy](/Users/robert/Code/macbox/docs/examples/2026-06-01-pkg-destroy.json)
- Warm flow:
  [warm-create](/Users/robert/Code/macbox/docs/examples/2026-06-01-warm-create.json),
  [warm-run](/Users/robert/Code/macbox/docs/examples/2026-06-01-warm-run.json),
  [warm-reset](/Users/robert/Code/macbox/docs/examples/2026-06-01-warm-reset.json),
  [warm-destroy](/Users/robert/Code/macbox/docs/examples/2026-06-01-warm-destroy.json)
- Matrix flow: [2026-06-01-matrix-amphetamine.json](/Users/robert/Code/macbox/docs/examples/2026-06-01-matrix-amphetamine.json)
- Cursor MCP gate summary: [2026-06-01-cursor-mcp-gate-summary.json](/Users/robert/Code/macbox/docs/examples/2026-06-01-cursor-mcp-gate-summary.json)

## Demo path

### 1. Amphetamine.app

Command:

```bash
cd /Users/robert/Code/macbox
macbox demo --app /Applications/Amphetamine.app --image macos-sequoia-clean --timeout 10 --json
```

Result:
- verdict: `passed`
- run id: `2026-06-02T02-03-37Z-macbox-demo-63e3c5bd`
- report: [report.json](/Users/robert/.macbox/runs/2026-06-02T02-03-37Z-macbox-demo-63e3c5bd/reports/report.json)
- screenshot: [launch.png](/Users/robert/.macbox/runs/2026-06-02T02-03-37Z-macbox-demo-63e3c5bd/screenshots/launch.png)
- logs: [system.log](/Users/robert/.macbox/runs/2026-06-02T02-03-37Z-macbox-demo-63e3c5bd/logs/system.log)

### 2. Clicky.dmg

Command:

```bash
cd /Users/robert/Code/macbox
macbox gate --image macos-sequoia-clean --artifact /Users/robert/Downloads/Clicky.dmg --app Clicky.app --timeout 10 --json
```

Result:
- verdict: `passed`
- run id: `2026-06-02T02-04-06Z-macbox-gate-91c4f01e`
- report: [report.json](/Users/robert/.macbox/runs/2026-06-02T02-04-06Z-macbox-gate-91c4f01e/reports/report.json)
- screenshot: [launch.png](/Users/robert/.macbox/runs/2026-06-02T02-04-06Z-macbox-gate-91c4f01e/screenshots/launch.png)
- logs: [system.log](/Users/robert/.macbox/runs/2026-06-02T02-04-06Z-macbox-gate-91c4f01e/logs/system.log)
- crash reports: none

### 3. Amphetamine.pkg

Artifact build:

```bash
mkdir -p /tmp/macbox-demo-artifacts
pkgbuild --quiet --component /Applications/Amphetamine.app --install-location /Applications /tmp/macbox-demo-artifacts/Amphetamine.pkg
```

Commands:

```bash
cd /Users/robert/Code/macbox
macbox start --image macos-sequoia-clean --name macbox-demo-pkg --headless --json
macbox upload --name macbox-demo-pkg --path /tmp/macbox-demo-artifacts/Amphetamine.pkg --json
macbox install-pkg --name macbox-demo-pkg --pkg /Users/admin/Desktop/Amphetamine.pkg --app Amphetamine.app --timeout 120 --json
macbox destroy --name macbox-demo-pkg --json
```

Result:
- verdict: `passed`
- run id: `2026-06-02T02-04-49Z-macbox-demo-pkg`
- report: [report.json](/Users/robert/.macbox/runs/2026-06-02T02-04-49Z-macbox-demo-pkg/reports/report.json)
- screenshot: [launch.png](/Users/robert/.macbox/runs/2026-06-02T02-04-49Z-macbox-demo-pkg/screenshots/launch.png)
- logs: [system.log](/Users/robert/.macbox/runs/2026-06-02T02-04-49Z-macbox-demo-pkg/logs/system.log)

### 4. Warm flow

Commands:

```bash
cd /Users/robert/Code/macbox
macbox warm --image macos-sequoia-clean --name macbox-demo-warm --headless --json
macbox run-on-warm --name macbox-demo-warm --app /Applications/Amphetamine.app --timeout 10 --json
macbox reset-warm --image macos-sequoia-clean --name macbox-demo-warm --headless --json
macbox destroy --name macbox-demo-warm --json
```

Result:
- warm run verdict: `passed`
- run id: `2026-06-02T02-07-12Z-macbox-demo-warm`
- report: [report.json](/Users/robert/.macbox/runs/2026-06-02T02-07-12Z-macbox-demo-warm/reports/report.json)
- screenshot: [launch.png](/Users/robert/.macbox/runs/2026-06-02T02-07-12Z-macbox-demo-warm/screenshots/launch.png)
- logs: [system.log](/Users/robert/.macbox/runs/2026-06-02T02-07-12Z-macbox-demo-warm/logs/system.log)
- reset run id: `2026-06-02T02-07-56Z-macbox-demo-warm`

### 5. Matrix flow

Command:

```bash
cd /Users/robert/Code/macbox
macbox matrix --images macos-sequoia-clean --artifact /Applications/Amphetamine.app --timeout 10 --json
```

Result:
- matrix verdict: `passed`
- image: `macos-sequoia-clean`
- per-image report: [report.json](/Users/robert/.macbox/runs/2026-06-02T02-08-11Z-macbox-gate-490e4114/reports/report.json)

### 6. MCP flow from Cursor

Prompt used in Cursor:

```text
Use the macbox MCP tools only. Do not run shell commands. Use `macbox-core` for status/lifecycle/evidence and `macbox-power` for the release-gate tool.

Create a macOS sandbox from macos-sequoia-clean, run a gate test for /Applications/Amphetamine.app, collect the report, screenshot, logs, crash summary, and destroy the sandbox. Return only the verdict and artifact paths.
```

What happened in Cursor:
- Cursor loaded the `user-macbox` MCP server and executed `Run Release Gate in macbox`
- Cursor also inspected local MCP descriptor files and ran `macbox_status` before the gate call
- Cursor returned a passed verdict with artifact paths

Result:
- verdict: `passed`
- run id: `2026-06-02T02-10-52Z-macbox-gate-0419fa04`
- report: [report.json](/Users/robert/.macbox/runs/2026-06-02T02-10-52Z-macbox-gate-0419fa04/reports/report.json)
- screenshot: [launch.png](/Users/robert/.macbox/runs/2026-06-02T02-10-52Z-macbox-gate-0419fa04/screenshots/launch.png)
- logs: [system.log](/Users/robert/.macbox/runs/2026-06-02T02-10-52Z-macbox-gate-0419fa04/logs/system.log)
- crash summary: none

## Safety boundary re-review

The current boundary is still the right one:

- uploads are restricted to `.app`, `.pkg`, and `.dmg` in [src/macbox/safety.py](/Users/robert/Code/macbox/src/macbox/safety.py:95)
- secret and profile-like host paths are blocked in [src/macbox/safety.py](/Users/robert/Code/macbox/src/macbox/safety.py:13)
- protected template VMs cannot be started/reset/destroyed as disposable names in [src/macbox/safety.py](/Users/robert/Code/macbox/src/macbox/safety.py:36)
- MCP does not expose host shell execution; that contract is tested in [tests/test_hardening.py](/Users/robert/Code/macbox/tests/test_hardening.py:158)

## Next important validation

The next step is a stricter IDE-agent check:

1. start a fresh Cursor or Codex agent session with `macbox-core` and `macbox-power` loaded
2. verify it goes straight to MCP tool calls without local shell or file inspection
3. repeat the same gate on a second local template image so `matrix` covers a real multi-image path instead of a single-image pass-through

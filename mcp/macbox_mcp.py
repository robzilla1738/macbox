"""macbox MCP server — narrow tools delegating to the macbox CLI."""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from macbox.redact import redact_secrets, redact_value
from macbox.runner import run_command
from macbox.safety import validate_upload_path, validate_vm_name

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - optional dependency
    raise SystemExit(
        "MCP support requires the mcp package. Install with: pip install 'macbox[mcp]'"
    ) from exc

MCP_PROFILE_ENV = "MACBOX_MCP_PROFILE"
MCP_PROFILE = os.environ.get(MCP_PROFILE_ENV, "all").strip().lower() or "all"
ALLOWED_MCP_PROFILES = {"all", "core", "power"}

if MCP_PROFILE not in ALLOWED_MCP_PROFILES:
    raise SystemExit(
        f"{MCP_PROFILE_ENV} must be one of: {', '.join(sorted(ALLOWED_MCP_PROFILES))}"
    )

CORE_TOOL_NAMES = {
    "macbox_status",
    "list_images",
    "list_profiles",
    "create_sandbox",
    "upload_app",
    "upload_dmg",
    "upload_pkg",
    "run_app_smoke_test",
    "collect_logs",
    "take_screenshot",
    "collect_crashes",
    "get_run_report",
    "watch_sandbox",
    "reset_sandbox",
    "stop_sandbox",
    "run_doctor",
    "destroy_sandbox",
}

mcp = FastMCP("macbox" if MCP_PROFILE == "all" else f"macbox-{MCP_PROFILE}")
_fastmcp_tool = mcp.tool
_ALL_TOOL_NAMES: list[str] = []
_ACTIVE_TOOL_NAMES: list[str] = []


def _tool_profiles(tool_name: str) -> set[str]:
    return {"core"} if tool_name in CORE_TOOL_NAMES else {"power"}


def _should_register_tool(tool_name: str) -> bool:
    return MCP_PROFILE == "all" or MCP_PROFILE in _tool_profiles(tool_name)


def _profiled_tool(*args, **kwargs):
    """Register tools according to MACBOX_MCP_PROFILE while preserving @mcp.tool()."""

    def decorate(fn):
        _ALL_TOOL_NAMES.append(fn.__name__)
        fn._macbox_mcp_profiles = _tool_profiles(fn.__name__)  # type: ignore[attr-defined]
        if not _should_register_tool(fn.__name__):
            return fn
        _ACTIVE_TOOL_NAMES.append(fn.__name__)
        return _fastmcp_tool(*args, **kwargs)(fn)

    return decorate


mcp.tool = _profiled_tool  # type: ignore[method-assign]


def active_tool_names() -> list[str]:
    """Return the tool names registered for the active MCP profile."""
    return sorted(_ACTIVE_TOOL_NAMES)


def all_tool_names() -> list[str]:
    """Return every tool name known to the full MCP surface."""
    return sorted(_ALL_TOOL_NAMES)


def _macbox_argv() -> list[str]:
    cli = shutil.which("macbox")
    if cli:
        return [cli]
    return [sys.executable, "-m", "macbox.cli"]


def _run_macbox(*args: str) -> dict[str, Any]:
    argv = _macbox_argv() + list(args)
    result = run_command(argv, timeout=1200)
    if not result.stdout.strip():
        return {
            "ok": False,
            "command": args[0] if args else "unknown",
            "vm": None,
            "data": {},
            "warnings": [],
            "errors": [
                {
                    "code": "CLI_ERROR",
                    "message": redact_secrets(result.stderr or "macbox CLI produced no output"),
                    "details": redact_value({"exit_code": result.exit_code, "argv": list(argv)}),
                }
            ],
        }
    payload = json.loads(result.stdout)
    if result.exit_code != 0 and payload.get("ok", True):
        payload["ok"] = False
    return payload


def _guest_dest_for_upload(local_path: Path) -> str:
    return f"/Users/admin/Desktop/{local_path.name}"


@mcp.tool()
def macbox_status() -> dict[str, Any]:
    """Check macbox and Tart readiness."""
    return _run_macbox("status", "--json")


@mcp.tool()
def list_images() -> dict[str, Any]:
    """List local Tart VM images."""
    return _run_macbox("images", "--json")


@mcp.tool()
def list_profiles() -> dict[str, Any]:
    """List built-in and configured sandbox profiles."""
    return _run_macbox("profiles", "--json")


@mcp.tool()
def create_sandbox(
    image: str,
    headless: bool = True,
    profile: str | None = None,
    display_mode: str | None = None,
) -> dict[str, Any]:
    """Create and start a disposable sandbox VM from a base image or profile."""
    vm_name = f"macbox-{uuid.uuid4().hex[:8]}"
    validate_vm_name(vm_name)
    args = ["start", "--name", vm_name]
    if image:
        args.extend(["--image", image])
    if profile:
        args.extend(["--profile", profile])
    if display_mode:
        args.extend(["--display-mode", display_mode])
    elif headless:
        args.append("--headless")
    else:
        args.append("--no-headless")
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def create_warm_sandbox(
    image: str,
    headless: bool = True,
    profile: str | None = None,
    display_mode: str | None = None,
) -> dict[str, Any]:
    """Create a warm sandbox VM that stays running between tests."""
    vm_name = f"macbox-warm-{uuid.uuid4().hex[:8]}"
    validate_vm_name(vm_name)
    args = ["warm", "--name", vm_name]
    if image:
        args.extend(["--image", image])
    if profile:
        args.extend(["--profile", profile])
    if display_mode:
        args.extend(["--display-mode", display_mode])
    elif headless:
        args.append("--headless")
    else:
        args.append("--no-headless")
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def run_on_warm_sandbox(vm_name: str, app_path: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Upload a local .app to a warm VM and run the smoke test without recreating the sandbox."""
    validate_vm_name(vm_name)
    local = validate_upload_path(app_path, mcp_mode=True, allowed_suffixes=(".app",))
    return _run_macbox(
        "run-on-warm",
        "--name",
        vm_name,
        "--app",
        str(local),
        "--timeout",
        str(timeout_seconds),
        "--json",
    )


@mcp.tool()
def reset_warm_sandbox(
    vm_name: str,
    image: str | None = None,
    profile: str | None = None,
    display_mode: str | None = None,
) -> dict[str, Any]:
    """Reset a warm sandbox VM back to a clean state."""
    validate_vm_name(vm_name)
    args = ["reset-warm", "--name", vm_name]
    if image:
        args.extend(["--image", image])
    if profile:
        args.extend(["--profile", profile])
    if display_mode:
        args.extend(["--display-mode", display_mode])
    else:
        args.append("--headless")
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def exec_in_guest(vm_name: str, command: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Run a shell command inside the guest VM. This executes only inside the sandbox, not on the host."""
    validate_vm_name(vm_name)
    return _run_macbox(
        "exec",
        "--name",
        vm_name,
        "--command",
        command,
        "--timeout",
        str(timeout_seconds),
        "--json",
    )


@mcp.tool()
def run_applescript_in_guest(vm_name: str, script: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Run AppleScript inside the guest VM for UI automation or inspection."""
    validate_vm_name(vm_name)
    return _run_macbox(
        "applescript",
        "--name",
        vm_name,
        "--script",
        script,
        "--timeout",
        str(timeout_seconds),
        "--json",
    )


@mcp.tool()
def open_guest_app(
    vm_name: str,
    app_path: str,
    args: list[str] | None = None,
    new_instance: bool = True,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Open an app inside the guest VM with optional launch arguments."""
    validate_vm_name(vm_name)
    cli_args = [
        "open-app",
        "--name",
        vm_name,
        "--app",
        app_path,
        "--wait-seconds",
        str(wait_seconds),
    ]
    if new_instance:
        cli_args.append("--new-instance")
    else:
        cli_args.append("--reuse-instance")
    for item in args or []:
        cli_args.extend(["--arg", item])
    cli_args.append("--json")
    return _run_macbox(*cli_args)


@mcp.tool()
def run_jxa_in_guest(vm_name: str, script: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Run a JavaScript for Automation (JXA) script inside the guest VM.

    JXA reaches the ObjC bridge (Quartz/CoreGraphics) for capabilities plain
    AppleScript cannot express, such as synthesizing low-level input.
    """
    validate_vm_name(vm_name)
    return _run_macbox("jxa", "--name", vm_name, "--script", script, "--timeout", str(timeout_seconds), "--json")


@mcp.tool()
def type_text_in_guest(vm_name: str, text: str, timeout_seconds: int = 30) -> dict[str, Any]:
    """Type literal text into the frontmost guest app (keyboard automation)."""
    validate_vm_name(vm_name)
    return _run_macbox("type-text", "--name", vm_name, "--text", text, "--timeout", str(timeout_seconds), "--json")


@mcp.tool()
def send_keys_in_guest(
    vm_name: str,
    key: str,
    modifiers: list[str] | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Send a key or key-combination to the guest.

    ``key`` is a single character, a named key (return, tab, escape, space,
    delete, arrows, f1-f12, etc.), or "key:<code>" for a raw virtual key code.
    ``modifiers`` accepts command/option/control/shift/fn (and aliases).
    """
    validate_vm_name(vm_name)
    args = ["send-keys", "--name", vm_name, "--key", key]
    for modifier in modifiers or []:
        args.extend(["--modifier", modifier])
    args.extend(["--timeout", str(timeout_seconds), "--json"])
    return _run_macbox(*args)


@mcp.tool()
def click_in_guest(
    vm_name: str,
    x: float,
    y: float,
    button: str = "left",
    count: int = 1,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Click at guest screen coordinates (mouse automation via CGEvent)."""
    validate_vm_name(vm_name)
    return _run_macbox(
        "click",
        "--name",
        vm_name,
        "--x",
        str(x),
        "--y",
        str(y),
        "--button",
        button,
        "--count",
        str(count),
        "--timeout",
        str(timeout_seconds),
        "--json",
    )


@mcp.tool()
def watch_sandbox(vm_name: str, open_viewer: bool = False) -> dict[str, Any]:
    """Return live-watch instructions for a running VM; optionally open the fixed VNC URL."""
    validate_vm_name(vm_name)
    args = ["watch", "--name", vm_name]
    if open_viewer:
        args.append("--open")
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def prepare_agent_workspace(vm_name: str, reset: bool = False) -> dict[str, Any]:
    """Create or reset /Users/admin/.macbox-agent inside the guest VM."""
    validate_vm_name(vm_name)
    args = ["prepare-agent-workspace", "--name", vm_name]
    if reset:
        args.append("--reset")
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def run_script_in_guest(
    vm_name: str,
    script: str,
    language: str = "shell",
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run a longer shell/AppleScript/JXA script from a guest-side file and save diagnostics."""
    validate_vm_name(vm_name)
    return _run_macbox(
        "run-script",
        "--name",
        vm_name,
        "--script",
        script,
        "--language",
        language,
        "--timeout",
        str(timeout_seconds),
        "--json",
    )


@mcp.tool()
def observe_guest(vm_name: str, process_filter: str | None = None) -> dict[str, Any]:
    """Collect screenshot, frontmost app/window, visible windows, screen size, and process state."""
    validate_vm_name(vm_name)
    args = ["observe", "--name", vm_name]
    if process_filter:
        args.extend(["--process-filter", process_filter])
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def inspect_ui_tree(
    vm_name: str,
    app_name: str | None = None,
    max_depth: int = 3,
    max_items: int = 100,
) -> dict[str, Any]:
    """Return an Accessibility tree summary for a guest app/window."""
    validate_vm_name(vm_name)
    args = [
        "inspect-ui-tree",
        "--name",
        vm_name,
        "--max-depth",
        str(max_depth),
        "--max-items",
        str(max_items),
    ]
    if app_name:
        args.extend(["--app", app_name])
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def click_ui_element(
    vm_name: str,
    app_name: str | None = None,
    role: str | None = None,
    title: str | None = None,
    name: str | None = None,
    exact: bool = False,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Click or press a guest UI element by Accessibility role/title/name selector."""
    validate_vm_name(vm_name)
    args = ["click-ui-element", "--name", vm_name, "--timeout", str(timeout_seconds)]
    if app_name:
        args.extend(["--app", app_name])
    if role:
        args.extend(["--role", role])
    if title:
        args.extend(["--title", title])
    if name:
        args.extend(["--element-name", name])
    if exact:
        args.append("--exact")
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def paste_text_in_guest(vm_name: str, text: str, timeout_seconds: int = 30) -> dict[str, Any]:
    """Set the guest clipboard and paste text into the frontmost app."""
    validate_vm_name(vm_name)
    return _run_macbox(
        "paste-text",
        "--name",
        vm_name,
        "--text",
        text,
        "--timeout",
        str(timeout_seconds),
        "--json",
    )


@mcp.tool()
def scroll_in_guest(vm_name: str, delta_x: int = 0, delta_y: int = -5, timeout_seconds: int = 30) -> dict[str, Any]:
    """Send a guest scroll-wheel event."""
    validate_vm_name(vm_name)
    return _run_macbox(
        "scroll",
        "--name",
        vm_name,
        "--delta-x",
        str(delta_x),
        "--delta-y",
        str(delta_y),
        "--timeout",
        str(timeout_seconds),
        "--json",
    )


@mcp.tool()
def drag_in_guest(
    vm_name: str,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    steps: int = 12,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Drag between two guest screen coordinates."""
    validate_vm_name(vm_name)
    return _run_macbox(
        "drag",
        "--name",
        vm_name,
        "--start-x",
        str(start_x),
        "--start-y",
        str(start_y),
        "--end-x",
        str(end_x),
        "--end-y",
        str(end_y),
        "--steps",
        str(steps),
        "--timeout",
        str(timeout_seconds),
        "--json",
    )


@mcp.tool()
def push_file_to_guest(vm_name: str, local_path: str, guest_path: str) -> dict[str, Any]:
    """Upload an arbitrary file or directory from the host into the guest VM.

    Unlike upload_app/upload_dmg/upload_pkg this accepts any file type so an
    agent can push scripts, configs, fixtures, or source. Host paths that look
    like secrets (keys, tokens, keychains, browser profiles) are still refused.
    """
    validate_vm_name(vm_name)
    local = validate_upload_path(local_path, mcp_mode=True, allow_any_suffix=True)
    return _run_macbox("push", "--name", vm_name, "--path", str(local), "--dest", guest_path, "--json")


@mcp.tool()
def pull_file_from_guest(vm_name: str, guest_path: str, local_path: str | None = None) -> dict[str, Any]:
    """Download an arbitrary file or directory from the guest VM to the host.

    When local_path is omitted the artifact is stored under the run's
    downloads/ directory and the resulting host path is returned.
    """
    validate_vm_name(vm_name)
    args = ["pull", "--name", vm_name, "--src", guest_path]
    if local_path:
        args.extend(["--dest", local_path])
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def list_guest_windows(vm_name: str, app_name: str | None = None) -> dict[str, Any]:
    """List visible guest window titles, optionally scoped to one app process name."""
    validate_vm_name(vm_name)
    args = ["list-windows", "--name", vm_name]
    if app_name:
        args.extend(["--app", app_name])
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def list_guest_processes(vm_name: str, filter_text: str | None = None) -> dict[str, Any]:
    """List guest processes with pid, CPU, RSS, and command, optionally filtered by substring."""
    validate_vm_name(vm_name)
    args = ["list-processes", "--name", vm_name]
    if filter_text:
        args.extend(["--filter", filter_text])
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def upload_app(vm_name: str, app_path: str) -> dict[str, Any]:
    """Upload a .app bundle to the guest sandbox."""
    validate_vm_name(vm_name)
    local = validate_upload_path(app_path, mcp_mode=True, allowed_suffixes=(".app",))
    return _run_macbox(
        "upload",
        "--name",
        vm_name,
        "--path",
        str(local),
        "--dest",
        _guest_dest_for_upload(local),
        "--json",
    )


@mcp.tool()
def upload_pkg(vm_name: str, pkg_path: str) -> dict[str, Any]:
    """Upload a .pkg installer to the guest sandbox."""
    validate_vm_name(vm_name)
    local = validate_upload_path(pkg_path, mcp_mode=True, allowed_suffixes=(".pkg",))
    return _run_macbox(
        "upload",
        "--name",
        vm_name,
        "--path",
        str(local),
        "--dest",
        _guest_dest_for_upload(local),
        "--json",
    )


@mcp.tool()
def upload_dmg(vm_name: str, dmg_path: str) -> dict[str, Any]:
    """Upload a .dmg release artifact to the guest sandbox."""
    validate_vm_name(vm_name)
    local = validate_upload_path(dmg_path, mcp_mode=True, allowed_suffixes=(".dmg",))
    return _run_macbox(
        "upload-dmg",
        "--name",
        vm_name,
        "--path",
        str(local),
        "--dest",
        _guest_dest_for_upload(local),
        "--json",
    )


@mcp.tool()
def mount_dmg_image(vm_name: str, guest_dmg_path: str) -> dict[str, Any]:
    """Mount a DMG that already exists in the guest VM."""
    validate_vm_name(vm_name)
    return _run_macbox("mount-dmg", "--name", vm_name, "--dmg", guest_dmg_path, "--json")


@mcp.tool()
def install_dmg_guest_app(vm_name: str, app_name: str, guest_dmg_path: str | None = None) -> dict[str, Any]:
    """Install an app bundle from a mounted or provided DMG inside the guest VM."""
    validate_vm_name(vm_name)
    args = ["install-dmg-app", "--name", vm_name, "--app", app_name]
    if guest_dmg_path:
        args.extend(["--dmg", guest_dmg_path])
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def install_guest_pkg(
    vm_name: str,
    guest_pkg_path: str,
    app_name: str | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Install a guest pkg, validate the install, and optionally launch the installed app."""
    validate_vm_name(vm_name)
    args = [
        "install-pkg",
        "--name",
        vm_name,
        "--pkg",
        guest_pkg_path,
        "--timeout",
        str(timeout_seconds),
    ]
    if app_name:
        args.extend(["--app", app_name])
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def run_app_smoke_test(vm_name: str, app_name: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Launch an app in the guest VM and collect smoke-test evidence."""
    validate_vm_name(vm_name)
    guest_app = app_name if app_name.startswith("/") else f"/Users/admin/Desktop/{app_name}"
    return _run_macbox(
        "run-app",
        "--name",
        vm_name,
        "--app",
        guest_app,
        "--timeout",
        str(timeout_seconds),
        "--json",
    )


@mcp.tool()
def run_installed_guest_app(vm_name: str, app_name: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Launch an installed app from /Applications in the guest VM."""
    validate_vm_name(vm_name)
    return _run_macbox(
        "run-installed-app",
        "--name",
        vm_name,
        "--app",
        app_name,
        "--timeout",
        str(timeout_seconds),
        "--json",
    )


@mcp.tool()
def assert_window(vm_name: str, contains: str, app_name: str | None = None) -> dict[str, Any]:
    """Verify that a guest app window title contains the expected text."""
    validate_vm_name(vm_name)
    args = ["assert-window", "--name", vm_name, "--contains", contains]
    if app_name:
        args.extend(["--app", app_name])
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def assert_app_running(vm_name: str, bundle_id: str) -> dict[str, Any]:
    """Verify that a guest app identified by bundle ID is running."""
    validate_vm_name(vm_name)
    return _run_macbox(
        "assert-app-running",
        "--name",
        vm_name,
        "--bundle-id",
        bundle_id,
        "--json",
    )


@mcp.tool()
def collect_logs(vm_name: str, last: str = "5m") -> dict[str, Any]:
    """Collect recent guest system logs."""
    validate_vm_name(vm_name)
    return _run_macbox("logs", "--name", vm_name, "--last", last, "--json")


@mcp.tool()
def take_screenshot(vm_name: str) -> dict[str, Any]:
    """Capture a guest VM screenshot."""
    validate_vm_name(vm_name)
    return _run_macbox("screenshot", "--name", vm_name, "--json")


@mcp.tool()
def collect_crashes(vm_name: str) -> dict[str, Any]:
    """Collect guest crash reports."""
    validate_vm_name(vm_name)
    return _run_macbox("collect-crashes", "--name", vm_name, "--json")


@mcp.tool()
def get_run_report(run_id: str) -> dict[str, Any]:
    """Load the structured report for a previously recorded run."""
    return _run_macbox("report", run_id, "--json")


@mcp.tool()
def run_release_gate(
    artifact_path: str,
    image: str,
    requirements: str = "launch,no-crash,screenshot,no-new-crash-report",
    app_name: str | None = None,
    bundle_id: str | None = None,
    window_contains: str | None = None,
    timeout_seconds: int | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Run a full release gate for a local .app, .dmg, or .pkg artifact."""
    args = [
        "gate",
        "--artifact",
        str(validate_upload_path(artifact_path, mcp_mode=True, allowed_suffixes=(".app", ".pkg", ".dmg"))),
        "--image",
        image,
        "--requirements",
        requirements,
    ]
    if app_name:
        args.extend(["--app", app_name])
    if bundle_id:
        args.extend(["--bundle-id", bundle_id])
    if window_contains:
        args.extend(["--window-contains", window_contains])
    if timeout_seconds is not None:
        args.extend(["--timeout", str(timeout_seconds)])
    if profile:
        args.extend(["--profile", profile])
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def run_release_matrix(
    artifact_path: str,
    images: str,
    requirements: str = "launch,no-crash,screenshot,no-new-crash-report",
    app_name: str | None = None,
    bundle_id: str | None = None,
    window_contains: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run a release artifact across multiple Tart images."""
    args = [
        "matrix",
        "--artifact",
        str(validate_upload_path(artifact_path, mcp_mode=True, allowed_suffixes=(".app", ".pkg", ".dmg"))),
        "--images",
        images,
        "--requirements",
        requirements,
    ]
    if app_name:
        args.extend(["--app", app_name])
    if bundle_id:
        args.extend(["--bundle-id", bundle_id])
    if window_contains:
        args.extend(["--window-contains", window_contains])
    if timeout_seconds is not None:
        args.extend(["--timeout", str(timeout_seconds)])
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def reset_sandbox(
    image: str,
    vm_name: str,
    profile: str | None = None,
    display_mode: str | None = None,
) -> dict[str, Any]:
    """Reset a sandbox VM from a base image or profile."""
    validate_vm_name(vm_name)
    args = ["reset", "--name", vm_name, "--image", image]
    if profile:
        args.extend(["--profile", profile])
    if display_mode:
        args.extend(["--display-mode", display_mode])
    else:
        args.append("--headless")
    args.append("--json")
    return _run_macbox(*args)


@mcp.tool()
def stop_sandbox(vm_name: str) -> dict[str, Any]:
    """Stop a running sandbox VM without deleting it (state is preserved)."""
    validate_vm_name(vm_name)
    return _run_macbox("stop", "--name", vm_name, "--json")


@mcp.tool()
def run_doctor() -> dict[str, Any]:
    """Run macbox environment checks (tart, ssh, scp, SSH identity, state dir)."""
    return _run_macbox("doctor", "--json")


@mcp.tool()
def destroy_sandbox(vm_name: str) -> dict[str, Any]:
    """Stop and delete a sandbox VM."""
    validate_vm_name(vm_name)
    return _run_macbox("destroy", "--name", vm_name, "--json")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

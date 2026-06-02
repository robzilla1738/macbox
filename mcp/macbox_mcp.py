"""macbox MCP server — narrow tools delegating to the macbox CLI."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from macbox.redact import redact_value
from macbox.redact import redact_secrets
from macbox.runner import run_command
from macbox.safety import validate_upload_path, validate_vm_name

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - optional dependency
    raise SystemExit(
        "MCP support requires the mcp package. Install with: pip install 'macbox[mcp]'"
    ) from exc

mcp = FastMCP("macbox")


def _macbox_argv() -> list[str]:
    cli = shutil.which("macbox")
    if cli:
        return [cli]
    return [sys.executable, "-m", "macbox.cli"]


def _run_macbox(*args: str) -> dict[str, Any]:
    argv = _macbox_argv() + list(args)
    result = run_command(argv, timeout=600)
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
                    "details": redact_value(
                        {"exit_code": result.exit_code, "argv": list(argv)}
                    ),
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
def create_sandbox(image: str, headless: bool = True) -> dict[str, Any]:
    """Create and start a disposable sandbox VM from a base image."""
    vm_name = f"macbox-{uuid.uuid4().hex[:8]}"
    validate_vm_name(vm_name)
    args = ["start", "--image", image, "--name", vm_name, "--json"]
    if headless:
        args.insert(-1, "--headless")
    return _run_macbox(*args)


@mcp.tool()
def upload_app(vm_name: str, app_path: str) -> dict[str, Any]:
    """Upload a .app bundle to the guest sandbox."""
    validate_vm_name(vm_name)
    local = validate_upload_path(app_path, mcp_mode=True)
    dest = _guest_dest_for_upload(local)
    return _run_macbox(
        "upload",
        "--name",
        vm_name,
        "--path",
        str(local),
        "--dest",
        dest,
        "--json",
    )


@mcp.tool()
def upload_pkg(vm_name: str, pkg_path: str) -> dict[str, Any]:
    """Upload a .pkg installer to the guest sandbox."""
    validate_vm_name(vm_name)
    local = validate_upload_path(pkg_path, mcp_mode=True)
    dest = _guest_dest_for_upload(local)
    return _run_macbox(
        "upload",
        "--name",
        vm_name,
        "--path",
        str(local),
        "--dest",
        dest,
        "--json",
    )


@mcp.tool()
def run_app_smoke_test(
    vm_name: str,
    app_name: str,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Launch an app in the guest VM and collect smoke-test evidence."""
    validate_vm_name(vm_name)
    if not app_name.startswith("/"):
        guest_app = f"/Users/admin/Desktop/{app_name}"
    else:
        guest_app = app_name
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
def reset_sandbox(image: str, vm_name: str) -> dict[str, Any]:
    """Reset a sandbox VM from a base image."""
    validate_vm_name(vm_name)
    return _run_macbox("reset", "--image", image, "--name", vm_name, "--json")


@mcp.tool()
def destroy_sandbox(vm_name: str) -> dict[str, Any]:
    """Stop and delete a sandbox VM."""
    validate_vm_name(vm_name)
    return _run_macbox("destroy", "--name", vm_name, "--json")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

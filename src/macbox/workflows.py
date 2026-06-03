"""High-level macbox workflows shared by CLI commands and demo."""

from __future__ import annotations

import json
import re
import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from macbox.config import load_config
from macbox.errors import AppCrashedError, AppError, MacboxError, ProcessError, RunError, SafetyError, VMNotReadyError
from macbox.models import expand_path
from macbox.runner import run_command
from macbox.runs import (
    CRASH_SUFFIXES,
    RunManager,
    detect_new_crashes,
    list_crash_basenames,
)
from macbox.safety import (
    is_denied_secret_path,
    validate_disposable_vm_operation,
    validate_guest_command,
    validate_guest_path,
    validate_start_vm,
    validate_upload_path,
    validate_vm_name,
)
from macbox.ssh import GuestSession
from macbox.tart_backend import TartBackend

GUEST_CRASH_DIR = "~/Library/Logs/DiagnosticReports"

BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "macos-sequoia-clean": {
        "image": "macos-sequoia-clean",
        "notes": ["Fresh disposable VM cloned from the clean Sequoia template."],
    },
    "macos-sequoia-dark-mode": {
        "image": "macos-sequoia-clean",
        "setup_commands": [
            "osascript -e 'tell application \"System Events\" to tell appearance preferences to set dark mode to true'"
        ],
        "notes": ["Enables Dark Mode after boot."],
    },
    "macos-sequoia-no-network": {
        "image": "macos-sequoia-clean",
        "setup_commands": [
            "networksetup -listallnetworkservices | tail -n +2 | while IFS= read -r service; do networksetup -setnetworkserviceenabled \"$service\" off || true; done"
        ],
        "notes": ["Disables guest network services after boot."],
    },
    "macos-sequoia-low-disk": {
        "image": "macos-sequoia-clean",
        "setup_commands": [
            "mkdir -p /Users/admin/.macbox && mkfile -n 8g /Users/admin/.macbox/low-disk-filler.img"
        ],
        "notes": ["Consumes 8GB in the guest to expose low-disk behaviors."],
    },
    "macos-sequoia-admin-user": {
        "image": "macos-sequoia-clean",
        "notes": ["Uses the default admin account from the clean Sequoia template."],
    },
    "macos-sequoia-fresh-user": {
        "image": "macos-sequoia-fresh-user",
        "notes": [
            "Expected to map to a dedicated template image created by the user.",
        ],
    },
    "macos-sequoia-standard-user": {
        "image": "macos-sequoia-standard-user",
        "notes": [
            "Expected to map to a dedicated template image created by the user.",
        ],
    },
}


@dataclass
class StartResult:
    vm: str
    image: str
    run_id: str
    run_dir: str
    headless: bool
    profile: str | None = None


@dataclass
class UploadResult:
    local_path: str
    guest_path: str
    artifact_type: str = ".app"


@dataclass
class DownloadResult:
    guest_path: str
    local_path: str
    is_directory: bool = False


@dataclass
class AssertionResult:
    kind: str
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ok": self.ok,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class RunAppResult:
    launched: bool
    crashed: bool
    app_path: str
    screenshot: str | None
    logs: str
    crash_reports: list[str]
    warnings: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


@dataclass
class MountResult:
    dmg_path: str
    mount_point: str
    volume_name: str
    app_candidates: list[str]


@dataclass
class GuestAppLaunchResult:
    app_path: str
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class WarmRunResult:
    upload: UploadResult
    smoke: RunAppResult


@dataclass
class InstallResult:
    artifact_path: str
    app_path: str
    mount_point: str | None = None
    install_exit_code: int = 0
    new_apps: list[str] = field(default_factory=list)
    launch_agents: list[str] = field(default_factory=list)
    launch_daemons: list[str] = field(default_factory=list)
    installed_files: dict[str, list[str]] = field(default_factory=dict)
    postinstall_logs: str | None = None
    report: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateResult:
    ok: bool
    vm: str
    image: str
    run_id: str
    run_dir: str
    artifact_path: str
    artifact_type: str
    report: dict[str, Any]
    failed_requirements: list[str] = field(default_factory=list)
    destroyed: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[MacboxError] = field(default_factory=list)


@dataclass
class MatrixEntry:
    image: str
    ok: bool
    launched: bool
    crashed: bool
    failed_requirements: list[str]
    report_path: str | None
    crash_reports: list[str]


@dataclass
class DemoResult:
    ok: bool
    vm: str
    image: str
    run_id: str
    run_dir: str
    local_app: str
    guest_app: str
    launched: bool
    crashed: bool
    screenshot: str | None
    logs: str
    crash_reports: list[str]
    destroyed: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[MacboxError] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


def _backend() -> TartBackend:
    config = load_config()
    return TartBackend(config.tart_path)


def _run_manager() -> RunManager:
    return RunManager.from_config()


def _guest_session(vm_name: str) -> GuestSession:
    config = load_config()
    backend = _backend()
    ip = backend.ip(vm_name)
    return GuestSession(host=ip, config=config)


def _wait_for_guest_ready(vm_name: str, *, timeout: int = 120, poll_interval: float = 2.0) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            session = _guest_session(vm_name)
            result = session.exec("true", timeout=10)
        except ProcessError as exc:
            last_error = exc.message
            time.sleep(poll_interval)
            continue
        if result.exit_code == 0:
            return
        last_error = result.stderr.strip() or result.stdout.strip() or "SSH connection not ready"
        time.sleep(poll_interval)
    raise VMNotReadyError(
        f"Timed out waiting for guest SSH readiness: {vm_name}",
        details={"vm": vm_name, "timeout": timeout, "last_error": last_error},
    )


def _artifact_type(path: str | Path) -> str:
    resolved = Path(path)
    if resolved.name.endswith(".app"):
        return ".app"
    if resolved.suffix == ".pkg":
        return ".pkg"
    if resolved.suffix == ".dmg":
        return ".dmg"
    return resolved.suffix


def _profile_definition(profile: str | None) -> dict[str, Any] | None:
    if not profile:
        return None

    config = load_config()
    if profile in config.profiles:
        return dict(config.profiles[profile])
    if profile in BUILTIN_PROFILES:
        return dict(BUILTIN_PROFILES[profile])
    return None


def resolve_profile(
    *,
    image: str | None,
    profile: str | None,
) -> tuple[str, list[str], list[str]]:
    if profile:
        definition = _profile_definition(profile)
        if definition is None:
            if image is not None:
                raise SafetyError(
                    "Cannot combine --image with an unknown --profile value.",
                    details={"image": image, "profile": profile},
                )
            return profile, [], [
                f"Profile {profile!r} is not defined in config; treating it as a Tart image name."
            ]
        profile_image = definition.get("image")
        if image is not None and profile_image and str(image) != str(profile_image):
            raise SafetyError(
                "Conflicting --image and --profile values.",
                details={"image": image, "profile": profile, "profile_image": str(profile_image)},
            )
        resolved_image = str(profile_image or image or profile)
        setup_commands = [str(item) for item in definition.get("setup_commands", [])]
        notes = [str(item) for item in definition.get("notes", [])]
        return resolved_image, setup_commands, notes

    config = load_config()
    return image or config.default_image, [], []


def list_profiles() -> dict[str, dict[str, Any]]:
    config = load_config()
    merged = {name: dict(value) for name, value in BUILTIN_PROFILES.items()}
    for name, value in config.profiles.items():
        merged[name] = dict(value)
    return merged


def guest_crash_basenames(session: GuestSession) -> set[str]:
    files = session.list_remote_files(GUEST_CRASH_DIR)
    return list_crash_basenames(files)


def guest_exec_command(*, vm: str, command: str, timeout: int = 60):
    validate_vm_name(vm)
    guest_command = validate_guest_command(command)
    session = _guest_session(vm)
    return session.exec(guest_command, timeout=timeout)


def run_guest_applescript(*, vm: str, script: str, timeout: int = 60):
    guest_script = validate_guest_command(script)
    command = (
        "osascript <<'APPLESCRIPT'\n"
        f"{guest_script}\n"
        "APPLESCRIPT"
    )
    return guest_exec_command(vm=vm, command=command, timeout=timeout)


def list_guest_windows(*, vm: str, app_name: str | None = None) -> list[dict[str, str]]:
    if app_name:
        script = (
            "tell application \"System Events\"\n"
            f"  if not (exists application process \"{app_name}\") then return \"\"\n"
            f"  tell application process \"{app_name}\"\n"
            "    set titleLines to {}\n"
            "    repeat with w in windows\n"
            "      try\n"
            "        copy (name of w) to end of titleLines\n"
            "      on error\n"
            "        copy \"\" to end of titleLines\n"
            "      end try\n"
            "    end repeat\n"
            "  end tell\n"
            "  set AppleScript's text item delimiters to linefeed\n"
            "  return titleLines as text\n"
            "end tell"
        )
        result = run_guest_applescript(vm=vm, script=script, timeout=45)
        return [
            {"app_name": app_name, "title": line.strip()}
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    script = (
        "tell application \"System Events\"\n"
        "  set titleLines to {}\n"
        "  repeat with proc in application processes\n"
        "    set procName to name of proc\n"
        "    try\n"
        "      repeat with w in windows of proc\n"
        "        try\n"
        "          copy (procName & \"::\" & name of w) to end of titleLines\n"
        "        on error\n"
        "          copy (procName & \"::\") to end of titleLines\n"
        "        end try\n"
        "      end repeat\n"
        "    end try\n"
        "  end repeat\n"
        "  set AppleScript's text item delimiters to linefeed\n"
        "  return titleLines as text\n"
        "end tell"
    )
    result = run_guest_applescript(vm=vm, script=script, timeout=45)
    windows: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        entry = line.strip()
        if not entry:
            continue
        app, _, title = entry.partition("::")
        windows.append({"app_name": app, "title": title})
    return windows


def list_guest_processes(*, vm: str, filter_text: str | None = None) -> list[dict[str, Any]]:
    filter_text = filter_text.strip().lower() if filter_text else None
    result = guest_exec_command(
        vm=vm,
        command="ps -axo pid=,%cpu=,rss=,comm=",
        timeout=30,
    )
    processes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = raw.split(None, 3)
        if len(parts) != 4:
            continue
        pid_text, cpu_text, rss_text, command = parts
        if filter_text and filter_text not in command.lower():
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        try:
            cpu_percent = float(cpu_text)
        except ValueError:
            cpu_percent = 0.0
        try:
            rss_kb = int(rss_text)
        except ValueError:
            rss_kb = 0
        processes.append(
            {
                "pid": pid,
                "cpu_percent": cpu_percent,
                "rss_kb": rss_kb,
                "command": command,
            }
        )
    return processes


def open_guest_app(
    *,
    vm: str,
    app_path: str,
    args: list[str] | None = None,
    new_instance: bool = True,
    wait_seconds: float | None = None,
) -> GuestAppLaunchResult:
    validate_vm_name(vm)
    guest_app = validate_guest_path(app_path)
    session = _guest_session(vm)
    open_args = ["open"]
    if new_instance:
        open_args.append("-n")
    open_args.append(guest_app)
    if args:
        open_args.append("--args")
        open_args.extend(args)
    command = " ".join(shlex.quote(item) for item in open_args)
    result = session.exec(command, timeout=30)
    if wait_seconds and wait_seconds > 0:
        time.sleep(wait_seconds)
    return GuestAppLaunchResult(
        app_path=guest_app,
        argv=open_args,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


# Named keys mapped to macOS virtual key codes for keyboard automation.
NAMED_KEY_CODES: dict[str, int] = {
    "return": 36,
    "enter": 36,
    "tab": 48,
    "space": 49,
    "delete": 51,
    "backspace": 51,
    "escape": 53,
    "esc": 53,
    "forward_delete": 117,
    "home": 115,
    "end": 119,
    "pageup": 116,
    "pagedown": 121,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
}

MODIFIER_ALIASES: dict[str, str] = {
    "cmd": "command",
    "command": "command",
    "opt": "option",
    "option": "option",
    "alt": "option",
    "ctrl": "control",
    "control": "control",
    "shift": "shift",
    "fn": "function",
    "function": "function",
}

_CLICK_BUTTONS = {"left": 0, "right": 1, "center": 2}


def _applescript_string_literal(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _modifier_clause(modifiers: list[str] | None) -> str:
    if not modifiers:
        return ""
    normalized: list[str] = []
    for raw in modifiers:
        key = MODIFIER_ALIASES.get(raw.strip().lower())
        if key is None:
            raise SafetyError(
                f"Unknown keyboard modifier: {raw!r}",
                details={"allowed": sorted(set(MODIFIER_ALIASES.values()))},
            )
        normalized.append(f"{key} down")
    return " using {" + ", ".join(normalized) + "}"


def run_guest_jxa(*, vm: str, script: str, timeout: int = 60):
    """Run a JavaScript for Automation (JXA) script inside the guest VM."""
    guest_script = validate_guest_command(script)
    command = (
        "osascript -l JavaScript <<'JXA'\n"
        f"{guest_script}\n"
        "JXA"
    )
    return guest_exec_command(vm=vm, command=command, timeout=timeout)


def guest_type_text(*, vm: str, text: str, timeout: int = 30):
    """Type literal text into the frontmost guest app via System Events."""
    validate_vm_name(vm)
    script = (
        "tell application \"System Events\" to keystroke "
        f"{_applescript_string_literal(text)}"
    )
    return run_guest_applescript(vm=vm, script=script, timeout=timeout)


def guest_send_keys(*, vm: str, key: str, modifiers: list[str] | None = None, timeout: int = 30):
    """Send a single key or key-combination to the frontmost guest app.

    ``key`` may be a single character, a named special key (return, tab,
    escape, arrows, f1-f12, ...), or "key:<code>" for a raw virtual key code.
    ``modifiers`` accepts command/option/control/shift/fn and aliases.
    """
    validate_vm_name(vm)
    modifier_clause = _modifier_clause(modifiers)
    lookup = key.strip().lower()
    if lookup in NAMED_KEY_CODES:
        action = f"key code {NAMED_KEY_CODES[lookup]}"
    elif lookup.startswith("key:"):
        code_text = lookup.split(":", 1)[1]
        try:
            code = int(code_text)
        except ValueError as exc:
            raise SafetyError(f"Invalid raw key code: {key!r}", details={"key": key}) from exc
        action = f"key code {code}"
    elif len(key) == 1:
        action = f"keystroke {_applescript_string_literal(key)}"
    else:
        raise SafetyError(
            f"Unsupported key: {key!r}",
            details={"named_keys": sorted(NAMED_KEY_CODES)},
        )
    script = f"tell application \"System Events\" to {action}{modifier_clause}"
    return run_guest_applescript(vm=vm, script=script, timeout=timeout)


def guest_click(*, vm: str, x: float, y: float, button: str = "left", count: int = 1, timeout: int = 30):
    """Synthesize a mouse click at guest screen coordinates via CGEvent (JXA)."""
    validate_vm_name(vm)
    button_key = button.strip().lower()
    if button_key not in _CLICK_BUTTONS:
        raise SafetyError(f"Unknown mouse button: {button!r}", details={"allowed": sorted(_CLICK_BUTTONS)})
    if count < 1:
        raise SafetyError("Click count must be >= 1", details={"count": count})
    cg_button = _CLICK_BUTTONS[button_key]
    down_event = "kCGEventRightMouseDown" if cg_button == 1 else "kCGEventLeftMouseDown"
    up_event = "kCGEventRightMouseUp" if cg_button == 1 else "kCGEventLeftMouseUp"
    jxa = (
        "ObjC.import('Quartz');\n"
        f"var px = {float(x)};\n"
        f"var py = {float(y)};\n"
        f"var btn = {cg_button};\n"
        f"var clicks = {int(count)};\n"
        "var pt = $.CGPointMake(px, py);\n"
        "for (var i = 1; i <= clicks; i++) {\n"
        f"  var down = $.CGEventCreateMouseEvent($(), $.{down_event}, pt, btn);\n"
        "  $.CGEventSetIntegerValueField(down, $.kCGMouseEventClickState, i);\n"
        "  $.CGEventPost($.kCGHIDEventTap, down);\n"
        f"  var up = $.CGEventCreateMouseEvent($(), $.{up_event}, pt, btn);\n"
        "  $.CGEventSetIntegerValueField(up, $.kCGMouseEventClickState, i);\n"
        "  $.CGEventPost($.kCGHIDEventTap, up);\n"
        "}\n"
        "'ok'\n"
    )
    return run_guest_jxa(vm=vm, script=jxa, timeout=timeout)


def collect_logs(
    session: GuestSession,
    manager: RunManager,
    vm_name: str,
    *,
    duration: str | None = None,
) -> Path:
    config = load_config()
    dur = duration or config.log_collect_duration
    result = session.exec(
        f"log show --style syslog --last {shlex.quote(dur)}",
        timeout=120,
    )
    log_path = manager.artifact_path(vm_name, "logs", "system.log")
    log_path.write_text(result.stdout, encoding="utf-8")
    manager.register_artifact(vm_name, "logs", log_path)
    return log_path


def _write_diagnostic_text(manager: RunManager, vm_name: str, filename: str, text: str) -> Path:
    path = manager.artifact_path(vm_name, "diagnostics", filename)
    path.write_text(text, encoding="utf-8")
    manager.register_artifact(vm_name, "diagnostics", path)
    return path


def capture_screenshot(
    session: GuestSession,
    manager: RunManager,
    vm_name: str,
    *,
    filename: str = "launch.png",
) -> tuple[Path | None, str | None]:
    remote = "/tmp/macbox-shot.png"
    result = session.exec(f"screencapture -x {shlex.quote(remote)}", timeout=30)
    if result.exit_code != 0:
        return None, f"Screenshot capture failed: {result.stderr.strip() or result.stdout.strip()}"
    local = manager.artifact_path(vm_name, "screenshots", filename)
    try:
        session.download(remote, local)
    except MacboxError as exc:
        return None, exc.message
    manager.register_artifact(vm_name, "screenshots", local)
    return local, None


def download_crash_reports(
    session: GuestSession,
    manager: RunManager,
    vm_name: str,
    basenames: list[str],
) -> list[Path]:
    paths: list[Path] = []
    for basename in basenames:
        if not any(basename.endswith(suffix) for suffix in CRASH_SUFFIXES):
            continue
        remote = f"{GUEST_CRASH_DIR}/{basename}"
        local = manager.artifact_path(vm_name, "crashes", basename)
        try:
            session.download(remote, local)
        except MacboxError:
            continue
        manager.register_artifact(vm_name, "crashes", local)
        paths.append(local)
    return paths


def _apply_profile_setup(vm_name: str, setup_commands: list[str]) -> None:
    if not setup_commands:
        return

    session = _guest_session(vm_name)
    for command in setup_commands:
        result = session.exec(command, timeout=180)
        if result.exit_code != 0:
            raise AppError(
                "Guest profile setup command failed.",
                details={
                    "vm": vm_name,
                    "command": command,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.exit_code,
                },
            )


def start_sandbox(
    *,
    image: str | None,
    name: str,
    headless: bool = True,
    profile: str | None = None,
) -> StartResult:
    resolved_image, setup_commands, _notes = resolve_profile(image=image, profile=profile)
    config = load_config()
    validate_start_vm(resolved_image, name, config)
    backend = _backend()
    if not backend.vm_exists(name):
        backend.clone(resolved_image, name)
    backend.run(name, headless=headless)
    backend.ip(name)
    _wait_for_guest_ready(name)
    manager = _run_manager()
    metadata = manager.create_run(name, resolved_image)
    _apply_profile_setup(name, setup_commands)
    return StartResult(
        vm=name,
        image=resolved_image,
        run_id=metadata.run_id,
        run_dir=str(manager.run_dir(metadata.run_id)),
        headless=headless,
        profile=profile,
    )


def reset_sandbox(*, image: str | None, name: str, headless: bool = True, profile: str | None = None) -> StartResult:
    resolved_image, setup_commands, _notes = resolve_profile(image=image, profile=profile)
    config = load_config()
    validate_start_vm(resolved_image, name, config)
    validate_disposable_vm_operation(name, config, operation="reset")
    backend = _backend()
    if backend.vm_exists(name):
        backend.stop(name)
        backend.delete(name)
    backend.clone(resolved_image, name)
    backend.run(name, headless=headless)
    backend.ip(name)
    _wait_for_guest_ready(name)
    manager = _run_manager()
    metadata = manager.create_run(name, resolved_image)
    _apply_profile_setup(name, setup_commands)
    return StartResult(
        vm=name,
        image=resolved_image,
        run_id=metadata.run_id,
        run_dir=str(manager.run_dir(metadata.run_id)),
        headless=headless,
        profile=profile,
    )


def upload_artifact_to_guest(
    *,
    vm: str,
    local_path: str | Path,
    guest_path: str | None = None,
) -> UploadResult:
    validate_vm_name(vm)
    validated = validate_upload_path(local_path, mcp_mode=False)
    artifact_type = _artifact_type(validated)
    guest_dest = guest_path or f"/Users/admin/Desktop/{validated.name}"
    guest_dest = validate_guest_path(guest_dest)
    _wait_for_guest_ready(vm, timeout=60)
    session = _guest_session(vm)
    session.upload(validated, guest_dest)
    manager = _run_manager()
    local_artifact = manager.artifact_path(vm, "uploads", validated.name)
    if local_artifact.exists() or local_artifact.is_symlink():
        local_artifact.unlink()
    local_artifact.symlink_to(validated, target_is_directory=validated.is_dir())
    manager.register_artifact(vm, "uploads", local_artifact)
    return UploadResult(local_path=str(validated), guest_path=guest_dest, artifact_type=artifact_type)


def upload_app_to_guest(*, vm: str, local_path: str | Path, guest_path: str | None = None) -> UploadResult:
    return upload_artifact_to_guest(vm=vm, local_path=local_path, guest_path=guest_path)


def push_file_to_guest(*, vm: str, local_path: str | Path, guest_path: str) -> UploadResult:
    """Upload an arbitrary file or directory into the guest VM.

    Not limited to .app/.dmg/.pkg, so an agent can push scripts, configs,
    fixtures, or source trees. Host paths that look like secrets (keys, tokens,
    keychains, browser profiles) are still refused.
    """
    validate_vm_name(vm)
    validated = validate_upload_path(local_path, allow_any_suffix=True)
    guest_dest = validate_guest_path(guest_path)
    _wait_for_guest_ready(vm, timeout=60)
    session = _guest_session(vm)
    parent = str(Path(guest_dest).parent)
    if parent and parent != "/":
        session.exec(f"mkdir -p {shlex.quote(parent)}", timeout=30)
    session.upload(validated, guest_dest)
    return UploadResult(
        local_path=str(validated),
        guest_path=guest_dest,
        artifact_type=_artifact_type(validated),
    )


def pull_file_from_guest(*, vm: str, guest_path: str, local_path: str | Path | None = None) -> DownloadResult:
    """Download an arbitrary file or directory from the guest VM to the host.

    When local_path is omitted the artifact is stored under the run's
    ``downloads/`` directory. Writes to host secret paths are refused.
    """
    validate_vm_name(vm)
    guest_src = validate_guest_path(guest_path)
    _wait_for_guest_ready(vm, timeout=60)
    session = _guest_session(vm)
    if not session.remote_path_exists(guest_src):
        raise AppError(
            f"Guest path not found: {guest_src}",
            details={"guest_path": guest_src},
        )
    is_directory = session.remote_is_directory(guest_src)
    manager = _run_manager()
    if local_path is None:
        local_dest = manager.artifact_path(vm, "downloads", Path(guest_src).name)
    else:
        local_dest = expand_path(local_path)
        if is_denied_secret_path(local_dest):
            raise SafetyError(
                f"Refusing to write to sensitive host path: {local_dest}",
                details={"path": str(local_dest)},
            )
        local_dest.parent.mkdir(parents=True, exist_ok=True)
    session.download(guest_src, local_dest, recursive=is_directory)
    if local_path is None:
        manager.register_artifact(vm, "downloads", local_dest)
    return DownloadResult(
        guest_path=guest_src,
        local_path=str(local_dest),
        is_directory=is_directory,
    )


def run_on_warm(
    *,
    vm: str,
    local_app_path: str | Path,
    timeout: int,
    dsym_path: str | None = None,
    guest_path: str | None = None,
    profile: str | None = None,
) -> WarmRunResult:
    local_app = validate_upload_path(local_app_path, mcp_mode=False, allowed_suffixes=(".app",))
    upload = upload_artifact_to_guest(vm=vm, local_path=local_app, guest_path=guest_path)
    smoke = run_app_smoke(
        vm=vm,
        app_path=upload.guest_path,
        timeout=timeout,
        dsym_path=dsym_path,
        profile=profile,
    )
    return WarmRunResult(upload=upload, smoke=smoke)


def _report_path(manager: RunManager, vm_name: str) -> Path:
    return manager.artifact_path(vm_name, "reports", "report.json")


def persist_report(vm_name: str, report: dict[str, Any]) -> dict[str, Any]:
    manager = _run_manager()
    path = _report_path(manager, vm_name)
    payload = dict(report)
    payload["report_path"] = str(path)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    manager.register_artifact(vm_name, "reports", path)
    return payload


def load_report(run_id: str) -> dict[str, Any]:
    manager = _run_manager()
    manager.read_metadata(run_id)
    path = manager.run_dir(run_id) / "reports" / "report.json"
    if not path.exists():
        raise RunError(
            f"Run report not found: {run_id}",
            details={"run_id": run_id, "path": str(path)},
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_run_id_for_vm(vm_name: str) -> str:
    manager = _run_manager()
    metadata = manager.find_latest_run_for_vm(vm_name)
    if metadata is None:
        raise RunError(
            f"No run metadata found for VM: {vm_name}",
            details={"vm": vm_name},
        )
    return metadata.run_id


def _parse_crash_summary(crash_paths: list[str]) -> dict[str, Any] | None:
    if not crash_paths:
        return None

    crash_path = expand_path(crash_paths[0])
    if not crash_path.exists():
        return None

    text = crash_path.read_text(encoding="utf-8", errors="replace")
    exception_match = re.search(r"^Exception Type:\s+(.+)$", text, flags=re.MULTILINE)
    thread_match = re.search(
        r"^Thread\s+(\d+)\s+Crashed:\n(?P<body>(?:.+\n)+?)(?:^\S|\Z)",
        text,
        flags=re.MULTILINE,
    )

    top_frame = None
    frame_binary = None
    frame_address = None
    if thread_match:
        for raw_line in thread_match.group("body").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            top_frame = line
            frame_match = re.match(r"^\d+\s+(\S+)\s+(0x[0-9a-fA-F]+)\s+(.+)$", line)
            if frame_match:
                frame_binary = frame_match.group(1)
                frame_address = frame_match.group(2)
            break

    return {
        "crash_report": str(crash_path),
        "exception": exception_match.group(1).strip() if exception_match else None,
        "crashing_thread": thread_match.group(1) if thread_match else None,
        "top_frame": top_frame,
        "frame_binary": frame_binary,
        "frame_address": frame_address,
    }


def _parse_binary_images(crash_path: Path) -> dict[str, tuple[str, str]]:
    text = crash_path.read_text(encoding="utf-8", errors="replace")
    marker = "Binary Images:"
    if marker not in text:
        return {}

    images_text = text.split(marker, maxsplit=1)[1]
    mapping: dict[str, tuple[str, str]] = {}
    for line in images_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("0x"):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        load_address = parts[0]
        path_match = re.search(r"(/.+)$", stripped)
        image_path = path_match.group(1) if path_match else ""
        image_name = Path(image_path).name if image_path else parts[2]
        mapping[image_name] = (load_address, image_path)
        mapping[parts[2]] = (load_address, image_path)
    return mapping


def _symbolicate_crash_summary(vm_name: str, crash_summary: dict[str, Any] | None, dsym_path: str | None) -> dict[str, Any] | None:
    if crash_summary is None or not dsym_path or not crash_summary.get("frame_address"):
        return crash_summary

    dsym = expand_path(dsym_path)
    if not dsym.exists():
        enriched = dict(crash_summary)
        enriched["symbolication_error"] = f"dSYM not found: {dsym}"
        return enriched

    crash_path = expand_path(crash_summary["crash_report"])
    images = _parse_binary_images(crash_path)
    binary_name = str(crash_summary.get("frame_binary") or "").split("/")[-1]
    dwarf_dir = dsym / "Contents" / "Resources" / "DWARF"
    candidates = list(dwarf_dir.iterdir()) if dwarf_dir.is_dir() else []
    binary_path = None
    if binary_name:
        binary_path = next((path for path in candidates if path.name == binary_name), None)
    if binary_path is None and candidates:
        binary_path = candidates[0]
    load_address = None
    if binary_name and binary_name in images:
        load_address = images[binary_name][0]
    elif crash_summary.get("frame_binary") in images:
        load_address = images[str(crash_summary["frame_binary"])][0]

    enriched = dict(crash_summary)
    if binary_path is None or load_address is None:
        enriched["symbolication_error"] = "Could not resolve matching binary image for dSYM symbolication."
        return enriched

    result = run_command(
        [
            "atos",
            "-o",
            str(binary_path),
            "-l",
            load_address,
            str(crash_summary["frame_address"]),
        ],
        timeout=30,
    )
    if result.exit_code != 0 or not result.stdout.strip():
        enriched["symbolication_error"] = result.stderr.strip() or "atos failed"
        return enriched

    enriched["symbolicated_top_frame"] = result.stdout.strip()
    manager = _run_manager()
    _write_diagnostic_text(
        manager,
        vm_name,
        "symbolication.txt",
        result.stdout.strip() + "\n",
    )
    return enriched


def _diagnosis_for_report(report: dict[str, Any]) -> str:
    if report.get("reason") == "pkg_install_failed":
        return "Installer failed before the app launch phase."
    if report.get("reason") == "dmg_mount_failed":
        return "Disk image did not mount cleanly in the guest VM."
    if report.get("reason") == "app_launch_failed":
        return "Guest launch command failed before the app became interactive."
    if report.get("crashed"):
        summary = report.get("crash_summary") or {}
        exception = summary.get("exception")
        top_frame = summary.get("symbolicated_top_frame") or summary.get("top_frame")
        if exception and top_frame:
            return f"Crash detected: {exception}. Top frame: {top_frame}"
        if exception:
            return f"Crash detected: {exception}."
        return "Crash detected after launch."
    failed_assertions = [item for item in report.get("assertions", []) if not item.get("ok")]
    if failed_assertions:
        return failed_assertions[0].get("message", "An assertion failed after launch.")
    if report.get("launched"):
        return "No crash detected. App launched successfully."
    return "App did not launch successfully."


def _next_actions_for_report(report: dict[str, Any]) -> list[str]:
    if report.get("crashed"):
        summary = report.get("crash_summary") or {}
        next_actions: list[str] = []
        if summary.get("symbolicated_top_frame") or summary.get("top_frame"):
            next_actions.append(
                f"Inspect the crashing code path around {summary.get('symbolicated_top_frame') or summary.get('top_frame')}."
            )
        if summary.get("exception"):
            next_actions.append(f"Validate the conditions that trigger {summary['exception']}.")
        next_actions.append("Review the collected system log around the crash timestamp.")
        return next_actions

    failed_assertions = [item for item in report.get("assertions", []) if not item.get("ok")]
    if failed_assertions:
        return [item["message"] for item in failed_assertions]

    if report.get("reason") == "pkg_install_failed":
        return [
            "Inspect the installer output and postinstall log artifact.",
            "Verify the guest admin account can run installer with the current sudo policy.",
        ]

    return ["Promote this artifact to the next validation stage."]


def build_report(
    *,
    vm_name: str,
    image: str,
    app_path: str,
    launched: bool,
    crashed: bool,
    screenshot: str | None,
    logs: str,
    crash_reports: list[str],
    warnings: list[str] | None = None,
    reason: str | None = None,
    assertions: list[AssertionResult] | None = None,
    artifact_type: str = ".app",
    profile: str | None = None,
    extra: dict[str, Any] | None = None,
    dsym_path: str | None = None,
) -> dict[str, Any]:
    manager = _run_manager()
    run_id = _latest_run_id_for_vm(vm_name)
    run_dir = manager.run_dir(run_id)
    crash_summary = _symbolicate_crash_summary(vm_name, _parse_crash_summary(crash_reports), dsym_path)
    payload: dict[str, Any] = {
        "run_id": run_id,
        "app": Path(app_path).name,
        "app_path": app_path,
        "image": image,
        "profile": profile,
        "artifact_type": artifact_type,
        "launched": launched,
        "crashed": crashed,
        "verdict": "failed" if crashed or not launched else "passed",
        "reason": reason or ("app_crashed" if crashed else "passed" if launched else "app_launch_failed"),
        "screenshot": screenshot,
        "logs": logs,
        "crash_reports": crash_reports,
        "warnings": warnings or [],
        "assertions": [item.to_dict() for item in assertions or []],
        "crash_summary": crash_summary,
        "artifacts_dir": str(run_dir),
    }
    if extra:
        payload.update(extra)
    payload["diagnosis"] = _diagnosis_for_report(payload)
    payload["next_actions"] = _next_actions_for_report(payload)
    return persist_report(vm_name, payload)


def _image_for_vm(vm_name: str) -> str:
    manager = _run_manager()
    metadata = manager.find_latest_run_for_vm(vm_name)
    return metadata.image if metadata is not None else "unknown"


def run_app_smoke(
    *,
    vm: str,
    app_path: str,
    timeout: int,
    dsym_path: str | None = None,
    profile: str | None = None,
) -> RunAppResult:
    validate_vm_name(vm)
    guest_app = validate_guest_path(app_path)
    _wait_for_guest_ready(vm, timeout=60)
    session = _guest_session(vm)
    manager = _run_manager()
    image = _image_for_vm(vm)

    if not session.remote_is_directory(guest_app):
        report = build_report(
            vm_name=vm,
            image=image,
            app_path=guest_app,
            launched=False,
            crashed=False,
            screenshot=None,
            logs="",
            crash_reports=[],
            reason="app_not_found",
            artifact_type=".app",
            profile=profile,
            dsym_path=dsym_path,
        )
        raise AppError(
            f"App not found in guest: {guest_app}",
            details={"app_path": guest_app, "report": report},
        )

    before = guest_crash_basenames(session)
    launch = session.exec(f"open {shlex.quote(guest_app)}", timeout=30)
    if launch.exit_code != 0:
        report = build_report(
            vm_name=vm,
            image=image,
            app_path=guest_app,
            launched=False,
            crashed=False,
            screenshot=None,
            logs="",
            crash_reports=[],
            reason="app_launch_failed",
            artifact_type=".app",
            profile=profile,
            dsym_path=dsym_path,
            extra={
                "launch_stdout": launch.stdout,
                "launch_stderr": launch.stderr,
            },
        )
        raise AppError(
            f"Failed to launch app: {guest_app}",
            details={"stdout": launch.stdout, "stderr": launch.stderr, "report": report},
        )

    time.sleep(timeout)

    screenshot_path, screenshot_warning = capture_screenshot(session, manager, vm)
    log_path = collect_logs(session, manager, vm)
    after = guest_crash_basenames(session)
    new_crashes = detect_new_crashes(before, after)
    crash_paths = download_crash_reports(session, manager, vm, new_crashes)

    warnings = [screenshot_warning] if screenshot_warning else []
    report = build_report(
        vm_name=vm,
        image=image,
        app_path=guest_app,
        launched=True,
        crashed=bool(new_crashes),
        screenshot=str(screenshot_path) if screenshot_path else None,
        logs=str(log_path),
        crash_reports=[str(p) for p in crash_paths],
        warnings=warnings,
        artifact_type=".app",
        profile=profile,
        dsym_path=dsym_path,
    )
    result = RunAppResult(
        launched=True,
        crashed=bool(new_crashes),
        app_path=guest_app,
        screenshot=str(screenshot_path) if screenshot_path else None,
        logs=str(log_path),
        crash_reports=[str(p) for p in crash_paths],
        warnings=warnings,
        report=report,
    )
    if new_crashes:
        raise AppCrashedError(
            "The app crashed after launch.",
            details={
                "launched": result.launched,
                "crashed": result.crashed,
                "app_path": result.app_path,
                "screenshot": result.screenshot,
                "logs": result.logs,
                "crash_reports": result.crash_reports,
                "report": report,
            },
        )
    return result


def destroy_sandbox(*, vm: str) -> None:
    config = load_config()
    validate_disposable_vm_operation(vm, config, operation="destroy")
    backend = _backend()
    backend.stop(vm)
    backend.delete(vm)
    manager = _run_manager()
    manager.update_status(vm, "destroyed")


def make_demo_vm_name(prefix: str = "macbox-demo") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _find_apps_in_remote_dir(session: GuestSession, remote_dir: str) -> list[str]:
    quoted = shlex.quote(remote_dir)
    result = session.exec(
        f"find {quoted} -maxdepth 2 -name '*.app' -print 2>/dev/null | sort",
        timeout=60,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def mount_dmg(*, vm: str, dmg_path: str) -> MountResult:
    validate_vm_name(vm)
    guest_dmg = validate_guest_path(dmg_path)
    session = _guest_session(vm)
    if not session.remote_path_exists(guest_dmg):
        raise AppError(
            f"DMG not found in guest: {guest_dmg}",
            details={"dmg_path": guest_dmg},
        )

    result = session.exec(f"hdiutil attach -nobrowse -readonly {shlex.quote(guest_dmg)}", timeout=180)
    if result.exit_code != 0:
        raise AppError(
            f"Failed to mount DMG: {guest_dmg}",
            details={"stdout": result.stdout, "stderr": result.stderr, "dmg_path": guest_dmg},
        )

    mount_point = None
    for line in result.stdout.splitlines():
        if "/Volumes/" in line:
            mount_point = line.split()[-1]
    if not mount_point:
        raise AppError(
            "DMG mounted but no /Volumes mount point was detected.",
            details={"stdout": result.stdout, "dmg_path": guest_dmg},
        )

    apps = _find_apps_in_remote_dir(session, mount_point)
    return MountResult(
        dmg_path=guest_dmg,
        mount_point=mount_point,
        volume_name=Path(mount_point).name,
        app_candidates=apps,
    )


def install_dmg_app(
    *,
    vm: str,
    app_name: str,
    dmg_path: str | None = None,
    destination_dir: str = "/Applications",
) -> InstallResult:
    validate_vm_name(vm)
    session = _guest_session(vm)
    mount = mount_dmg(vm=vm, dmg_path=dmg_path) if dmg_path else None
    if mount:
        candidates = mount.app_candidates
    else:
        candidates = [
            line.strip()
            for line in session.exec(
                f"find /Volumes -maxdepth 3 -name {shlex.quote(app_name)} -print 2>/dev/null | sort",
                timeout=60,
            ).stdout.splitlines()
            if line.strip()
        ]

    selected = next((path for path in candidates if Path(path).name == app_name), None)
    if selected is None:
        raise AppError(
            f"App {app_name!r} not found in mounted DMG volumes.",
            details={"app_name": app_name, "candidates": candidates},
        )

    guest_destination = validate_guest_path(f"{destination_dir.rstrip('/')}/{app_name}")
    result = session.exec(
        f"ditto {shlex.quote(selected)} {shlex.quote(guest_destination)}",
        timeout=300,
    )
    if result.exit_code != 0:
        raise AppError(
            f"Failed to install app from DMG: {app_name}",
            details={"stdout": result.stdout, "stderr": result.stderr, "source": selected},
        )

    return InstallResult(
        artifact_path=dmg_path or selected,
        app_path=guest_destination,
        mount_point=mount.mount_point if mount else None,
        new_apps=[guest_destination],
    )


def _list_remote_paths(session: GuestSession, remote_dir: str, pattern: str) -> list[str]:
    quoted = shlex.quote(remote_dir)
    result = session.exec(
        f"find {quoted} -maxdepth 1 -name {shlex.quote(pattern)} -print 2>/dev/null | sort",
        timeout=60,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _list_pkg_ids(session: GuestSession) -> set[str]:
    result = session.exec("pkgutil --pkgs", timeout=120)
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def install_pkg(
    *,
    vm: str,
    pkg_path: str,
    app_name: str | None = None,
    timeout: int = 600,
    dsym_path: str | None = None,
    profile: str | None = None,
) -> InstallResult:
    validate_vm_name(vm)
    guest_pkg = validate_guest_path(pkg_path)
    session = _guest_session(vm)
    manager = _run_manager()
    image = _image_for_vm(vm)

    before_apps = set(_list_remote_paths(session, "/Applications", "*.app"))
    before_agents = set(_list_remote_paths(session, "/Library/LaunchAgents", "*.plist"))
    before_daemons = set(_list_remote_paths(session, "/Library/LaunchDaemons", "*.plist"))
    before_pkg_ids = _list_pkg_ids(session)

    install = session.exec(
        f"sudo -n installer -pkg {shlex.quote(guest_pkg)} -target /",
        timeout=timeout,
    )

    postinstall = session.exec("tail -n 200 /var/log/install.log", timeout=120)
    log_artifact = _write_diagnostic_text(manager, vm, "postinstall.log", postinstall.stdout)

    after_apps = set(_list_remote_paths(session, "/Applications", "*.app"))
    after_agents = set(_list_remote_paths(session, "/Library/LaunchAgents", "*.plist"))
    after_daemons = set(_list_remote_paths(session, "/Library/LaunchDaemons", "*.plist"))
    after_pkg_ids = _list_pkg_ids(session)

    new_apps = sorted(after_apps - before_apps)
    launch_agents = sorted(after_agents - before_agents)
    launch_daemons = sorted(after_daemons - before_daemons)
    new_pkg_ids = sorted(after_pkg_ids - before_pkg_ids)

    installed_files: dict[str, list[str]] = {}
    for pkg_id in new_pkg_ids:
        files_result = session.exec(f"pkgutil --files {shlex.quote(pkg_id)}", timeout=120)
        installed_files[pkg_id] = [
            line.strip() for line in files_result.stdout.splitlines() if line.strip()
        ]
    if installed_files:
        installed_files_path = manager.artifact_path(vm, "diagnostics", "installed-files.json")
        installed_files_path.write_text(json.dumps(installed_files, indent=2) + "\n", encoding="utf-8")
        manager.register_artifact(vm, "diagnostics", installed_files_path)

    extra = {
        "installer_exit_code": install.exit_code,
        "installer_stdout": install.stdout,
        "installer_stderr": install.stderr,
        "new_apps": new_apps,
        "launch_agents": launch_agents,
        "launch_daemons": launch_daemons,
        "installed_files": installed_files,
        "postinstall_logs": str(log_artifact),
    }

    if install.exit_code != 0:
        report = build_report(
            vm_name=vm,
            image=image,
            app_path=guest_pkg,
            launched=False,
            crashed=False,
            screenshot=None,
            logs="",
            crash_reports=[],
            reason="pkg_install_failed",
            artifact_type=".pkg",
            profile=profile,
            extra=extra,
            dsym_path=dsym_path,
        )
        return InstallResult(
            artifact_path=guest_pkg,
            app_path="",
            install_exit_code=install.exit_code,
            new_apps=new_apps,
            launch_agents=launch_agents,
            launch_daemons=launch_daemons,
            installed_files=installed_files,
            postinstall_logs=str(log_artifact),
            report=report,
        )

    resolved_app = None
    if app_name:
        resolved_app = f"/Applications/{app_name}"
    elif new_apps:
        resolved_app = new_apps[0]

    combined_report: dict[str, Any]
    launched = False
    crashed = False
    screenshot = None
    logs = ""
    crash_reports: list[str] = []
    if resolved_app:
        try:
            smoke = run_app_smoke(
                vm=vm,
                app_path=resolved_app,
                timeout=max(30, min(timeout, 180)),
                dsym_path=dsym_path,
                profile=profile,
            )
            launched = smoke.launched
            crashed = smoke.crashed
            screenshot = smoke.screenshot
            logs = smoke.logs
            crash_reports = smoke.crash_reports
            combined_report = dict(smoke.report)
        except AppCrashedError as exc:
            launched = True
            crashed = True
            screenshot = exc.details.get("screenshot")
            logs = str(exc.details.get("logs", ""))
            crash_reports = list(exc.details.get("crash_reports", []))
            combined_report = dict(exc.details.get("report", {}))
    else:
        combined_report = build_report(
            vm_name=vm,
            image=image,
            app_path=guest_pkg,
            launched=False,
            crashed=False,
            screenshot=None,
            logs="",
            crash_reports=[],
            reason="pkg_install_completed_no_app",
            artifact_type=".pkg",
            profile=profile,
            extra=extra,
            dsym_path=dsym_path,
        )

    combined_report.update(extra)
    combined_report["artifact_type"] = ".pkg"
    combined_report["app_path"] = resolved_app or ""
    combined_report["launched"] = launched
    combined_report["crashed"] = crashed
    combined_report["screenshot"] = screenshot
    combined_report["logs"] = logs
    combined_report["crash_reports"] = crash_reports
    if not combined_report.get("reason"):
        combined_report["reason"] = "app_crashed" if crashed else "passed" if launched else "pkg_install_completed_no_app"
    combined_report["diagnosis"] = _diagnosis_for_report(combined_report)
    combined_report["next_actions"] = _next_actions_for_report(combined_report)
    combined_report = persist_report(vm, combined_report)

    return InstallResult(
        artifact_path=guest_pkg,
        app_path=resolved_app or "",
        install_exit_code=install.exit_code,
        new_apps=new_apps,
        launch_agents=launch_agents,
        launch_daemons=launch_daemons,
        installed_files=installed_files,
        postinstall_logs=str(log_artifact),
        report=combined_report,
    )


def run_installed_app(
    *,
    vm: str,
    app_name: str,
    timeout: int,
    dsym_path: str | None = None,
    profile: str | None = None,
) -> RunAppResult:
    candidate_paths = [f"/Applications/{app_name}", f"/Users/admin/Applications/{app_name}"]
    session = _guest_session(vm)
    guest_app = next((path for path in candidate_paths if session.remote_is_directory(path)), None)
    if guest_app is None:
        image = _image_for_vm(vm)
        report = build_report(
            vm_name=vm,
            image=image,
            app_path=f"/Applications/{app_name}",
            launched=False,
            crashed=False,
            screenshot=None,
            logs="",
            crash_reports=[],
            reason="app_not_found",
            artifact_type=".app",
            profile=profile,
            dsym_path=dsym_path,
            extra={"candidates": candidate_paths},
        )
        raise AppError(
            f"Installed app not found: {app_name}",
            details={"candidates": candidate_paths, "report": report},
        )
    return run_app_smoke(vm=vm, app_path=guest_app, timeout=timeout, dsym_path=dsym_path, profile=profile)


def assert_window_contains(*, vm: str, contains: str, app_name: str | None = None) -> AssertionResult:
    validate_vm_name(vm)
    session = _guest_session(vm)
    if app_name:
        command = (
            "osascript <<'APPLESCRIPT'\n"
            "tell application \"System Events\"\n"
            f"  if not (exists application process \"{app_name}\") then return \"\"\n"
            f"  tell application process \"{app_name}\"\n"
            "    set titleLines to {}\n"
            "    repeat with w in windows\n"
            "      try\n"
            "        copy (name of w) to end of titleLines\n"
            "      on error\n"
            "        copy \"\" to end of titleLines\n"
            "      end try\n"
            "    end repeat\n"
            "  end tell\n"
            "  set AppleScript's text item delimiters to linefeed\n"
            "  return titleLines as text\n"
            "end tell\n"
            "APPLESCRIPT"
        )
    else:
        command = (
            "osascript <<'APPLESCRIPT'\n"
            "tell application \"System Events\"\n"
            "  set titleLines to {}\n"
            "  repeat with proc in application processes\n"
            "    set procName to name of proc\n"
            "    try\n"
            "      repeat with w in windows of proc\n"
            "        try\n"
            "          copy (procName & \"::\" & name of w) to end of titleLines\n"
            "        on error\n"
            "          copy procName to end of titleLines\n"
            "        end try\n"
            "      end repeat\n"
            "    end try\n"
            "  end repeat\n"
            "  set AppleScript's text item delimiters to linefeed\n"
            "  return titleLines as text\n"
            "end tell\n"
            "APPLESCRIPT"
        )
    result = session.exec(command, timeout=45)
    titles = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    ok = any(contains.lower() in title.lower() for title in titles)
    scope = f" for {app_name}" if app_name else ""
    message = (
        f"Found window containing {contains!r}{scope}."
        if ok
        else f"Did not find a window containing {contains!r}{scope}."
    )
    return AssertionResult(
        kind="window_contains",
        ok=ok,
        message=message,
        details={"contains": contains, "app_name": app_name, "titles": titles[:25]},
    )


def assert_app_running(*, vm: str, bundle_id: str) -> AssertionResult:
    validate_vm_name(vm)
    session = _guest_session(vm)
    lookup = shlex.quote(f"bundleID={bundle_id}")
    find_result = session.exec(f"lsappinfo find {lookup}", timeout=30)
    match = find_result.stdout.strip()
    if find_result.exit_code == 0 and match:
        return AssertionResult(
            kind="app_running",
            ok=True,
            message=f"Bundle {bundle_id} is running.",
            details={"bundle_id": bundle_id, "match": match},
        )

    discover = session.exec(
        f"mdfind \"kMDItemCFBundleIdentifier == '{bundle_id}'\" | head -n 5",
        timeout=60,
    )
    candidates = [line.strip() for line in discover.stdout.splitlines() if line.strip()]
    candidate_names = [Path(path).stem for path in candidates]
    running_process_names: list[str] = []
    for name in candidate_names:
        probe = session.exec(f"pgrep -ix {shlex.quote(name)}", timeout=15)
        if probe.exit_code == 0:
            running_process_names.append(name)

    return AssertionResult(
        kind="app_running",
        ok=False,
        message=f"Bundle {bundle_id} is not running.",
        details={
            "bundle_id": bundle_id,
            "lsappinfo_match": match,
            "candidate_paths": candidates,
            "matched_process_names": running_process_names,
            "lsappinfo_stderr": find_result.stderr.strip(),
        },
    )


def _evaluate_gate_requirements(report: dict[str, Any], requirements: list[str]) -> list[str]:
    failed: list[str] = []
    assertions = report.get("assertions", [])
    for requirement in requirements:
        if requirement == "launch" and not report.get("launched"):
            failed.append(requirement)
        elif requirement == "no-crash" and report.get("crashed"):
            failed.append(requirement)
        elif requirement == "screenshot" and not report.get("screenshot"):
            failed.append(requirement)
        elif requirement == "no-new-crash-report" and report.get("crash_reports"):
            failed.append(requirement)
        elif requirement == "app-running":
            if not any(item.get("kind") == "app_running" and item.get("ok") for item in assertions):
                failed.append(requirement)
        elif requirement.startswith("window:"):
            needle = requirement.split(":", maxsplit=1)[1]
            if not any(
                item.get("kind") == "window_contains"
                and item.get("ok")
                and item.get("details", {}).get("contains") == needle
                for item in assertions
            ):
                failed.append(requirement)
    return failed


def _apply_assertions(
    *,
    vm: str,
    report: dict[str, Any],
    bundle_id: str | None = None,
    window_contains: list[str] | None = None,
    app_name: str | None = None,
) -> dict[str, Any]:
    assertions: list[AssertionResult] = []
    existing = report.get("assertions", [])
    for item in existing:
        assertions.append(
            AssertionResult(
                kind=item.get("kind", "unknown"),
                ok=bool(item.get("ok")),
                message=str(item.get("message", "")),
                details=dict(item.get("details", {})),
            )
        )

    if bundle_id:
        assertions.append(assert_app_running(vm=vm, bundle_id=bundle_id))
    for needle in window_contains or []:
        assertions.append(assert_window_contains(vm=vm, contains=needle, app_name=app_name))

    has_failed_assertion = any(not item.ok for item in assertions)
    report["assertions"] = [item.to_dict() for item in assertions]
    report["diagnosis"] = _diagnosis_for_report(report)
    report["next_actions"] = _next_actions_for_report(report)
    report["verdict"] = (
        "failed" if report.get("crashed") or not report.get("launched") or has_failed_assertion else "passed"
    )
    return persist_report(vm, report)


def run_release_gate(
    *,
    artifact_path: str | Path,
    image: str | None,
    requirements: list[str],
    app_name: str | None = None,
    bundle_id: str | None = None,
    window_contains: str | None = None,
    timeout: int | None = None,
    dsym_path: str | None = None,
    headless: bool = True,
    profile: str | None = None,
) -> GateResult:
    config = load_config()
    run_timeout = timeout if timeout is not None else config.run_app_timeout_seconds
    local_artifact = validate_upload_path(artifact_path, mcp_mode=False)
    artifact_type = _artifact_type(local_artifact)
    required_window_needles = [
        requirement.split(":", maxsplit=1)[1]
        for requirement in requirements
        if requirement.startswith("window:")
    ]
    explicit_window_needles = [window_contains] if window_contains else []
    window_needles = list(dict.fromkeys([*required_window_needles, *explicit_window_needles]))
    if "app-running" in requirements and not bundle_id:
        raise SafetyError(
            "The app-running release-gate requirement needs --bundle-id.",
            details={"requirements": requirements},
        )
    vm = make_demo_vm_name(prefix="macbox-gate")
    start_info: StartResult | None = None
    report: dict[str, Any] = {}
    warnings: list[str] = []
    errors: list[MacboxError] = []
    destroyed = False

    try:
        start_info = start_sandbox(image=image, name=vm, headless=headless, profile=profile)
        uploaded = upload_artifact_to_guest(vm=vm, local_path=local_artifact)
        if artifact_type == ".app":
            smoke = run_app_smoke(
                vm=vm,
                app_path=uploaded.guest_path,
                timeout=run_timeout,
                dsym_path=dsym_path,
                profile=profile,
            )
            report = dict(smoke.report)
            warnings.extend(smoke.warnings)
            resolved_app_name = Path(uploaded.guest_path).name
        elif artifact_type == ".dmg":
            app_label = app_name
            if app_label is None:
                mount = mount_dmg(vm=vm, dmg_path=uploaded.guest_path)
                if not mount.app_candidates:
                    report = build_report(
                        vm_name=vm,
                        image=start_info.image,
                        app_path=uploaded.guest_path,
                        launched=False,
                        crashed=False,
                        screenshot=None,
                        logs="",
                        crash_reports=[],
                        reason="dmg_mount_failed",
                        artifact_type=".dmg",
                        profile=profile,
                        extra={"mount_point": mount.mount_point, "app_candidates": mount.app_candidates},
                        dsym_path=dsym_path,
                    )
                    resolved_app_name = None
                else:
                    app_label = Path(mount.app_candidates[0]).name
                    resolved_app_name = app_label
            else:
                resolved_app_name = app_label

            if report:
                pass
            else:
                install = install_dmg_app(vm=vm, dmg_path=uploaded.guest_path, app_name=app_label or "")
                smoke = run_app_smoke(
                    vm=vm,
                    app_path=install.app_path,
                    timeout=run_timeout,
                    dsym_path=dsym_path,
                    profile=profile,
                )
                report = dict(smoke.report)
                report["artifact_type"] = ".dmg"
                report["mount_point"] = install.mount_point
                report["new_apps"] = install.new_apps
                report = persist_report(vm, report)
                warnings.extend(smoke.warnings)
        elif artifact_type == ".pkg":
            install = install_pkg(
                vm=vm,
                pkg_path=uploaded.guest_path,
                app_name=app_name,
                timeout=max(run_timeout, 300),
                dsym_path=dsym_path,
                profile=profile,
            )
            report = dict(install.report)
            resolved_app_name = Path(install.app_path).name if install.app_path else app_name
        else:
            raise SafetyError(
                f"Unsupported release artifact type: {local_artifact}",
                details={"artifact_path": str(local_artifact), "artifact_type": artifact_type},
            )

        report = _apply_assertions(
            vm=vm,
            report=report,
            bundle_id=bundle_id,
            window_contains=window_needles,
            app_name=locals().get("resolved_app_name"),
        )
        failed_requirements = _evaluate_gate_requirements(report, requirements)
        report["gate"] = {
            "requirements": requirements,
            "failed_requirements": failed_requirements,
            "passed": not failed_requirements,
        }
        report["verdict"] = "passed" if not failed_requirements else "failed"
        report = persist_report(vm, report)
    except AppCrashedError as exc:
        report = dict(exc.details.get("report", {}))
        report = _apply_assertions(
            vm=vm,
            report=report,
            bundle_id=bundle_id,
            window_contains=window_needles,
            app_name=locals().get("resolved_app_name", app_name),
        )
        failed_requirements = _evaluate_gate_requirements(report, requirements)
        report["gate"] = {
            "requirements": requirements,
            "failed_requirements": failed_requirements,
            "passed": False,
        }
        report["verdict"] = "failed"
        report = persist_report(vm, report)
        errors.append(exc)
    except MacboxError as exc:
        if isinstance(exc.details, dict) and isinstance(exc.details.get("report"), dict):
            report = dict(exc.details["report"])
        errors.append(exc)
        failed_requirements = _evaluate_gate_requirements(report, requirements) if report else (requirements[:] if requirements else [])
        if report:
            report["gate"] = {
                "requirements": requirements,
                "failed_requirements": failed_requirements,
                "passed": False,
            }
            report["verdict"] = "failed"
            report = persist_report(vm, report)
    finally:
        try:
            destroy_sandbox(vm=vm)
            destroyed = True
        except MacboxError as exc:
            errors.append(exc)

    ok = bool(report) and not failed_requirements and destroyed and not [
        err for err in errors if not isinstance(err, AppCrashedError)
    ]
    return GateResult(
        ok=ok,
        vm=vm,
        image=start_info.image if start_info else (image or profile or load_config().default_image),
        run_id=start_info.run_id if start_info else "",
        run_dir=start_info.run_dir if start_info else "",
        artifact_path=str(local_artifact),
        artifact_type=artifact_type,
        report=report,
        failed_requirements=failed_requirements,
        destroyed=destroyed,
        warnings=warnings,
        errors=errors,
    )


def run_matrix(
    *,
    images: list[str],
    artifact_path: str | Path,
    requirements: list[str],
    app_name: str | None = None,
    bundle_id: str | None = None,
    window_contains: str | None = None,
    timeout: int | None = None,
    dsym_path: str | None = None,
    headless: bool = True,
) -> tuple[bool, list[MatrixEntry]]:
    results: list[MatrixEntry] = []
    overall_ok = True
    for image in images:
        gate = run_release_gate(
            artifact_path=artifact_path,
            image=image,
            requirements=requirements,
            app_name=app_name,
            bundle_id=bundle_id,
            window_contains=window_contains,
            timeout=timeout,
            dsym_path=dsym_path,
            headless=headless,
        )
        report = gate.report
        launched = bool(report.get("launched"))
        crashed = bool(report.get("crashed"))
        ok = gate.ok
        overall_ok = overall_ok and ok
        results.append(
            MatrixEntry(
                image=image,
                ok=ok,
                launched=launched,
                crashed=crashed,
                failed_requirements=gate.failed_requirements,
                report_path=report.get("report_path"),
                crash_reports=list(report.get("crash_reports", [])),
            )
        )
    return overall_ok, results


def run_demo(
    *,
    app_path: str | Path,
    image: str | None = None,
    timeout: int | None = None,
    headless: bool = True,
    profile: str | None = None,
) -> DemoResult:
    config = load_config()
    run_timeout = timeout if timeout is not None else config.run_app_timeout_seconds
    local_app = expand_path(app_path)
    vm = make_demo_vm_name()

    if not local_app.exists():
        raise SafetyError(
            f"App path does not exist: {local_app}",
            details={"path": str(local_app)},
        )

    guest_app = f"/Users/admin/Desktop/{local_app.name}"
    start_info: StartResult | None = None
    upload_info: UploadResult | None = None
    smoke: RunAppResult | None = None
    errors: list[MacboxError] = []
    warnings: list[str] = []
    destroyed = False
    report: dict[str, Any] = {}

    try:
        start_info = start_sandbox(image=image, name=vm, headless=headless, profile=profile)
        upload_info = upload_app_to_guest(vm=vm, local_path=local_app, guest_path=guest_app)
        try:
            smoke = run_app_smoke(vm=vm, app_path=guest_app, timeout=run_timeout, profile=profile)
            warnings.extend(smoke.warnings)
            report = smoke.report
        except AppCrashedError as exc:
            smoke = RunAppResult(
                launched=True,
                crashed=True,
                app_path=guest_app,
                screenshot=exc.details.get("screenshot"),
                logs=str(exc.details.get("logs", "")),
                crash_reports=list(exc.details.get("crash_reports", [])),
                warnings=warnings,
                report=dict(exc.details.get("report", {})),
            )
            report = smoke.report
            errors.append(exc)
    except MacboxError as exc:
        errors.append(exc)
    finally:
        try:
            destroy_sandbox(vm=vm)
            destroyed = True
        except MacboxError as exc:
            errors.append(exc)

    ok = (
        smoke is not None
        and smoke.launched
        and not smoke.crashed
        and destroyed
        and not [e for e in errors if not isinstance(e, AppCrashedError)]
    )
    return DemoResult(
        ok=ok,
        vm=vm,
        image=start_info.image if start_info else (image or config.default_image),
        run_id=start_info.run_id if start_info else "",
        run_dir=start_info.run_dir if start_info else "",
        local_app=str(local_app),
        guest_app=upload_info.guest_path if upload_info else guest_app,
        launched=smoke.launched if smoke else False,
        crashed=smoke.crashed if smoke else False,
        screenshot=smoke.screenshot if smoke else None,
        logs=smoke.logs if smoke else "",
        crash_reports=smoke.crash_reports if smoke else [],
        destroyed=destroyed,
        warnings=warnings,
        errors=errors,
        report=report,
    )

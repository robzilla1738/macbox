"""macbox CLI."""

from __future__ import annotations

import shlex
import sys
import time
from pathlib import Path
from typing import Any

import click

from macbox.config import ensure_state_layout, get_state_dir, load_config
from macbox.errors import MacboxError
from macbox.models import DoctorCheck, ErrorDetail, MacboxResponse, expand_path
from macbox.runner import run_command
from macbox.runs import (
    CRASH_SUFFIXES,
    RunManager,
    detect_new_crashes,
    list_crash_basenames,
)
from macbox.safety import (
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


def emit(response: MacboxResponse, exit_code: int = 0, *, as_json: bool = True) -> None:
    if as_json:
        click.echo(response.model_dump_json(indent=2))
    else:
        click.echo(response.model_dump_json(indent=2))
    raise SystemExit(exit_code)


def success(
    command: str,
    *,
    vm: str | None = None,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> MacboxResponse:
    return MacboxResponse(
        ok=True,
        command=command,
        vm=vm,
        data=data or {},
        warnings=warnings or [],
        errors=[],
    )


def failure(
    command: str,
    *,
    vm: str | None = None,
    errors: list[MacboxError | ErrorDetail],
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> MacboxResponse:
    details: list[ErrorDetail] = []
    for err in errors:
        if isinstance(err, MacboxError):
            details.append(ErrorDetail(**err.to_dict()))
        else:
            details.append(err)
    return MacboxResponse(
        ok=False,
        command=command,
        vm=vm,
        data=data or {},
        warnings=warnings or [],
        errors=details,
    )


def handle_errors(command: str, vm: str | None, fn, *, as_json: bool = True):
    try:
        return fn()
    except MacboxError as exc:
        emit(failure(command, vm=vm, errors=[exc]), exit_code=1, as_json=as_json)


@click.group()
def main() -> None:
    """macbox — local macOS sandbox runner."""


@main.command("status")
@click.option("--json", "as_json", is_flag=True, default=False)
def status_cmd(as_json: bool) -> None:
    def run():
        ensure_state_layout()
        backend = _backend()
        vms = backend.list_vms()
        config = load_config()
        emit(
            success(
                "status",
                data={
                    "state_dir": str(get_state_dir(config)),
                    "backend": "tart",
                    "vms": [vm.model_dump() for vm in vms],
                    "vm_count": len(vms),
                },
            ),
            as_json=as_json,
        )

    handle_errors("status", None, run, as_json=as_json)


@main.command("images")
@click.option("--json", "as_json", is_flag=True, default=False)
def images_cmd(as_json: bool) -> None:
    def run():
        backend = _backend()
        vms = backend.list_vms()
        emit(
            success(
                "images",
                data={"images": [vm.model_dump() for vm in vms]},
            ),
            as_json=as_json,
        )

    handle_errors("images", None, run, as_json=as_json)


@main.command("prepare")
@click.option("--image", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def prepare_cmd(image: str, as_json: bool) -> None:
    def run():
        backend = _backend()
        exists = backend.vm_exists(image)
        data: dict[str, Any] = {
            "image": image,
            "exists_locally": exists,
            "checklist": [
                "Clone base image locally (tart clone <source> <image>)",
                "Enable Remote Login in guest Sharing settings",
                "Configure auto-login for admin user",
                "Disable lock screen password requirement",
                "Install host SSH public key into guest authorized_keys",
                "Verify: ssh -i ~/.ssh/macbox_id admin@$(tart ip <image>)",
            ],
        }
        if not exists:
            data["hint"] = (
                f"Image {image!r} not found locally. Clone it before starting sandboxes."
            )
        emit(success("prepare", data=data), as_json=as_json)

    handle_errors("prepare", None, run, as_json=as_json)


@main.command("start")
@click.option("--image", required=True)
@click.option("--name", required=True)
@click.option("--headless", is_flag=True, default=False)
@click.option("--json", "as_json", is_flag=True, default=False)
def start_cmd(image: str, name: str, headless: bool, as_json: bool) -> None:
    def run():
        config = load_config()
        validate_start_vm(image, name, config)
        backend = _backend()
        if not backend.vm_exists(name):
            backend.clone(image, name)
        backend.run(name, headless=headless)
        backend.ip(name)
        manager = _run_manager()
        metadata = manager.create_run(name, image)
        emit(
            success(
                "start",
                vm=name,
                data={
                    "image": image,
                    "headless": headless,
                    "run_id": metadata.run_id,
                    "run_dir": str(manager.run_dir(metadata.run_id)),
                },
            ),
            as_json=as_json,
        )

    handle_errors("start", name, run, as_json=as_json)


@main.command("stop")
@click.option("--name", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def stop_cmd(name: str, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        backend = _backend()
        backend.stop(name)
        manager = _run_manager()
        manager.update_status(name, "stopped")
        emit(success("stop", vm=name, data={"stopped": True}), as_json=as_json)

    handle_errors("stop", name, run, as_json=as_json)


@main.command("destroy")
@click.option("--name", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def destroy_cmd(name: str, as_json: bool) -> None:
    def run():
        config = load_config()
        validate_disposable_vm_operation(name, config, operation="destroy")
        backend = _backend()
        backend.stop(name)
        backend.delete(name)
        manager = _run_manager()
        manager.update_status(name, "destroyed")
        emit(success("destroy", vm=name, data={"destroyed": True}), as_json=as_json)

    handle_errors("destroy", name, run, as_json=as_json)


@main.command("reset")
@click.option("--image", required=True)
@click.option("--name", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def reset_cmd(image: str, name: str, as_json: bool) -> None:
    def run():
        config = load_config()
        validate_start_vm(image, name, config)
        validate_disposable_vm_operation(name, config, operation="reset")
        backend = _backend()
        if backend.vm_exists(name):
            backend.stop(name)
            backend.delete(name)
        backend.clone(image, name)
        backend.run(name, headless=True)
        backend.ip(name)
        manager = _run_manager()
        metadata = manager.create_run(name, image)
        emit(
            success(
                "reset",
                vm=name,
                data={
                    "image": image,
                    "run_id": metadata.run_id,
                    "run_dir": str(manager.run_dir(metadata.run_id)),
                },
            ),
            as_json=as_json,
        )

    handle_errors("reset", name, run, as_json=as_json)


@main.command("upload")
@click.option("--name", required=True)
@click.option("--path", "local_path", required=True, type=click.Path())
@click.option("--dest", required=True)
@click.option("--allow-secret-path", is_flag=True, default=False)
@click.option("--json", "as_json", is_flag=True, default=False)
def upload_cmd(
    name: str,
    local_path: str,
    dest: str,
    allow_secret_path: bool,
    as_json: bool,
) -> None:
    def run():
        validate_vm_name(name)
        validated = validate_upload_path(
            local_path,
            allow_override=allow_secret_path,
            mcp_mode=False,
        )
        guest_dest = validate_guest_path(dest)
        session = _guest_session(name)
        session.upload(validated, guest_dest)
        manager = _run_manager()
        artifact = manager.artifact_path(name, "uploads", validated.name)
        manager.register_artifact(name, "uploads", artifact)
        emit(
            success(
                "upload",
                vm=name,
                data={
                    "local_path": str(validated),
                    "guest_path": guest_dest,
                },
            ),
            as_json=as_json,
        )

    handle_errors("upload", name, run, as_json=as_json)


@main.command("exec")
@click.option("--name", required=True)
@click.option("--command", required=True)
@click.option("--timeout", default=60, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def exec_cmd(name: str, command: str, timeout: int, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        cmd = validate_guest_command(command)
        session = _guest_session(name)
        result = session.exec(cmd, timeout=timeout)
        response = success(
            "exec",
            vm=name,
            data={
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        if result.exit_code != 0:
            emit(response, exit_code=result.exit_code, as_json=as_json)
        emit(response, as_json=as_json)

    handle_errors("exec", name, run, as_json=as_json)


@main.command("run-app")
@click.option("--name", required=True)
@click.option("--app", "app_path", required=True)
@click.option("--timeout", default=120, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def run_app_cmd(name: str, app_path: str, timeout: int, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        guest_app = validate_guest_path(app_path)
        session = _guest_session(name)
        manager = _run_manager()

        if not session.remote_is_directory(guest_app):
            from macbox.errors import AppError

            raise AppError(
                f"App not found in guest: {guest_app}",
                details={"app_path": guest_app},
            )

        before = _guest_crash_basenames(session)
        launch = session.exec(f"open {shlex.quote(guest_app)}", timeout=30)
        if launch.exit_code != 0:
            from macbox.errors import AppError

            raise AppError(
                f"Failed to launch app: {guest_app}",
                details={
                    "stdout": launch.stdout,
                    "stderr": launch.stderr,
                },
            )

        time.sleep(timeout)

        screenshot_path, screenshot_warning = _capture_screenshot(session, manager, name)
        log_path = _collect_logs(session, manager, name)
        after = _guest_crash_basenames(session)
        new_crashes = detect_new_crashes(before, after)
        crash_paths = _download_crash_reports(session, manager, name, new_crashes)

        data = {
            "launched": True,
            "crashed": bool(new_crashes),
            "app_path": guest_app,
            "screenshot": str(screenshot_path) if screenshot_path else None,
            "logs": str(log_path),
            "crash_reports": [str(p) for p in crash_paths],
        }
        warnings = [screenshot_warning] if screenshot_warning else []
        if new_crashes:
            from macbox.errors import AppCrashedError

            emit(
                failure(
                    "run-app",
                    vm=name,
                    data=data,
                    warnings=warnings,
                    errors=[AppCrashedError("The app crashed after launch.")],
                ),
                exit_code=1,
                as_json=as_json,
            )
        emit(success("run-app", vm=name, data=data, warnings=warnings), as_json=as_json)

    handle_errors("run-app", name, run, as_json=as_json)


@main.command("logs")
@click.option("--name", required=True)
@click.option("--last", "duration", default="5m", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def logs_cmd(name: str, duration: str, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        session = _guest_session(name)
        manager = _run_manager()
        log_path = _collect_logs(session, manager, name, duration=duration)
        emit(
            success(
                "logs",
                vm=name,
                data={"logs": str(log_path), "duration": duration},
            ),
            as_json=as_json,
        )

    handle_errors("logs", name, run, as_json=as_json)


@main.command("screenshot")
@click.option("--name", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def screenshot_cmd(name: str, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        session = _guest_session(name)
        manager = _run_manager()
        screenshot_path, warning = _capture_screenshot(session, manager, name)
        data = {"screenshot": str(screenshot_path) if screenshot_path else None}
        emit(success("screenshot", vm=name, data=data, warnings=[warning] if warning else []), as_json=as_json)

    handle_errors("screenshot", name, run, as_json=as_json)


@main.command("collect-crashes")
@click.option("--name", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def collect_crashes_cmd(name: str, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        session = _guest_session(name)
        manager = _run_manager()
        basenames = sorted(_guest_crash_basenames(session))
        paths = _download_crash_reports(session, manager, name, basenames)
        emit(
            success(
                "collect-crashes",
                vm=name,
                data={"crash_reports": [str(p) for p in paths]},
            ),
            as_json=as_json,
        )

    handle_errors("collect-crashes", name, run, as_json=as_json)


@main.command("doctor")
@click.option("--json", "as_json", is_flag=True, default=False)
def doctor_cmd(as_json: bool) -> None:
    def run():
        config = ensure_state_layout()
        checks: list[DoctorCheck] = []

        tart = run_command(["which", config.tart_path], timeout=10)
        checks.append(
            DoctorCheck(
                name="tart",
                ok=tart.exit_code == 0,
                message=tart.stdout.strip() or tart.stderr.strip() or "tart not found",
            )
        )

        for tool in ("ssh", "scp"):
            result = run_command(["which", tool], timeout=10)
            checks.append(
                DoctorCheck(
                    name=tool,
                    ok=result.exit_code == 0,
                    message=result.stdout.strip() or result.stderr.strip() or f"{tool} not found",
                )
            )

        identity = expand_path(config.ssh_identity_file)
        checks.append(
            DoctorCheck(
                name="ssh_identity",
                ok=identity.exists(),
                message=str(identity) if identity.exists() else f"Missing SSH identity: {identity}",
            )
        )

        state_dir = get_state_dir(config)
        checks.append(
            DoctorCheck(
                name="state_dir",
                ok=state_dir.exists(),
                message=str(state_dir),
            )
        )

        ok = all(check.ok for check in checks)
        emit(
            MacboxResponse(
                ok=ok,
                command="doctor",
                vm=None,
                data={"checks": [check.model_dump() for check in checks]},
                warnings=[],
                errors=[] if ok else [
                    ErrorDetail(
                        code="DOCTOR_FAILED",
                        message="One or more doctor checks failed",
                        details={},
                    )
                ],
            ),
            exit_code=0 if ok else 1,
            as_json=as_json,
        )

    handle_errors("doctor", None, run, as_json=as_json)


def _guest_crash_basenames(session: GuestSession) -> set[str]:
    files = session.list_remote_files(GUEST_CRASH_DIR)
    return list_crash_basenames(files)


def _collect_logs(
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


def _capture_screenshot(
    session: GuestSession,
    manager: RunManager,
    vm_name: str,
) -> tuple[Path | None, str | None]:
    remote = "/tmp/macbox-shot.png"
    result = session.exec(f"screencapture -x {shlex.quote(remote)}", timeout=30)
    if result.exit_code != 0:
        return None, f"Screenshot capture failed: {result.stderr.strip() or result.stdout.strip()}"
    local = manager.artifact_path(vm_name, "screenshots", "launch.png")
    try:
        session.download(remote, local)
    except MacboxError as exc:
        return None, exc.message
    manager.register_artifact(vm_name, "screenshots", local)
    return local, None


def _download_crash_reports(
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


if __name__ == "__main__":
    main()

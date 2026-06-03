"""macbox CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from macbox.config import ensure_state_layout, get_state_dir, load_config
from macbox.errors import AppCrashedError, MacboxError
from macbox.models import DoctorCheck, ErrorDetail, MacboxResponse, expand_path
from macbox.runner import run_command
from macbox.runs import RunManager
from macbox.safety import (
    validate_disposable_vm_operation,
    validate_guest_path,
    validate_vm_name,
)
from macbox.ssh import GuestSession
from macbox.tart_backend import TartBackend
from macbox.workflows import (
    assert_app_running,
    assert_window_contains,
    capture_screenshot,
    collect_logs,
    destroy_sandbox,
    download_crash_reports,
    guest_click,
    guest_exec_command,
    guest_crash_basenames,
    guest_send_keys,
    guest_type_text,
    install_dmg_app,
    install_pkg,
    list_profiles,
    list_guest_processes,
    list_guest_windows,
    load_report,
    mount_dmg,
    open_guest_app,
    pull_file_from_guest,
    push_file_to_guest,
    reset_sandbox,
    run_app_smoke,
    run_guest_applescript,
    run_guest_jxa,
    run_demo,
    run_installed_app,
    run_on_warm,
    run_matrix,
    run_release_gate,
    start_sandbox,
    upload_artifact_to_guest,
)


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


def _csv_items(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _gate_requirements(raw: str | None) -> list[str]:
    items = _csv_items(raw)
    return items or ["launch", "no-crash", "screenshot", "no-new-crash-report"]


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
        emit(success("images", data={"images": [vm.model_dump() for vm in vms]}), as_json=as_json)

    handle_errors("images", None, run, as_json=as_json)


@main.command("profiles")
@click.option("--json", "as_json", is_flag=True, default=False)
def profiles_cmd(as_json: bool) -> None:
    def run():
        emit(success("profiles", data={"profiles": list_profiles()}), as_json=as_json)

    handle_errors("profiles", None, run, as_json=as_json)


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
            data["hint"] = f"Image {image!r} not found locally. Clone it before starting sandboxes."
        emit(success("prepare", data=data), as_json=as_json)

    handle_errors("prepare", None, run, as_json=as_json)


@main.command("start")
@click.option("--image", default=None)
@click.option("--profile", default=None, help="Named sandbox profile or Tart image alias.")
@click.option("--name", required=True)
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def start_cmd(image: str | None, profile: str | None, name: str, headless: bool, as_json: bool) -> None:
    def run():
        started = start_sandbox(image=image, profile=profile, name=name, headless=headless)
        emit(
            success(
                "start",
                vm=name,
                data={
                    "image": started.image,
                    "profile": started.profile,
                    "headless": started.headless,
                    "run_id": started.run_id,
                    "run_dir": started.run_dir,
                },
            ),
            as_json=as_json,
        )

    handle_errors("start", name, run, as_json=as_json)


@main.command("warm")
@click.option("--image", default=None)
@click.option("--profile", default=None)
@click.option("--name", required=True)
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def warm_cmd(image: str | None, profile: str | None, name: str, headless: bool, as_json: bool) -> None:
    def run():
        started = start_sandbox(image=image, profile=profile, name=name, headless=headless)
        emit(
            success(
                "warm",
                vm=name,
                data={
                    "image": started.image,
                    "profile": started.profile,
                    "headless": started.headless,
                    "run_id": started.run_id,
                    "run_dir": started.run_dir,
                    "warm": True,
                },
            ),
            as_json=as_json,
        )

    handle_errors("warm", name, run, as_json=as_json)


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
        validate_disposable_vm_operation(name, load_config(), operation="destroy")
        destroy_sandbox(vm=name)
        emit(success("destroy", vm=name, data={"destroyed": True}), as_json=as_json)

    handle_errors("destroy", name, run, as_json=as_json)


@main.command("reset")
@click.option("--image", default=None)
@click.option("--profile", default=None)
@click.option("--name", required=True)
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def reset_cmd(
    image: str | None,
    profile: str | None,
    name: str,
    headless: bool,
    as_json: bool,
) -> None:
    def run():
        started = reset_sandbox(image=image, profile=profile, name=name, headless=headless)
        emit(
            success(
                "reset",
                vm=name,
                data={
                    "image": started.image,
                    "profile": started.profile,
                    "run_id": started.run_id,
                    "run_dir": started.run_dir,
                },
            ),
            as_json=as_json,
        )

    handle_errors("reset", name, run, as_json=as_json)


@main.command("reset-warm")
@click.option("--image", default=None)
@click.option("--profile", default=None)
@click.option("--name", required=True)
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def reset_warm_cmd(
    image: str | None,
    profile: str | None,
    name: str,
    headless: bool,
    as_json: bool,
) -> None:
    def run():
        started = reset_sandbox(image=image, profile=profile, name=name, headless=headless)
        emit(
            success(
                "reset-warm",
                vm=name,
                data={
                    "image": started.image,
                    "profile": started.profile,
                    "run_id": started.run_id,
                    "run_dir": started.run_dir,
                    "warm": True,
                },
            ),
            as_json=as_json,
        )

    handle_errors("reset-warm", name, run, as_json=as_json)


@main.command("upload")
@click.option("--name", required=True)
@click.option("--path", "local_path", required=True, type=click.Path())
@click.option("--dest", default=None)
@click.option("--json", "as_json", is_flag=True, default=False)
def upload_cmd(name: str, local_path: str, dest: str | None, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        uploaded = upload_artifact_to_guest(vm=name, local_path=local_path, guest_path=dest)
        emit(
            success(
                "upload",
                vm=name,
                data={
                    "local_path": uploaded.local_path,
                    "guest_path": uploaded.guest_path,
                    "artifact_type": uploaded.artifact_type,
                },
            ),
            as_json=as_json,
        )

    handle_errors("upload", name, run, as_json=as_json)


@main.command("upload-dmg")
@click.option("--name", required=True)
@click.option("--path", "local_path", required=True, type=click.Path())
@click.option("--dest", default=None)
@click.option("--json", "as_json", is_flag=True, default=False)
def upload_dmg_cmd(name: str, local_path: str, dest: str | None, as_json: bool) -> None:
    def run():
        uploaded = upload_artifact_to_guest(vm=name, local_path=local_path, guest_path=dest)
        emit(
            success(
                "upload-dmg",
                vm=name,
                data={
                    "local_path": uploaded.local_path,
                    "guest_path": uploaded.guest_path,
                    "artifact_type": uploaded.artifact_type,
                },
            ),
            as_json=as_json,
        )

    handle_errors("upload-dmg", name, run, as_json=as_json)


@main.command("mount-dmg")
@click.option("--name", required=True)
@click.option("--dmg", "dmg_path", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def mount_dmg_cmd(name: str, dmg_path: str, as_json: bool) -> None:
    def run():
        mounted = mount_dmg(vm=name, dmg_path=dmg_path)
        emit(
            success(
                "mount-dmg",
                vm=name,
                data={
                    "dmg_path": mounted.dmg_path,
                    "mount_point": mounted.mount_point,
                    "volume_name": mounted.volume_name,
                    "app_candidates": mounted.app_candidates,
                },
            ),
            as_json=as_json,
        )

    handle_errors("mount-dmg", name, run, as_json=as_json)


@main.command("install-dmg-app")
@click.option("--name", required=True)
@click.option("--app", "app_name", required=True)
@click.option("--dmg", "dmg_path", default=None)
@click.option("--destination-dir", default="/Applications", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def install_dmg_app_cmd(
    name: str,
    app_name: str,
    dmg_path: str | None,
    destination_dir: str,
    as_json: bool,
) -> None:
    def run():
        installed = install_dmg_app(
            vm=name,
            app_name=app_name,
            dmg_path=dmg_path,
            destination_dir=destination_dir,
        )
        emit(
            success(
                "install-dmg-app",
                vm=name,
                data={
                    "artifact_path": installed.artifact_path,
                    "app_path": installed.app_path,
                    "mount_point": installed.mount_point,
                    "new_apps": installed.new_apps,
                },
            ),
            as_json=as_json,
        )

    handle_errors("install-dmg-app", name, run, as_json=as_json)


@main.command("install-pkg")
@click.option("--name", required=True)
@click.option("--pkg", "pkg_path", required=True)
@click.option("--app", "app_name", default=None, help="Installed app name to launch after install.")
@click.option("--timeout", default=600, show_default=True)
@click.option("--dsym", "dsym_path", default=None, type=click.Path())
@click.option("--json", "as_json", is_flag=True, default=False)
def install_pkg_cmd(
    name: str,
    pkg_path: str,
    app_name: str | None,
    timeout: int,
    dsym_path: str | None,
    as_json: bool,
) -> None:
    def run():
        result = install_pkg(
            vm=name,
            pkg_path=pkg_path,
            app_name=app_name,
            timeout=timeout,
            dsym_path=dsym_path,
        )
        data = {
            "artifact_path": result.artifact_path,
            "app_path": result.app_path,
            "installer_exit_code": result.install_exit_code,
            "new_apps": result.new_apps,
            "launch_agents": result.launch_agents,
            "launch_daemons": result.launch_daemons,
            "installed_files": result.installed_files,
            "postinstall_logs": result.postinstall_logs,
            "report": result.report,
        }
        if result.install_exit_code == 0:
            emit(success("install-pkg", vm=name, data=data), as_json=as_json)
        emit(
            failure(
                "install-pkg",
                vm=name,
                data=data,
                errors=[
                    ErrorDetail(
                        code="INSTALL_FAILED",
                        message="Package install did not complete successfully.",
                        details={"installer_exit_code": result.install_exit_code},
                    )
                ],
            ),
            exit_code=1,
            as_json=as_json,
        )

    handle_errors("install-pkg", name, run, as_json=as_json)


@main.command("exec")
@click.option("--name", required=True)
@click.option("--command", required=True)
@click.option("--timeout", default=60, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def exec_cmd(name: str, command: str, timeout: int, as_json: bool) -> None:
    def run():
        result = guest_exec_command(vm=name, command=command, timeout=timeout)
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


@main.command("applescript")
@click.option("--name", required=True)
@click.option("--script", required=True)
@click.option("--timeout", default=60, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def applescript_cmd(name: str, script: str, timeout: int, as_json: bool) -> None:
    def run():
        result = run_guest_applescript(vm=name, script=script, timeout=timeout)
        response = success(
            "applescript",
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

    handle_errors("applescript", name, run, as_json=as_json)


@main.command("jxa")
@click.option("--name", required=True)
@click.option("--script", required=True)
@click.option("--timeout", default=60, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def jxa_cmd(name: str, script: str, timeout: int, as_json: bool) -> None:
    def run():
        result = run_guest_jxa(vm=name, script=script, timeout=timeout)
        response = success(
            "jxa",
            vm=name,
            data={"exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr},
        )
        if result.exit_code != 0:
            emit(response, exit_code=result.exit_code, as_json=as_json)
        emit(response, as_json=as_json)

    handle_errors("jxa", name, run, as_json=as_json)


@main.command("push")
@click.option("--name", required=True)
@click.option("--path", "local_path", required=True, type=click.Path())
@click.option("--dest", "guest_path", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def push_cmd(name: str, local_path: str, guest_path: str, as_json: bool) -> None:
    def run():
        result = push_file_to_guest(vm=name, local_path=local_path, guest_path=guest_path)
        emit(
            success(
                "push",
                vm=name,
                data={
                    "local_path": result.local_path,
                    "guest_path": result.guest_path,
                    "artifact_type": result.artifact_type,
                },
            ),
            as_json=as_json,
        )

    handle_errors("push", name, run, as_json=as_json)


@main.command("pull")
@click.option("--name", required=True)
@click.option("--src", "guest_path", required=True)
@click.option("--dest", "local_path", default=None, type=click.Path())
@click.option("--json", "as_json", is_flag=True, default=False)
def pull_cmd(name: str, guest_path: str, local_path: str | None, as_json: bool) -> None:
    def run():
        result = pull_file_from_guest(vm=name, guest_path=guest_path, local_path=local_path)
        emit(
            success(
                "pull",
                vm=name,
                data={
                    "guest_path": result.guest_path,
                    "local_path": result.local_path,
                    "is_directory": result.is_directory,
                },
            ),
            as_json=as_json,
        )

    handle_errors("pull", name, run, as_json=as_json)


@main.command("type-text")
@click.option("--name", required=True)
@click.option("--text", required=True)
@click.option("--timeout", default=30, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def type_text_cmd(name: str, text: str, timeout: int, as_json: bool) -> None:
    def run():
        result = guest_type_text(vm=name, text=text, timeout=timeout)
        response = success(
            "type-text",
            vm=name,
            data={"exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr},
        )
        if result.exit_code != 0:
            emit(response, exit_code=result.exit_code, as_json=as_json)
        emit(response, as_json=as_json)

    handle_errors("type-text", name, run, as_json=as_json)


@main.command("send-keys")
@click.option("--name", required=True)
@click.option("--key", required=True)
@click.option("--modifier", "modifiers", multiple=True, help="Repeatable: command/option/control/shift/fn.")
@click.option("--timeout", default=30, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def send_keys_cmd(name: str, key: str, modifiers: tuple[str, ...], timeout: int, as_json: bool) -> None:
    def run():
        result = guest_send_keys(vm=name, key=key, modifiers=list(modifiers), timeout=timeout)
        response = success(
            "send-keys",
            vm=name,
            data={"exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr},
        )
        if result.exit_code != 0:
            emit(response, exit_code=result.exit_code, as_json=as_json)
        emit(response, as_json=as_json)

    handle_errors("send-keys", name, run, as_json=as_json)


@main.command("click")
@click.option("--name", required=True)
@click.option("--x", required=True, type=float)
@click.option("--y", required=True, type=float)
@click.option("--button", default="left", show_default=True, type=click.Choice(["left", "right", "center"]))
@click.option("--count", default=1, show_default=True, type=int)
@click.option("--timeout", default=30, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def click_cmd(name: str, x: float, y: float, button: str, count: int, timeout: int, as_json: bool) -> None:
    def run():
        result = guest_click(vm=name, x=x, y=y, button=button, count=count, timeout=timeout)
        response = success(
            "click",
            vm=name,
            data={
                "x": x,
                "y": y,
                "button": button,
                "count": count,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        if result.exit_code != 0:
            emit(response, exit_code=result.exit_code, as_json=as_json)
        emit(response, as_json=as_json)

    handle_errors("click", name, run, as_json=as_json)


@main.command("open-app")
@click.option("--name", required=True)
@click.option("--app", "app_path", required=True)
@click.option("--arg", "app_args", multiple=True)
@click.option("--new-instance/--reuse-instance", default=True, show_default=True)
@click.option("--wait-seconds", default=0.0, show_default=True, type=float)
@click.option("--json", "as_json", is_flag=True, default=False)
def open_app_cmd(
    name: str,
    app_path: str,
    app_args: tuple[str, ...],
    new_instance: bool,
    wait_seconds: float,
    as_json: bool,
) -> None:
    def run():
        result = open_guest_app(
            vm=name,
            app_path=app_path,
            args=list(app_args),
            new_instance=new_instance,
            wait_seconds=wait_seconds,
        )
        response = success(
            "open-app",
            vm=name,
            data={
                "app_path": result.app_path,
                "argv": result.argv,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        if result.exit_code != 0:
            emit(response, exit_code=result.exit_code, as_json=as_json)
        emit(response, as_json=as_json)

    handle_errors("open-app", name, run, as_json=as_json)


@main.command("list-windows")
@click.option("--name", required=True)
@click.option("--app", "app_name", default=None)
@click.option("--json", "as_json", is_flag=True, default=False)
def list_windows_cmd(name: str, app_name: str | None, as_json: bool) -> None:
    def run():
        windows = list_guest_windows(vm=name, app_name=app_name)
        emit(
            success(
                "list-windows",
                vm=name,
                data={"windows": windows, "count": len(windows), "app_name": app_name},
            ),
            as_json=as_json,
        )

    handle_errors("list-windows", name, run, as_json=as_json)


@main.command("list-processes")
@click.option("--name", required=True)
@click.option("--filter", "filter_text", default=None)
@click.option("--json", "as_json", is_flag=True, default=False)
def list_processes_cmd(name: str, filter_text: str | None, as_json: bool) -> None:
    def run():
        processes = list_guest_processes(vm=name, filter_text=filter_text)
        emit(
            success(
                "list-processes",
                vm=name,
                data={"processes": processes, "count": len(processes), "filter": filter_text},
            ),
            as_json=as_json,
        )

    handle_errors("list-processes", name, run, as_json=as_json)


@main.command("run-app")
@click.option("--name", required=True)
@click.option("--app", "app_path", required=True)
@click.option("--timeout", default=120, show_default=True)
@click.option("--dsym", "dsym_path", default=None, type=click.Path())
@click.option("--json", "as_json", is_flag=True, default=False)
def run_app_cmd(name: str, app_path: str, timeout: int, dsym_path: str | None, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        guest_app = validate_guest_path(app_path)
        try:
            result = run_app_smoke(vm=name, app_path=guest_app, timeout=timeout, dsym_path=dsym_path)
            emit(
                success(
                    "run-app",
                    vm=name,
                    data={
                        "launched": result.launched,
                        "crashed": result.crashed,
                        "app_path": result.app_path,
                        "screenshot": result.screenshot,
                        "logs": result.logs,
                        "crash_reports": result.crash_reports,
                        "report": result.report,
                    },
                    warnings=result.warnings,
                ),
                as_json=as_json,
            )
        except AppCrashedError as exc:
            emit(
                failure(
                    "run-app",
                    vm=name,
                    data={
                        "launched": exc.details.get("launched", True),
                        "crashed": True,
                        "app_path": guest_app,
                        "screenshot": exc.details.get("screenshot"),
                        "logs": exc.details.get("logs"),
                        "crash_reports": exc.details.get("crash_reports", []),
                        "report": exc.details.get("report"),
                    },
                    errors=[exc],
                ),
                exit_code=1,
                as_json=as_json,
            )

    handle_errors("run-app", name, run, as_json=as_json)


@main.command("run-installed-app")
@click.option("--name", required=True)
@click.option("--app", "app_name", required=True)
@click.option("--timeout", default=120, show_default=True)
@click.option("--dsym", "dsym_path", default=None, type=click.Path())
@click.option("--json", "as_json", is_flag=True, default=False)
def run_installed_app_cmd(
    name: str,
    app_name: str,
    timeout: int,
    dsym_path: str | None,
    as_json: bool,
) -> None:
    def run():
        try:
            result = run_installed_app(vm=name, app_name=app_name, timeout=timeout, dsym_path=dsym_path)
            emit(
                success(
                    "run-installed-app",
                    vm=name,
                    data={
                        "launched": result.launched,
                        "crashed": result.crashed,
                        "app_path": result.app_path,
                        "screenshot": result.screenshot,
                        "logs": result.logs,
                        "crash_reports": result.crash_reports,
                        "report": result.report,
                    },
                    warnings=result.warnings,
                ),
                as_json=as_json,
            )
        except AppCrashedError as exc:
            emit(
                failure(
                    "run-installed-app",
                    vm=name,
                    data={
                        "launched": exc.details.get("launched", True),
                        "crashed": True,
                        "app_path": exc.details.get("app_path"),
                        "screenshot": exc.details.get("screenshot"),
                        "logs": exc.details.get("logs"),
                        "crash_reports": exc.details.get("crash_reports", []),
                        "report": exc.details.get("report"),
                    },
                    errors=[exc],
                ),
                exit_code=1,
                as_json=as_json,
            )

    handle_errors("run-installed-app", name, run, as_json=as_json)


@main.command("run-on-warm")
@click.option("--name", required=True)
@click.option("--app", "local_app_path", required=True, type=click.Path(exists=True))
@click.option("--dest", "guest_path", default=None)
@click.option("--timeout", default=120, show_default=True)
@click.option("--dsym", "dsym_path", default=None, type=click.Path())
@click.option("--json", "as_json", is_flag=True, default=False)
def run_on_warm_cmd(
    name: str,
    local_app_path: str,
    guest_path: str | None,
    timeout: int,
    dsym_path: str | None,
    as_json: bool,
) -> None:
    def run():
        try:
            result = run_on_warm(
                vm=name,
                local_app_path=local_app_path,
                guest_path=guest_path,
                timeout=timeout,
                dsym_path=dsym_path,
            )
            emit(
                success(
                    "run-on-warm",
                    vm=name,
                    data={
                        "local_path": result.upload.local_path,
                        "guest_path": result.upload.guest_path,
                        "launched": result.smoke.launched,
                        "crashed": result.smoke.crashed,
                        "app_path": result.smoke.app_path,
                        "screenshot": result.smoke.screenshot,
                        "logs": result.smoke.logs,
                        "crash_reports": result.smoke.crash_reports,
                        "report": result.smoke.report,
                    },
                    warnings=result.smoke.warnings,
                ),
                as_json=as_json,
            )
        except AppCrashedError as exc:
            emit(
                failure(
                    "run-on-warm",
                    vm=name,
                    data={
                        "local_path": local_app_path,
                        "guest_path": guest_path or f"/Users/admin/Desktop/{Path(local_app_path).name}",
                        "launched": exc.details.get("launched", True),
                        "crashed": True,
                        "app_path": exc.details.get("app_path"),
                        "screenshot": exc.details.get("screenshot"),
                        "logs": exc.details.get("logs"),
                        "crash_reports": exc.details.get("crash_reports", []),
                        "report": exc.details.get("report"),
                    },
                    errors=[exc],
                ),
                exit_code=1,
                as_json=as_json,
            )

    handle_errors("run-on-warm", name, run, as_json=as_json)


@main.command("assert-window")
@click.option("--name", required=True)
@click.option("--contains", required=True)
@click.option("--app", "app_name", default=None)
@click.option("--json", "as_json", is_flag=True, default=False)
def assert_window_cmd(name: str, contains: str, app_name: str | None, as_json: bool) -> None:
    def run():
        result = assert_window_contains(vm=name, contains=contains, app_name=app_name)
        payload = result.to_dict()
        if result.ok:
            emit(success("assert-window", vm=name, data=payload), as_json=as_json)
        emit(
            failure(
                "assert-window",
                vm=name,
                data=payload,
                errors=[
                    ErrorDetail(
                        code="ASSERTION_FAILED",
                        message=result.message,
                        details=payload["details"],
                    )
                ],
            ),
            exit_code=1,
            as_json=as_json,
        )

    handle_errors("assert-window", name, run, as_json=as_json)


@main.command("assert-app-running")
@click.option("--name", required=True)
@click.option("--bundle-id", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def assert_app_running_cmd(name: str, bundle_id: str, as_json: bool) -> None:
    def run():
        result = assert_app_running(vm=name, bundle_id=bundle_id)
        payload = result.to_dict()
        if result.ok:
            emit(success("assert-app-running", vm=name, data=payload), as_json=as_json)
        emit(
            failure(
                "assert-app-running",
                vm=name,
                data=payload,
                errors=[
                    ErrorDetail(
                        code="ASSERTION_FAILED",
                        message=result.message,
                        details=payload["details"],
                    )
                ],
            ),
            exit_code=1,
            as_json=as_json,
        )

    handle_errors("assert-app-running", name, run, as_json=as_json)


@main.command("report")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, default=False)
def report_cmd(run_id: str, as_json: bool) -> None:
    def run():
        emit(success("report", data=load_report(run_id)), as_json=as_json)

    handle_errors("report", None, run, as_json=as_json)


@main.command("gate")
@click.option("--image", default=None)
@click.option("--profile", default=None)
@click.option("--artifact", "artifact_path", required=True, type=click.Path(exists=True))
@click.option("--requirements", default=None)
@click.option("--app", "app_name", default=None)
@click.option("--bundle-id", default=None)
@click.option("--window-contains", default=None)
@click.option("--timeout", default=None, type=int)
@click.option("--dsym", "dsym_path", default=None, type=click.Path())
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def gate_cmd(
    image: str | None,
    profile: str | None,
    artifact_path: str,
    requirements: str | None,
    app_name: str | None,
    bundle_id: str | None,
    window_contains: str | None,
    timeout: int | None,
    dsym_path: str | None,
    headless: bool,
    as_json: bool,
) -> None:
    def run():
        result = run_release_gate(
            artifact_path=artifact_path,
            image=image,
            profile=profile,
            requirements=_gate_requirements(requirements),
            app_name=app_name,
            bundle_id=bundle_id,
            window_contains=window_contains,
            timeout=timeout,
            dsym_path=dsym_path,
            headless=headless,
        )
        data = {
            "vm": result.vm,
            "image": result.image,
            "run_id": result.run_id,
            "run_dir": result.run_dir,
            "artifact_path": result.artifact_path,
            "artifact_type": result.artifact_type,
            "destroyed": result.destroyed,
            "failed_requirements": result.failed_requirements,
            "report": result.report,
        }
        if result.ok:
            emit(success("gate", vm=result.vm, data=data, warnings=result.warnings), as_json=as_json)
        emit(
            failure(
                "gate",
                vm=result.vm,
                data=data,
                warnings=result.warnings,
                errors=result.errors or [
                    ErrorDetail(
                        code="RELEASE_GATE_FAILED",
                        message="The artifact did not satisfy the requested release-gate requirements.",
                        details={"failed_requirements": result.failed_requirements},
                    )
                ],
            ),
            exit_code=1,
            as_json=as_json,
        )

    handle_errors("gate", None, run, as_json=as_json)


@main.command("matrix")
@click.option("--images", required=True, help="Comma-separated Tart image names.")
@click.option("--artifact", "artifact_path", required=True, type=click.Path(exists=True))
@click.option("--requirements", default=None)
@click.option("--app", "app_name", default=None)
@click.option("--bundle-id", default=None)
@click.option("--window-contains", default=None)
@click.option("--timeout", default=None, type=int)
@click.option("--dsym", "dsym_path", default=None, type=click.Path())
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def matrix_cmd(
    images: str,
    artifact_path: str,
    requirements: str | None,
    app_name: str | None,
    bundle_id: str | None,
    window_contains: str | None,
    timeout: int | None,
    dsym_path: str | None,
    headless: bool,
    as_json: bool,
) -> None:
    def run():
        ok, results = run_matrix(
            images=_csv_items(images),
            artifact_path=artifact_path,
            requirements=_gate_requirements(requirements),
            app_name=app_name,
            bundle_id=bundle_id,
            window_contains=window_contains,
            timeout=timeout,
            dsym_path=dsym_path,
            headless=headless,
        )
        data = {
            "ok": ok,
            "results": [
                {
                    "image": result.image,
                    "ok": result.ok,
                    "launched": result.launched,
                    "crashed": result.crashed,
                    "failed_requirements": result.failed_requirements,
                    "report_path": result.report_path,
                    "crash_reports": result.crash_reports,
                }
                for result in results
            ],
        }
        if ok:
            emit(success("matrix", data=data), as_json=as_json)
        emit(
            failure(
                "matrix",
                data=data,
                errors=[
                    ErrorDetail(
                        code="MATRIX_FAILED",
                        message="One or more matrix entries failed the requested requirements.",
                        details={"images": _csv_items(images)},
                    )
                ],
            ),
            exit_code=1,
            as_json=as_json,
        )

    handle_errors("matrix", None, run, as_json=as_json)


@main.command("logs")
@click.option("--name", required=True)
@click.option("--last", "duration", default="5m", show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def logs_cmd(name: str, duration: str, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        session = _guest_session(name)
        manager = _run_manager()
        log_path = collect_logs(session, manager, name, duration=duration)
        emit(success("logs", vm=name, data={"logs": str(log_path), "duration": duration}), as_json=as_json)

    handle_errors("logs", name, run, as_json=as_json)


@main.command("screenshot")
@click.option("--name", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def screenshot_cmd(name: str, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        session = _guest_session(name)
        manager = _run_manager()
        screenshot_path, warning = capture_screenshot(session, manager, name)
        emit(
            success(
                "screenshot",
                vm=name,
                data={"screenshot": str(screenshot_path) if screenshot_path else None},
                warnings=[warning] if warning else [],
            ),
            as_json=as_json,
        )

    handle_errors("screenshot", name, run, as_json=as_json)


@main.command("collect-crashes")
@click.option("--name", required=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def collect_crashes_cmd(name: str, as_json: bool) -> None:
    def run():
        validate_vm_name(name)
        session = _guest_session(name)
        manager = _run_manager()
        basenames = sorted(guest_crash_basenames(session))
        paths = download_crash_reports(session, manager, name, basenames)
        emit(success("collect-crashes", vm=name, data={"crash_reports": [str(p) for p in paths]}), as_json=as_json)

    handle_errors("collect-crashes", name, run, as_json=as_json)


@main.command("demo")
@click.option("--app", "app_path", required=True, type=click.Path(exists=True))
@click.option("--image", default=None, help="Base template VM (default: config default_image)")
@click.option("--profile", default=None)
@click.option("--timeout", default=None, type=int, help="Smoke test wait seconds")
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def demo_cmd(
    app_path: str,
    image: str | None,
    profile: str | None,
    timeout: int | None,
    headless: bool,
    as_json: bool,
) -> None:
    """Run full sandbox demo: start, upload, smoke test, destroy."""

    def run():
        result = run_demo(
            app_path=app_path,
            image=image,
            profile=profile,
            timeout=timeout,
            headless=headless,
        )
        data = {
            "vm": result.vm,
            "image": result.image,
            "run_id": result.run_id,
            "run_dir": result.run_dir,
            "local_app": result.local_app,
            "guest_app": result.guest_app,
            "launched": result.launched,
            "crashed": result.crashed,
            "destroyed": result.destroyed,
            "artifacts": {
                "screenshot": result.screenshot,
                "logs": result.logs,
                "crash_reports": result.crash_reports,
            },
            "report": result.report,
        }
        if result.ok:
            emit(success("demo", vm=result.vm, data=data, warnings=result.warnings), as_json=as_json)
        emit(
            failure("demo", vm=result.vm, data=data, warnings=result.warnings, errors=result.errors),
            exit_code=1,
            as_json=as_json,
        )

    handle_errors("demo", None, run, as_json=as_json)


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
        checks.append(DoctorCheck(name="state_dir", ok=state_dir.exists(), message=str(state_dir)))

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


if __name__ == "__main__":
    main()

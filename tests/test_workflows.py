"""Workflow-level tests for release-gate and upload helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from macbox.errors import AppError, SafetyError
from macbox.models import GuestCommandResult
from macbox.workflows import (
    AssertionResult,
    GateResult,
    RunAppResult,
    StartResult,
    UploadResult,
    assert_app_running,
    click_ui_element,
    drag_in_guest,
    guest_click,
    guest_exec_command,
    guest_send_keys,
    guest_type_text,
    inspect_ui_tree,
    list_guest_processes,
    list_guest_windows,
    observe_guest,
    open_guest_app,
    paste_text_in_guest,
    prepare_agent_workspace,
    pull_file_from_guest,
    push_file_to_guest,
    resolve_profile,
    run_script_in_guest,
    run_guest_applescript,
    scroll_in_guest,
    run_release_gate,
    start_sandbox,
    upload_artifact_to_guest,
    watch_sandbox,
)


def test_resolve_profile_rejects_conflicting_image_and_profile() -> None:
    with pytest.raises(SafetyError):
        resolve_profile(image="macos-sonoma-clean", profile="macos-sequoia-dark-mode")


def test_resolve_profile_rejects_unknown_profile_when_image_also_set() -> None:
    with pytest.raises(SafetyError):
        resolve_profile(image="macos-sequoia-clean", profile="unknown-profile")


def test_start_sandbox_records_vnc_watch_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path / "state"))

    with patch("macbox.workflows._backend") as backend, patch(
        "macbox.workflows._wait_for_guest_ready"
    ), patch("macbox.workflows._apply_profile_setup"):
        backend.return_value.vm_exists.return_value = False
        backend.return_value.ip.return_value = "192.168.64.2"

        started = start_sandbox(
            image="macos-sequoia-clean",
            name="macbox-test-001",
            display_mode="vnc",
        )

    backend.return_value.run.assert_called_once_with(
        "macbox-test-001",
        headless=False,
        display_mode="vnc",
    )
    assert started.headless is False
    assert started.display_mode == "vnc"
    assert started.watch["url"] == "vnc://192.168.64.2"
    assert started.watch["open_command"] == ["open", "vnc://192.168.64.2"]


def test_start_sandbox_no_headless_maps_to_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path / "state"))

    with patch("macbox.workflows._backend") as backend, patch(
        "macbox.workflows._wait_for_guest_ready"
    ), patch("macbox.workflows._apply_profile_setup"):
        backend.return_value.vm_exists.return_value = True
        backend.return_value.ip.return_value = "192.168.64.2"

        started = start_sandbox(
            image="macos-sequoia-clean",
            name="macbox-test-001",
            headless=False,
        )

    backend.return_value.run.assert_called_once_with(
        "macbox-test-001",
        headless=False,
        display_mode="window",
    )
    assert started.display_mode == "window"
    assert started.watch["available"] is True
    assert started.watch["mode"] == "window"


def test_run_release_gate_requires_bundle_id_for_app_running(tmp_path) -> None:
    app = tmp_path / "Demo.app"
    app.mkdir()

    with pytest.raises(SafetyError):
        run_release_gate(
            artifact_path=app,
            image="macos-sequoia-clean",
            requirements=["launch", "app-running"],
        )


def test_upload_artifact_to_guest_records_materialized_upload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path / "state"))
    app = tmp_path / "Demo.app"
    app.mkdir()

    with patch("macbox.workflows._guest_session") as guest_session:
        guest_session.return_value.exec.return_value.exit_code = 0
        uploaded = upload_artifact_to_guest(vm="macbox-test-001", local_path=app)

    guest_session.return_value.upload.assert_called_once()
    upload_artifact = Path(tmp_path / "state" / "runs").glob("*/uploads/Demo.app")
    upload_artifact_path = next(upload_artifact)
    assert upload_artifact_path.is_symlink()
    assert upload_artifact_path.resolve() == app.resolve()
    assert uploaded.artifact_type == ".app"


def test_run_release_gate_preserves_app_error_report(tmp_path) -> None:
    app = tmp_path / "Demo.app"
    app.mkdir()
    start = StartResult(
        vm="macbox-gate-001",
        image="macos-sequoia-clean",
        run_id="run-id",
        run_dir=str(tmp_path / "run"),
        headless=True,
    )
    upload = UploadResult(
        local_path=str(app),
        guest_path="/Users/admin/Desktop/Demo.app",
        artifact_type=".app",
    )
    error = AppError(
        "Failed to launch app",
        details={
            "report": {
                "run_id": "run-id",
                "launched": False,
                "crashed": False,
                "verdict": "failed",
                "reason": "app_launch_failed",
                "assertions": [],
            }
        },
    )

    with patch("macbox.workflows.start_sandbox", return_value=start), patch(
        "macbox.workflows.upload_artifact_to_guest", return_value=upload
    ), patch("macbox.workflows.run_app_smoke", side_effect=error), patch(
        "macbox.workflows.destroy_sandbox"
    ):
        result = run_release_gate(
            artifact_path=app,
            image="macos-sequoia-clean",
            requirements=["launch"],
        )

    assert result.ok is False
    assert result.report["reason"] == "app_launch_failed"
    assert result.failed_requirements == ["launch"]
    assert result.errors[0].code == "APP_ERROR"


def test_run_release_gate_applies_window_requirement_assertions(tmp_path) -> None:
    app = tmp_path / "Demo.app"
    app.mkdir()
    start = StartResult(
        vm="macbox-gate-001",
        image="macos-sequoia-clean",
        run_id="run-id",
        run_dir=str(tmp_path / "run"),
        headless=True,
    )
    upload = UploadResult(
        local_path=str(app),
        guest_path="/Users/admin/Desktop/Demo.app",
        artifact_type=".app",
    )
    smoke = RunAppResult(
        launched=True,
        crashed=False,
        app_path="/Users/admin/Desktop/Demo.app",
        screenshot=str(tmp_path / "shot.png"),
        logs=str(tmp_path / "system.log"),
        crash_reports=[],
        report={
            "run_id": "run-id",
            "launched": True,
            "crashed": False,
            "verdict": "passed",
            "assertions": [],
            "report_path": str(tmp_path / "report.json"),
        },
    )

    with patch("macbox.workflows.start_sandbox", return_value=start), patch(
        "macbox.workflows.upload_artifact_to_guest", return_value=upload
    ), patch("macbox.workflows.run_app_smoke", return_value=smoke), patch(
        "macbox.workflows.assert_window_contains",
        return_value=AssertionResult(
            kind="window_contains",
            ok=True,
            message="Found window containing 'Welcome'.",
            details={"contains": "Welcome"},
        ),
    ) as assert_window, patch("macbox.workflows.persist_report", side_effect=lambda vm, report: report), patch(
        "macbox.workflows.destroy_sandbox"
    ):
        result = run_release_gate(
            artifact_path=app,
            image="macos-sequoia-clean",
            requirements=["launch", "window:Welcome"],
        )

    assert result.ok is True
    assert assert_window.call_args.kwargs["contains"] == "Welcome"
    assert result.report["gate"]["passed"] is True


def test_assert_app_running_uses_documented_lsappinfo_query() -> None:
    with patch("macbox.workflows._guest_session") as guest_session:
        guest_session.return_value.exec.return_value.exit_code = 0
        guest_session.return_value.exec.return_value.stdout = 'ASN:0x0-0x1234-"Demo":\n'
        guest_session.return_value.exec.return_value.stderr = ""

        result = assert_app_running(vm="macbox-test-001", bundle_id="com.example.Demo")

    assert result.ok is True
    guest_session.return_value.exec.assert_called_once_with(
        "lsappinfo find bundleID=com.example.Demo",
        timeout=30,
    )


def test_assert_app_running_does_not_treat_candidate_process_name_as_bundle_match(tmp_path) -> None:
    candidate = tmp_path / "Demo.app"
    candidate.mkdir()

    with patch("macbox.workflows._guest_session") as guest_session:
        guest_session.return_value.exec.side_effect = [
            type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})(),
            type("Result", (), {"exit_code": 0, "stdout": f"{candidate}\n", "stderr": ""})(),
            type("Result", (), {"exit_code": 0, "stdout": "123\n", "stderr": ""})(),
        ]

        result = assert_app_running(vm="macbox-test-001", bundle_id="com.example.Demo")

    assert result.ok is False
    assert result.message == "Bundle com.example.Demo is not running."
    assert result.details["candidate_paths"] == [str(candidate)]
    assert result.details["matched_process_names"] == ["Demo"]


def test_guest_exec_command_uses_guest_session() -> None:
    with patch("macbox.workflows._guest_session") as guest_session:
        guest_session.return_value.exec.return_value = GuestCommandResult(
            exit_code=0,
            stdout="ok\n",
            stderr="",
        )

        result = guest_exec_command(vm="macbox-test-001", command="uname -a", timeout=25)

    assert result.stdout == "ok\n"
    guest_session.return_value.exec.assert_called_once_with("uname -a", timeout=25)


def test_run_guest_applescript_wraps_script_in_heredoc() -> None:
    with patch("macbox.workflows._guest_session") as guest_session:
        guest_session.return_value.exec.return_value = GuestCommandResult(
            exit_code=0,
            stdout="done\n",
            stderr="",
        )

        result = run_guest_applescript(
            vm="macbox-test-001",
            script='return "done"',
            timeout=12,
        )

    assert result.stdout == "done\n"
    guest_session.return_value.exec.assert_called_once_with(
        "osascript <<'APPLESCRIPT'\nreturn \"done\"\nAPPLESCRIPT",
        timeout=12,
    )


def test_list_guest_windows_parses_unscoped_titles() -> None:
    with patch("macbox.workflows.run_guest_applescript") as applescript:
        applescript.return_value = GuestCommandResult(
            exit_code=0,
            stdout="Ghostty::READY\nHarness::bench-01\n",
            stderr="",
        )

        windows = list_guest_windows(vm="macbox-test-001")

    assert windows == [
        {"app_name": "Ghostty", "title": "READY"},
        {"app_name": "Harness", "title": "bench-01"},
    ]


def test_list_guest_processes_parses_and_filters() -> None:
    with patch("macbox.workflows.guest_exec_command") as guest_exec:
        guest_exec.return_value = GuestCommandResult(
            exit_code=0,
            stdout=(
                "123  0.1  2048 /Applications/Ghostty.app/Contents/MacOS/ghostty\n"
                "456  8.5  8192 /Applications/Harness.app/Contents/MacOS/Harness\n"
            ),
            stderr="",
        )

        processes = list_guest_processes(vm="macbox-test-001", filter_text="Harness")

    assert processes == [
        {
            "pid": 456,
            "cpu_percent": 8.5,
            "rss_kb": 8192,
            "command": "/Applications/Harness.app/Contents/MacOS/Harness",
        }
    ]


def test_push_file_to_guest_allows_any_suffix(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path / "state"))
    script = tmp_path / "fixture.sh"
    script.write_text("echo hi", encoding="utf-8")

    with patch("macbox.workflows._wait_for_guest_ready"), patch(
        "macbox.workflows._guest_session"
    ) as guest_session:
        result = push_file_to_guest(
            vm="macbox-test-001",
            local_path=script,
            guest_path="/Users/admin/fixture.sh",
        )

    guest_session.return_value.upload.assert_called_once()
    assert result.guest_path == "/Users/admin/fixture.sh"
    assert result.local_path == str(script.resolve())


def test_push_file_to_guest_rejects_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path / "state"))
    secret = tmp_path / "api-token.txt"
    secret.write_text("x", encoding="utf-8")

    with pytest.raises(SafetyError):
        push_file_to_guest(
            vm="macbox-test-001",
            local_path=secret,
            guest_path="/Users/admin/token.txt",
        )


def test_pull_file_from_guest_downloads_to_run_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path / "state"))

    with patch("macbox.workflows._wait_for_guest_ready"), patch(
        "macbox.workflows._guest_session"
    ) as guest_session:
        guest_session.return_value.remote_path_exists.return_value = True
        guest_session.return_value.remote_is_directory.return_value = False
        result = pull_file_from_guest(
            vm="macbox-test-001",
            guest_path="/Users/admin/output.log",
        )

    guest_session.return_value.download.assert_called_once()
    assert result.guest_path == "/Users/admin/output.log"
    assert result.is_directory is False
    assert "downloads" in result.local_path


def test_guest_type_text_escapes_and_wraps_keystroke() -> None:
    with patch("macbox.workflows.run_guest_applescript") as applescript:
        applescript.return_value = GuestCommandResult(exit_code=0, stdout="", stderr="")
        guest_type_text(vm="macbox-test-001", text='say "hi"\\done')

    script = applescript.call_args.kwargs["script"]
    assert script == 'tell application "System Events" to keystroke "say \\"hi\\"\\\\done"'


def test_guest_send_keys_named_key_with_modifiers() -> None:
    with patch("macbox.workflows.run_guest_applescript") as applescript:
        applescript.return_value = GuestCommandResult(exit_code=0, stdout="", stderr="")
        guest_send_keys(vm="macbox-test-001", key="return", modifiers=["cmd", "shift"])

    script = applescript.call_args.kwargs["script"]
    assert script == (
        'tell application "System Events" to key code 36 using {command down, shift down}'
    )


def test_guest_send_keys_single_char() -> None:
    with patch("macbox.workflows.run_guest_applescript") as applescript:
        applescript.return_value = GuestCommandResult(exit_code=0, stdout="", stderr="")
        guest_send_keys(vm="macbox-test-001", key="c", modifiers=["command"])

    script = applescript.call_args.kwargs["script"]
    assert script == 'tell application "System Events" to keystroke "c" using {command down}'


def test_guest_send_keys_rejects_unknown_modifier() -> None:
    with pytest.raises(SafetyError):
        guest_send_keys(vm="macbox-test-001", key="c", modifiers=["hyper"])


def test_guest_click_builds_jxa_with_coordinates() -> None:
    with patch("macbox.workflows.run_guest_jxa") as jxa:
        jxa.return_value = GuestCommandResult(exit_code=0, stdout="ok", stderr="")
        guest_click(vm="macbox-test-001", x=100, y=200, button="left", count=2)

    script = jxa.call_args.kwargs["script"]
    assert "ObjC.import('CoreGraphics')" in script
    assert "var px = 100.0;" in script
    assert "var py = 200.0;" in script
    assert "kCGEventLeftMouseDown" in script
    assert "clicks = 2" in script


def test_prepare_agent_workspace_resets_guest_directories() -> None:
    with patch("macbox.workflows.guest_exec_command") as guest_exec:
        guest_exec.return_value = GuestCommandResult(exit_code=0, stdout="", stderr="")
        result = prepare_agent_workspace(vm="macbox-test-001", reset=True)

    assert result["workspace"] == "/Users/admin/.macbox-agent"
    command = guest_exec.call_args.kwargs["command"]
    assert "rm -rf /Users/admin/.macbox-agent" in command
    assert "mkdir -p /Users/admin/.macbox-agent/scripts" in command


def test_run_script_in_guest_writes_diagnostics(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path / "state"))
    with patch(
        "macbox.workflows.prepare_agent_workspace",
        return_value={
            "directories": {
                "scripts": "/Users/admin/.macbox-agent/scripts",
                "tmp": "/Users/admin/.macbox-agent/tmp",
                "artifacts": "/Users/admin/.macbox-agent/artifacts",
                "logs": "/Users/admin/.macbox-agent/logs",
            }
        },
    ), patch("macbox.workflows._guest_session") as guest_session:
        guest_session.return_value.exec.side_effect = [
            GuestCommandResult(exit_code=0, stdout="", stderr=""),
            GuestCommandResult(exit_code=7, stdout="hello\n", stderr="bad\n"),
        ]

        result = run_script_in_guest(
            vm="macbox-test-001",
            script="echo hello",
            language="shell",
            timeout=12,
        )

    guest_session.return_value.upload.assert_called_once()
    assert result["exit_code"] == 7
    assert Path(result["stdout"]).read_text(encoding="utf-8") == "hello\n"
    assert Path(result["stderr"]).read_text(encoding="utf-8") == "bad\n"
    assert Path(result["local_script"]).read_text(encoding="utf-8") == "echo hello"
    assert guest_session.return_value.exec.call_args_list[-1].args[0].startswith("/bin/zsh ")


def test_observe_guest_collects_screenshot_and_state(tmp_path) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (1440).to_bytes(4, "big")
        + (900).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    with patch("macbox.workflows._guest_session"), patch(
        "macbox.workflows.capture_screenshot",
        return_value=(shot, None),
    ), patch("macbox.workflows.run_guest_jxa") as jxa, patch(
        "macbox.workflows.list_guest_windows"
    ) as windows, patch(
        "macbox.workflows.list_guest_processes"
    ) as processes:
        jxa.return_value = GuestCommandResult(
            exit_code=0,
            stdout='{"app_name":"Finder","window_title":"Desktop"}\n',
            stderr="",
        )
        windows.return_value = [{"app_name": "Finder", "title": "Desktop"}]
        processes.return_value = [{"pid": 1, "command": "Finder"}]

        result = observe_guest(vm="macbox-test-001")

    assert result["screenshot"] == str(shot)
    assert result["frontmost"]["app_name"] == "Finder"
    assert result["screen"]["width"] == 1440
    assert result["windows"] == [{"app_name": "Finder", "title": "Desktop"}]
    processes.assert_called_once_with(vm="macbox-test-001", filter_text="Finder")


def test_inspect_ui_tree_returns_accessibility_failure() -> None:
    with patch("macbox.workflows.run_guest_jxa") as jxa:
        jxa.return_value = GuestCommandResult(exit_code=1, stdout="", stderr="not allowed assistive access")
        result = inspect_ui_tree(vm="macbox-test-001", app_name="Demo")

    assert result["accessible"] is False
    assert "Accessibility permission" in result["hint"]


def test_click_ui_element_requires_selector() -> None:
    with pytest.raises(SafetyError):
        click_ui_element(vm="macbox-test-001")


def test_click_ui_element_generates_axpress_fallback() -> None:
    with patch("macbox.workflows.run_guest_jxa") as jxa:
        jxa.return_value = GuestCommandResult(exit_code=0, stdout='{"ok":true}\n', stderr="")
        result = click_ui_element(vm="macbox-test-001", app_name="Demo", role="button", title="Continue")

    script = jxa.call_args.kwargs["script"]
    assert result["ok"] is True
    assert "target.actions.byName('AXPress').perform()" in script
    assert "if (!pressed)" in script
    assert "target.click()" in script


def test_paste_scroll_and_drag_build_guest_automation() -> None:
    with patch("macbox.workflows.run_guest_applescript") as applescript:
        applescript.return_value = GuestCommandResult(exit_code=0, stdout="", stderr="")
        paste_text_in_guest(vm="macbox-test-001", text='hello "there"')
    assert 'set the clipboard to "hello \\"there\\""' in applescript.call_args.kwargs["script"]

    with patch("macbox.workflows.run_guest_jxa") as jxa:
        jxa.return_value = GuestCommandResult(exit_code=0, stdout="", stderr="")
        scroll_in_guest(vm="macbox-test-001", delta_x=1, delta_y=-4)
    assert "CGEventCreateScrollWheelEvent" in jxa.call_args.kwargs["script"]
    assert "ObjC.import('CoreGraphics')" in jxa.call_args.kwargs["script"]
    assert "var dx = 1;" in jxa.call_args.kwargs["script"]

    with patch("macbox.workflows.run_guest_jxa") as jxa:
        jxa.return_value = GuestCommandResult(exit_code=0, stdout="", stderr="")
        drag_in_guest(vm="macbox-test-001", start_x=1, start_y=2, end_x=3, end_y=4, steps=5)
    script = jxa.call_args.kwargs["script"]
    assert "ObjC.import('CoreGraphics')" in script
    assert "kCGEventLeftMouseDragged" in script
    assert "var steps = 5;" in script


def test_watch_sandbox_opens_only_vnc_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path / "state"))
    from macbox.runs import RunManager

    manager = RunManager.from_config()
    manager.create_run(
        "macbox-test-001",
        "macos-sequoia-clean",
        display_mode="vnc",
        watch={"mode": "vnc"},
    )
    with patch("macbox.workflows._backend") as backend, patch("macbox.workflows.run_command") as run:
        backend.return_value.ip.return_value = "192.168.64.2"
        result = watch_sandbox(vm="macbox-test-001", open_viewer=True)

    assert result["url"] == "vnc://192.168.64.2"
    run.assert_called_once_with(["open", "vnc://192.168.64.2"], timeout=30)


def test_open_guest_app_builds_open_command_with_args() -> None:
    with patch("macbox.workflows._guest_session") as guest_session:
        guest_session.return_value.exec.return_value = GuestCommandResult(
            exit_code=0,
            stdout="",
            stderr="",
        )

        result = open_guest_app(
            vm="macbox-test-001",
            app_path="/Applications/Ghostty.app",
            args=["-e", "zsh", "-lc", "echo hi"],
            new_instance=True,
        )

    assert result.argv == ["open", "-n", "/Applications/Ghostty.app", "--args", "-e", "zsh", "-lc", "echo hi"]
    guest_session.return_value.exec.assert_called_once_with(
        "open -n /Applications/Ghostty.app --args -e zsh -lc 'echo hi'",
        timeout=30,
    )

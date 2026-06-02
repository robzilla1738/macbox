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
    guest_exec_command,
    list_guest_processes,
    list_guest_windows,
    open_guest_app,
    resolve_profile,
    run_guest_applescript,
    run_release_gate,
    upload_artifact_to_guest,
)


def test_resolve_profile_rejects_conflicting_image_and_profile() -> None:
    with pytest.raises(SafetyError):
        resolve_profile(image="macos-sonoma-clean", profile="macos-sequoia-dark-mode")


def test_resolve_profile_rejects_unknown_profile_when_image_also_set() -> None:
    with pytest.raises(SafetyError):
        resolve_profile(image="macos-sequoia-clean", profile="unknown-profile")


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

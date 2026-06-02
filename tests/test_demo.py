"""Tests for macbox demo workflow."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from macbox.cli import main
from macbox.errors import AppCrashedError
from macbox.workflows import DemoResult, RunAppResult, StartResult, UploadResult, run_demo


runner = CliRunner()


def test_run_demo_success(tmp_path, monkeypatch) -> None:
    app = tmp_path / "Demo.app"
    app.mkdir()
    start = StartResult(
        vm="macbox-demo-deadbeef",
        image="macos-sequoia-clean",
        run_id="2026-06-02T00-00-00Z-macbox-demo-deadbeef",
        run_dir=str(tmp_path / "run"),
        headless=True,
    )
    upload = UploadResult(
        local_path=str(app),
        guest_path="/Users/admin/Desktop/Demo.app",
    )
    smoke = RunAppResult(
        launched=True,
        crashed=False,
        app_path="/Users/admin/Desktop/Demo.app",
        screenshot=str(tmp_path / "shot.png"),
        logs=str(tmp_path / "system.log"),
        crash_reports=[],
    )

    with patch("macbox.workflows.make_demo_vm_name", return_value="macbox-demo-deadbeef"), patch(
        "macbox.workflows.start_sandbox", return_value=start
    ), patch("macbox.workflows.upload_app_to_guest", return_value=upload), patch(
        "macbox.workflows.run_app_smoke", return_value=smoke
    ), patch("macbox.workflows.destroy_sandbox") as destroy:
        result = run_demo(app_path=app, image="macos-sequoia-clean", timeout=1)

    assert result.ok is True
    assert result.destroyed is True
    assert result.screenshot == str(tmp_path / "shot.png")
    destroy.assert_called_once_with(vm="macbox-demo-deadbeef")


def test_run_demo_crashed_still_destroys(tmp_path) -> None:
    app = tmp_path / "Demo.app"
    app.mkdir()
    start = StartResult(
        vm="macbox-demo-cafebabe",
        image="macos-sequoia-clean",
        run_id="run-id",
        run_dir=str(tmp_path),
        headless=True,
    )
    upload = UploadResult(local_path=str(app), guest_path="/Users/admin/Desktop/Demo.app")
    crash = AppCrashedError(
        "The app crashed after launch.",
        details={
            "launched": True,
            "crashed": True,
            "app_path": "/Users/admin/Desktop/Demo.app",
            "screenshot": str(tmp_path / "shot.png"),
            "logs": str(tmp_path / "log"),
            "crash_reports": [str(tmp_path / "x.crash")],
        },
    )

    with patch("macbox.workflows.make_demo_vm_name", return_value="macbox-demo-cafebabe"), patch(
        "macbox.workflows.start_sandbox", return_value=start
    ), patch("macbox.workflows.upload_app_to_guest", return_value=upload), patch(
        "macbox.workflows.run_app_smoke", side_effect=crash
    ), patch("macbox.workflows.destroy_sandbox") as destroy:
        result = run_demo(app_path=app, timeout=1)

    assert result.ok is False
    assert result.crashed is True
    assert result.destroyed is True
    destroy.assert_called_once()


def test_demo_cli_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))
    app = tmp_path / "Demo.app"
    app.mkdir()
    demo_result = DemoResult(
        ok=True,
        vm="macbox-demo-001",
        image="macos-sequoia-clean",
        run_id="run-id",
        run_dir=str(tmp_path / "run"),
        local_app=str(app),
        guest_app="/Users/admin/Desktop/Demo.app",
        launched=True,
        crashed=False,
        screenshot=str(tmp_path / "shot.png"),
        logs=str(tmp_path / "log"),
        crash_reports=[],
        destroyed=True,
    )
    with patch("macbox.cli.run_demo", return_value=demo_result):
        result = runner.invoke(main, ["demo", "--app", str(app), "--json"])

    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "demo"
    assert payload["data"]["artifacts"]["screenshot"] == str(tmp_path / "shot.png")

"""CLI contract and MCP validation tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from macbox.cli import main
from macbox.errors import AppCrashedError, SafetyError
from macbox.models import GuestCommandResult
from macbox.runner import ProcessResult
from macbox.safety import validate_vm_name
from macbox.workflows import GateResult


def _load_macbox_mcp():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "macbox_mcp_module",
        root / "mcp" / "macbox_mcp.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runner = CliRunner()


def test_doctor_json_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))
    missing_key = tmp_path / "missing-macbox-id"

    def fake_run(argv, **kwargs):
        if argv[0] == "which":
            return ProcessResult(argv=list(argv), exit_code=0, stdout=f"/usr/bin/{argv[1]}", stderr="")
        return ProcessResult(argv=list(argv), exit_code=1, stdout="", stderr="missing")

    def fake_expand(path):
        if "macbox_id" in str(path):
            return missing_key
        return Path(str(path)).expanduser().resolve()

    with patch("macbox.cli.run_command", side_effect=fake_run), patch(
        "macbox.cli.expand_path", side_effect=fake_expand
    ):
        result = runner.invoke(main, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["command"] == "doctor"
    assert "checks" in payload["data"]


def test_status_uses_tart_list(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))

    def fake_run(argv, **kwargs):
        if argv[:2] == ["tart", "list"]:
            if argv[-1] == "json":
                return ProcessResult(
                    argv=list(argv),
                    exit_code=0,
                    stdout='[{"name":"macos-sequoia-clean","state":"stopped"}]',
                    stderr="",
                )
        return ProcessResult(argv=list(argv), exit_code=0, stdout="", stderr="")

    with patch("macbox.tart_backend.run_command", side_effect=fake_run):
        result = runner.invoke(main, ["status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["vm_count"] == 1


def test_mcp_rejects_bad_vm_name() -> None:
    with pytest.raises(SafetyError):
        validate_vm_name("9bad")


def test_mcp_upload_app_builds_argv(tmp_path, monkeypatch) -> None:
    macbox_mcp = _load_macbox_mcp()
    app = tmp_path / "MyApp.app"
    app.mkdir()
    captured: dict[str, list[str]] = {}

    def fake_run_macbox(*args: str):
        captured["args"] = list(args)
        return {"ok": True, "command": "upload", "vm": "macbox-abcd1234", "data": {}, "warnings": [], "errors": []}

    monkeypatch.setattr(macbox_mcp, "_run_macbox", fake_run_macbox)
    macbox_mcp.upload_app("macbox-abcd1234", str(app))
    assert captured["args"][:2] == ["upload", "--name"]
    assert captured["args"][captured["args"].index("--path") + 1] == str(app.resolve())


def test_ssh_exec_uses_argument_array() -> None:
    from macbox.models import MacboxConfig
    from macbox.ssh import GuestSession

    captured: dict[str, list[str]] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return ProcessResult(argv=list(argv), exit_code=0, stdout="ok", stderr="")

    session = GuestSession(host="192.168.64.2", config=MacboxConfig())
    with patch("macbox.ssh.run_command", side_effect=fake_run):
        session.exec("uname -a", timeout=10)

    assert captured["argv"][0] == "ssh"
    assert captured["argv"][-2:] == ["--", "uname -a"]
    assert "-i" in captured["argv"]


def test_run_app_crash_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))

    with patch("macbox.cli.run_app_smoke", side_effect=AppCrashedError(
        "The app crashed after launch.",
        details={
            "launched": True,
            "crashed": True,
            "app_path": "/Users/admin/Desktop/MyApp.app",
            "screenshot": str(tmp_path / "shot.png"),
            "logs": str(tmp_path / "log"),
            "crash_reports": [str(tmp_path / "MyApp.crash")],
        },
    )):
        result = runner.invoke(
            main,
            [
                "run-app",
                "--name",
                "macbox-test-001",
                "--app",
                "/Users/admin/Desktop/MyApp.app",
                "--timeout",
                "1",
                "--json",
            ],
        )

    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["data"]["crashed"] is True
    assert payload["errors"][0]["code"] == "APP_CRASHED"


def test_profiles_json_shape() -> None:
    result = runner.invoke(main, ["profiles", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "profiles" in payload["data"]
    assert "macos-sequoia-clean" in payload["data"]["profiles"]


def test_report_command_reads_run_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))
    run_id = "2026-06-02T00-00-00Z-macbox-test-001"
    report_dir = tmp_path / "runs" / run_id / "reports"
    report_dir.mkdir(parents=True)
    (tmp_path / "runs" / run_id / "metadata.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "vm": "macbox-test-001",
                "image": "macos-sequoia-clean",
                "created_at": "2026-06-02T00:00:00Z",
                "status": "running",
                "artifacts": {
                    "screenshots": [],
                    "logs": [],
                    "crashes": [],
                    "uploads": [],
                    "reports": [],
                    "diagnostics": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "report.json").write_text(
        json.dumps({"run_id": run_id, "verdict": "passed"}),
        encoding="utf-8",
    )

    result = runner.invoke(main, ["report", run_id, "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["data"]["run_id"] == run_id
    assert payload["data"]["verdict"] == "passed"


def test_gate_json_shape(tmp_path, monkeypatch) -> None:
    app = tmp_path / "Demo.app"
    app.mkdir()
    gate_result = GateResult(
        ok=True,
        vm="macbox-gate-001",
        image="macos-sequoia-clean",
        run_id="run-id",
        run_dir=str(tmp_path / "run"),
        artifact_path=str(app),
        artifact_type=".app",
        report={"run_id": "run-id", "verdict": "passed", "report_path": str(tmp_path / "report.json")},
        failed_requirements=[],
        destroyed=True,
        warnings=[],
        errors=[],
    )
    with patch("macbox.cli.run_release_gate", return_value=gate_result):
        result = runner.invoke(main, ["gate", "--artifact", str(app), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["artifact_type"] == ".app"
    assert payload["data"]["report"]["verdict"] == "passed"


def test_matrix_json_shape(tmp_path) -> None:
    app = tmp_path / "Demo.app"
    app.mkdir()
    with patch("macbox.cli.run_matrix", return_value=(False, [])):
        result = runner.invoke(
            main,
            [
                "matrix",
                "--images",
                "macos-sequoia-clean,macos-sonoma-clean",
                "--artifact",
                str(app),
                "--json",
            ],
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["command"] == "matrix"


def test_run_on_warm_json_shape(tmp_path) -> None:
    app = tmp_path / "Demo.app"
    app.mkdir()
    with patch(
        "macbox.cli.run_on_warm",
        return_value=type(
            "WarmResult",
            (),
            {
                "upload": type(
                    "Upload",
                    (),
                    {
                        "local_path": str(app),
                        "guest_path": "/Users/admin/Desktop/Demo.app",
                    },
                )(),
                "smoke": type(
                    "Smoke",
                    (),
                    {
                        "launched": True,
                        "crashed": False,
                        "app_path": "/Users/admin/Desktop/Demo.app",
                        "screenshot": str(tmp_path / "shot.png"),
                        "logs": str(tmp_path / "system.log"),
                        "crash_reports": [],
                        "report": {"run_id": "run-id", "verdict": "passed"},
                        "warnings": [],
                    },
                )(),
            },
        )(),
    ):
        result = runner.invoke(main, ["run-on-warm", "--name", "macbox-warm-001", "--app", str(app), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "run-on-warm"
    assert payload["data"]["report"]["verdict"] == "passed"

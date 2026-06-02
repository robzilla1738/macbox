"""CLI contract and MCP validation tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from macbox.cli import main
from macbox.errors import SafetyError
from macbox.models import GuestCommandResult
from macbox.runner import ProcessResult
from macbox.safety import validate_vm_name


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

    def fake_run(argv, **kwargs):
        if argv[0] == "which":
            return ProcessResult(argv=list(argv), exit_code=0, stdout=f"/usr/bin/{argv[1]}", stderr="")
        return ProcessResult(argv=list(argv), exit_code=1, stdout="", stderr="missing")

    with patch("macbox.cli.run_command", side_effect=fake_run):
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

    class FakeSession:
        def remote_is_directory(self, path: str) -> bool:
            return True

        def exec(self, command: str, timeout: int):
            return GuestCommandResult(exit_code=0, stdout="", stderr="")

        def list_remote_files(self, remote_dir: str) -> list[str]:
            if not hasattr(self, "called"):
                self.called = True
                return ["Old.crash"]
            return ["Old.crash", "MyApp_2026.crash"]

        def download(self, guest_path: str, local_path: Path) -> None:
            Path(local_path).write_bytes(b"data")

    with patch("macbox.cli._guest_session", return_value=FakeSession()), patch(
        "macbox.cli.time.sleep", return_value=None
    ):
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

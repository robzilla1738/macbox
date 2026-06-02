"""Hardening and safety validation tests."""

from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from macbox.cli import main
from macbox.errors import MacboxError, TartError
from macbox.models import MacboxConfig
from macbox.redact import redact_secrets
from macbox.runner import ProcessResult, run_command
from macbox.runs import RunManager
from macbox.safety import (
    is_disposable_vm_name,
    validate_disposable_vm_operation,
    validate_start_vm,
    validate_upload_path,
)
from macbox.tart_backend import TartBackend


runner = CliRunner()


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


def test_subprocess_never_uses_shell_true() -> None:
    runner_source = Path(__file__).resolve().parents[1] / "src" / "macbox" / "runner.py"
    text = runner_source.read_text(encoding="utf-8")
    assert "shell=True" not in text
    assert "shell = True" not in text


def test_run_command_uses_argument_array() -> None:
    with patch("macbox.runner.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo", "hi"],
            returncode=0,
            stdout="hi",
            stderr="",
        )
        run_command(["echo", "hi"])
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs.get("shell") is None
    assert mock_run.call_args.args[0] == ["echo", "hi"]


def test_error_details_redact_secrets() -> None:
    err = TartError(
        "failed",
        details={"stderr": "password=supersecret", "stdout": "token=abc123"},
    )
    payload = err.to_dict()
    assert "supersecret" not in json.dumps(payload)
    assert "[REDACTED]" in payload["details"]["stderr"]


def test_protected_base_image_cannot_be_destroyed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))
    result = runner.invoke(
        main,
        ["destroy", "--name", "macos-sequoia-clean", "--json"],
    )
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "SAFETY_ERROR"
    assert "protected" in payload["errors"][0]["message"].lower()


def test_start_refuses_same_name_as_base_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))
    result = runner.invoke(
        main,
        [
            "start",
            "--image",
            "macos-sequoia-clean",
            "--name",
            "macos-sequoia-clean",
            "--headless",
            "--json",
        ],
    )
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "SAFETY_ERROR"


def test_tart_clone_refuses_self_clone() -> None:
    backend = TartBackend()
    with pytest.raises(TartError) as exc:
        backend.clone("macos-sequoia-clean", "macos-sequoia-clean")
    assert exc.value.code == "TART_ERROR"


def test_artifacts_live_under_runs_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))
    manager = RunManager.from_config()
    metadata = manager.create_run("macbox-test-001", "macos-sequoia-clean")
    screenshot = manager.artifact_path("macbox-test-001", "screenshots", "launch.png")
    assert str(tmp_path.resolve()) in str(screenshot)
    assert metadata.run_id in str(screenshot)
    assert screenshot.parent.name == "screenshots"


def test_destroy_does_not_create_new_run_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))
    manager = RunManager.from_config()
    metadata = manager.create_run("macbox-test-001", "macos-sequoia-clean")
    run_count_before = len(list((tmp_path / "runs").iterdir()))

    def fake_run(argv, **kwargs):
        return ProcessResult(argv=list(argv), exit_code=0, stdout="", stderr="")

    with patch("macbox.tart_backend.run_command", side_effect=fake_run), patch(
        "macbox.tart_backend.start_background_command"
    ):
        result = runner.invoke(main, ["destroy", "--name", "macbox-test-001", "--json"])

    assert result.exit_code == 0
    run_count_after = len(list((tmp_path / "runs").iterdir()))
    assert run_count_after == run_count_before
    updated = manager.read_metadata(metadata.run_id)
    assert updated.status == "destroyed"


def test_mcp_has_no_host_exec_tool() -> None:
    macbox_mcp = _load_macbox_mcp()
    tool_names = {
        fn.__name__
        for name, fn in inspect.getmembers(macbox_mcp, inspect.isfunction)
        if hasattr(fn, "__name__") and not name.startswith("_")
    }
    assert "exec" not in tool_names
    assert "run_shell" not in tool_names
    public_tools = {
        "macbox_status",
        "list_images",
        "create_sandbox",
        "upload_app",
        "upload_pkg",
        "run_app_smoke_test",
        "collect_logs",
        "take_screenshot",
        "collect_crashes",
        "reset_sandbox",
        "destroy_sandbox",
    }
    assert public_tools.issubset(tool_names)


def test_mcp_upload_rejects_secret_and_unsupported(tmp_path) -> None:
    macbox_mcp = _load_macbox_mcp()
    secret = tmp_path / "api-token.txt"
    secret.write_text("x", encoding="utf-8")
    with pytest.raises(MacboxError):
        macbox_mcp.upload_app("macbox-abcd1234", str(secret))

    txt = tmp_path / "notes.txt"
    txt.write_text("x", encoding="utf-8")
    with pytest.raises(MacboxError):
        macbox_mcp.upload_app("macbox-abcd1234", str(txt))


def test_ssh_batchmode_enabled() -> None:
    from macbox.ssh import GuestSession

    session = GuestSession(host="192.168.64.2", config=MacboxConfig())
    argv = session._base_ssh_argv()
    assert "BatchMode=yes" in argv
    assert "-i" in argv


def test_disposable_vm_naming() -> None:
    assert is_disposable_vm_name("macbox-deadbeef")
    assert not is_disposable_vm_name("macos-sequoia-clean")


def test_cli_failure_emits_valid_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))
    result = runner.invoke(main, ["destroy", "--name", "macos-sequoia-clean", "--json"])
    payload = json.loads(result.output)
    assert set(payload.keys()) == {"ok", "command", "vm", "data", "warnings", "errors"}
    assert payload["ok"] is False


@pytest.mark.parametrize(
    "command,args",
    [
        ("status", ["--json"]),
        ("images", ["--json"]),
        ("prepare", ["--image", "macos-sequoia-clean", "--json"]),
        ("doctor", ["--json"]),
    ],
)
def test_readonly_commands_emit_json(command, args, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))

    def fake_run(argv, **kwargs):
        if argv[0] == "which":
            return ProcessResult(argv=list(argv), exit_code=0, stdout=f"/usr/bin/{argv[1]}", stderr="")
        if argv[:2] == ["tart", "list"]:
            return ProcessResult(
                argv=list(argv),
                exit_code=0,
                stdout='[{"name":"macos-sequoia-clean","state":"stopped"}]',
                stderr="",
            )
        return ProcessResult(argv=list(argv), exit_code=0, stdout="", stderr="")

    patches = [
        patch("macbox.cli.run_command", side_effect=fake_run),
        patch("macbox.tart_backend.run_command", side_effect=fake_run),
    ]
    with patches[0], patches[1]:
        result = runner.invoke(main, [command, *args])
    payload = json.loads(result.output)
    assert "ok" in payload
    assert payload["command"] == command


def test_config_always_protects_default_image() -> None:
    from macbox.config import normalize_config

    config = normalize_config(MacboxConfig(default_image="my-base"))
    assert "my-base" in config.protected_images


def test_validate_start_and_destroy_rules() -> None:
    from macbox.config import normalize_config

    config = normalize_config(MacboxConfig(default_image="my-base"))
    with pytest.raises(MacboxError):
        validate_start_vm("my-base", "my-base", config)
    with pytest.raises(MacboxError):
        validate_disposable_vm_operation("my-base", config, operation="destroy")


def test_redact_secrets_helper() -> None:
    assert redact_secrets("password=hunter2") == "[REDACTED]"

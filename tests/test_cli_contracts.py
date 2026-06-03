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


def _load_macbox_mcp(profile: str | None = None):
    root = Path(__file__).resolve().parents[1]
    if profile is None:
        module_name = "macbox_mcp_module"
    else:
        module_name = f"macbox_mcp_module_{profile}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        root / "mcp" / "macbox_mcp.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    if profile is not None:
        import os

        old_profile = os.environ.get("MACBOX_MCP_PROFILE")
        os.environ["MACBOX_MCP_PROFILE"] = profile
        try:
            spec.loader.exec_module(module)
        finally:
            if old_profile is None:
                os.environ.pop("MACBOX_MCP_PROFILE", None)
            else:
                os.environ["MACBOX_MCP_PROFILE"] = old_profile
        return module
    spec.loader.exec_module(module)
    return module


def _load_mcp_wrapper(script_name: str):
    import os
    import sys

    root = Path(__file__).resolve().parents[1]
    module_name = f"macbox_{script_name.replace('.', '_')}_module"
    spec = importlib.util.spec_from_file_location(
        module_name,
        root / "mcp" / script_name,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    old_profile = os.environ.get("MACBOX_MCP_PROFILE")
    old_path = list(sys.path)
    old_macbox_mcp = sys.modules.pop("macbox_mcp", None)
    os.environ.pop("MACBOX_MCP_PROFILE", None)
    sys.path.insert(0, str(root / "mcp"))
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = old_path
        sys.modules.pop("macbox_mcp", None)
        if old_macbox_mcp is not None:
            sys.modules["macbox_mcp"] = old_macbox_mcp
        if old_profile is None:
            os.environ.pop("MACBOX_MCP_PROFILE", None)
        else:
            os.environ["MACBOX_MCP_PROFILE"] = old_profile


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


def test_mcp_exec_in_guest_builds_argv(monkeypatch) -> None:
    macbox_mcp = _load_macbox_mcp()
    captured: dict[str, list[str]] = {}

    def fake_run_macbox(*args: str):
        captured["args"] = list(args)
        return {"ok": True, "command": "exec", "vm": "macbox-abcd1234", "data": {}, "warnings": [], "errors": []}

    monkeypatch.setattr(macbox_mcp, "_run_macbox", fake_run_macbox)
    macbox_mcp.exec_in_guest("macbox-abcd1234", "uname -a", timeout_seconds=25)
    assert captured["args"] == [
        "exec",
        "--name",
        "macbox-abcd1234",
        "--command",
        "uname -a",
        "--timeout",
        "25",
        "--json",
    ]


def test_mcp_open_guest_app_builds_argv(monkeypatch) -> None:
    macbox_mcp = _load_macbox_mcp()
    captured: dict[str, list[str]] = {}

    def fake_run_macbox(*args: str):
        captured["args"] = list(args)
        return {"ok": True, "command": "open-app", "vm": "macbox-abcd1234", "data": {}, "warnings": [], "errors": []}

    monkeypatch.setattr(macbox_mcp, "_run_macbox", fake_run_macbox)
    macbox_mcp.open_guest_app(
        "macbox-abcd1234",
        "/Applications/Ghostty.app",
        args=["-e", "zsh"],
        new_instance=False,
        wait_seconds=1.5,
    )
    assert captured["args"] == [
        "open-app",
        "--name",
        "macbox-abcd1234",
        "--app",
        "/Applications/Ghostty.app",
        "--wait-seconds",
        "1.5",
        "--reuse-instance",
        "--arg",
        "-e",
        "--arg",
        "zsh",
        "--json",
    ]


def test_mcp_create_sandbox_accepts_display_mode(monkeypatch) -> None:
    macbox_mcp = _load_macbox_mcp()
    captured: dict[str, list[str]] = {}

    def fake_run_macbox(*args: str):
        captured["args"] = list(args)
        return {"ok": True, "command": "start", "vm": "macbox-abcd1234", "data": {}, "warnings": [], "errors": []}

    monkeypatch.setattr(macbox_mcp, "_run_macbox", fake_run_macbox)
    macbox_mcp.create_sandbox("macos-sequoia-clean", display_mode="vnc")
    assert "--display-mode" in captured["args"]
    assert captured["args"][captured["args"].index("--display-mode") + 1] == "vnc"
    assert "--headless" not in captured["args"]


def test_mcp_profiles_split_core_and_power_tools() -> None:
    full = _load_macbox_mcp(profile="all")
    core = _load_macbox_mcp(profile="core")
    power = _load_macbox_mcp(profile="power")

    full_names = set(full.active_tool_names())
    core_names = set(core.active_tool_names())
    power_names = set(power.active_tool_names())

    assert len(full_names) == 47
    assert len(core_names) == 17
    assert core_names < full_names
    assert power_names < full_names
    assert core_names.isdisjoint(power_names)
    assert core_names | power_names == full_names

    assert {
        "macbox_status",
        "create_sandbox",
        "upload_app",
        "run_app_smoke_test",
        "take_screenshot",
        "destroy_sandbox",
    }.issubset(core_names)
    assert {
        "run_script_in_guest",
        "observe_guest",
        "inspect_ui_tree",
        "click_ui_element",
        "drag_in_guest",
        "run_release_matrix",
    }.issubset(power_names)


def test_mcp_profile_wrappers_select_expected_surfaces() -> None:
    core_wrapper = _load_mcp_wrapper("macbox_core_mcp.py")
    power_wrapper = _load_mcp_wrapper("macbox_power_mcp.py")

    assert core_wrapper.macbox_mcp.MCP_PROFILE == "core"
    assert power_wrapper.macbox_mcp.MCP_PROFILE == "power"
    assert len(core_wrapper.macbox_mcp.active_tool_names()) == 17
    assert "run_app_smoke_test" in core_wrapper.macbox_mcp.active_tool_names()
    assert "observe_guest" not in core_wrapper.macbox_mcp.active_tool_names()
    assert "observe_guest" in power_wrapper.macbox_mcp.active_tool_names()
    assert "run_app_smoke_test" not in power_wrapper.macbox_mcp.active_tool_names()


def test_mcp_new_guest_tools_build_argv(monkeypatch) -> None:
    macbox_mcp = _load_macbox_mcp()
    calls: list[list[str]] = []

    def fake_run_macbox(*args: str):
        calls.append(list(args))
        return {"ok": True, "command": args[0], "vm": "macbox-abcd1234", "data": {}, "warnings": [], "errors": []}

    monkeypatch.setattr(macbox_mcp, "_run_macbox", fake_run_macbox)
    macbox_mcp.watch_sandbox("macbox-abcd1234", open_viewer=True)
    macbox_mcp.prepare_agent_workspace("macbox-abcd1234", reset=True)
    macbox_mcp.run_script_in_guest("macbox-abcd1234", "echo hi", language="shell", timeout_seconds=42)
    macbox_mcp.observe_guest("macbox-abcd1234", process_filter="Finder")
    macbox_mcp.inspect_ui_tree("macbox-abcd1234", app_name="Finder", max_depth=2, max_items=25)
    macbox_mcp.click_ui_element("macbox-abcd1234", app_name="Finder", role="button", title="OK", exact=True)
    macbox_mcp.paste_text_in_guest("macbox-abcd1234", "hello")
    macbox_mcp.scroll_in_guest("macbox-abcd1234", delta_x=1, delta_y=-3)
    macbox_mcp.drag_in_guest("macbox-abcd1234", 1, 2, 3, 4, steps=5)

    assert calls[0] == ["watch", "--name", "macbox-abcd1234", "--open", "--json"]
    assert calls[1] == ["prepare-agent-workspace", "--name", "macbox-abcd1234", "--reset", "--json"]
    assert calls[2] == [
        "run-script",
        "--name",
        "macbox-abcd1234",
        "--script",
        "echo hi",
        "--language",
        "shell",
        "--timeout",
        "42",
        "--json",
    ]
    assert calls[3] == ["observe", "--name", "macbox-abcd1234", "--process-filter", "Finder", "--json"]
    assert calls[4] == [
        "inspect-ui-tree",
        "--name",
        "macbox-abcd1234",
        "--max-depth",
        "2",
        "--max-items",
        "25",
        "--app",
        "Finder",
        "--json",
    ]
    assert "--exact" in calls[5]
    assert calls[6] == ["paste-text", "--name", "macbox-abcd1234", "--text", "hello", "--timeout", "30", "--json"]
    assert calls[7] == [
        "scroll",
        "--name",
        "macbox-abcd1234",
        "--delta-x",
        "1",
        "--delta-y",
        "-3",
        "--timeout",
        "30",
        "--json",
    ]
    assert calls[8] == [
        "drag",
        "--name",
        "macbox-abcd1234",
        "--start-x",
        "1",
        "--start-y",
        "2",
        "--end-x",
        "3",
        "--end-y",
        "4",
        "--steps",
        "5",
        "--timeout",
        "30",
        "--json",
    ]


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

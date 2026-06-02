"""Tests for macbox safety validation."""

from pathlib import Path

import pytest

from macbox.errors import SafetyError
from macbox.safety import validate_upload_path, validate_vm_name


def test_vm_name_validation() -> None:
    assert validate_vm_name("macbox-test-001") == "macbox-test-001"
    with pytest.raises(SafetyError):
        validate_vm_name("001-bad")


def test_reject_secret_paths(tmp_path) -> None:
    secret_file = tmp_path / "my-token.txt"
    secret_file.write_text("secret", encoding="utf-8")
    with pytest.raises(SafetyError):
        validate_upload_path(secret_file)

    env_file = tmp_path / ".env"
    env_file.write_text("KEY=1", encoding="utf-8")
    with pytest.raises(SafetyError):
        validate_upload_path(env_file)


def test_allow_override_for_secrets(tmp_path) -> None:
    secret_file = tmp_path / "my-token.txt"
    secret_file.write_text("secret", encoding="utf-8")
    with pytest.raises(SafetyError):
        validate_upload_path(secret_file, allow_override=False)

    # Still fails because not .app/.pkg
    with pytest.raises(SafetyError):
        validate_upload_path(secret_file, allow_override=True)


def test_mcp_upload_requires_supported_artifact(tmp_path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("hello", encoding="utf-8")
    with pytest.raises(SafetyError):
        validate_upload_path(txt, mcp_mode=True)

    app_dir = tmp_path / "MyApp.app"
    app_dir.mkdir()
    assert validate_upload_path(app_dir, mcp_mode=True) == app_dir.resolve()

    pkg = tmp_path / "MyApp.pkg"
    pkg.write_bytes(b"pkg")
    assert validate_upload_path(pkg, mcp_mode=True) == pkg.resolve()

    dmg = tmp_path / "MyApp.dmg"
    dmg.write_bytes(b"dmg")
    assert validate_upload_path(dmg, mcp_mode=True) == dmg.resolve()


def test_reject_ssh_directory(tmp_path) -> None:
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    key = ssh_dir / "id_rsa"
    key.write_text("key", encoding="utf-8")
    with pytest.raises(SafetyError):
        validate_upload_path(key)

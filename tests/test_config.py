"""Tests for macbox config."""

from macbox.config import ensure_state_layout, get_state_dir, load_config, save_config
from macbox.models import MacboxConfig


def test_default_config_and_state_layout(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))

    config = ensure_state_layout()
    assert config.guest_user == "admin"
    assert get_state_dir(config) == tmp_path.resolve()
    assert (tmp_path / "runs").is_dir()
    assert (tmp_path / "tmp").is_dir()
    assert (tmp_path / "config.json").is_file()

    reloaded = load_config()
    assert reloaded.default_image == "macos-sequoia-clean"


def test_save_config_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))
    config = MacboxConfig(default_image="custom-image")
    path = save_config(config)
    assert path.exists()
    loaded = load_config()
    assert loaded.default_image == "custom-image"

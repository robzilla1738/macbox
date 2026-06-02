"""Configuration loading and state directory management."""

from __future__ import annotations

import json
import os
from pathlib import Path

from macbox.errors import ConfigError
from macbox.models import MacboxConfig, expand_path


def state_dir_from_env() -> Path | None:
    override = os.environ.get("MACBOX_STATE_DIR")
    if not override:
        return None
    return expand_path(override)


def get_state_dir(config: MacboxConfig) -> Path:
    override = state_dir_from_env()
    if override is not None:
        return override
    return expand_path(config.state_dir)


def config_path(config: MacboxConfig) -> Path:
    return get_state_dir(config) / "config.json"


def normalize_config(config: MacboxConfig) -> MacboxConfig:
    protected = list(dict.fromkeys([*config.protected_images, config.default_image]))
    if protected == config.protected_images:
        return config
    return config.model_copy(update={"protected_images": protected})


def load_config() -> MacboxConfig:
    override = state_dir_from_env()
    if override is not None:
        path = override / "config.json"
        if path.exists():
            return normalize_config(_read_config(path))
        return normalize_config(MacboxConfig(state_dir=str(override)))

    default = MacboxConfig()
    path = expand_path(default.state_dir) / "config.json"
    if path.exists():
        return normalize_config(_read_config(path))
    return normalize_config(default)


def save_config(config: MacboxConfig) -> Path:
    cfg = normalize_config(config)
    state_dir = get_state_dir(cfg)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "runs").mkdir(exist_ok=True)
    (state_dir / "tmp").mkdir(exist_ok=True)

    path = state_dir / "config.json"
    payload = cfg.model_dump()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def ensure_state_layout(config: MacboxConfig | None = None) -> MacboxConfig:
    cfg = config or load_config()
    state_dir = get_state_dir(cfg)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "runs").mkdir(exist_ok=True)
    (state_dir / "tmp").mkdir(exist_ok=True)
    if not (state_dir / "config.json").exists():
        save_config(cfg)
    return cfg


def _read_config(path: Path) -> MacboxConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"Failed to read config at {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    return MacboxConfig.model_validate(raw)

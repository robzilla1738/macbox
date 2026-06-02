"""Safety validation for uploads, paths, and MCP inputs."""

from __future__ import annotations

import re
from pathlib import Path

from macbox.errors import SafetyError
from macbox.models import expand_path

VM_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")

DENIED_PATH_PARTS = {
    ".ssh",
    ".gnupg",
    ".env",
    "keychains",
    "Keychains",
}

DENIED_PATH_SUBSTRINGS = (
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Firefox",
    "Library/Application Support/Arc",
    "Library/Application Support/BraveSoftware",
)

SECRET_NAME_PATTERNS = (
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)token"),
    re.compile(r"(?i)credential"),
)

ALLOWED_UPLOAD_SUFFIXES = (".app", ".pkg")
DISPOSABLE_VM_PREFIX = "macbox-"


def protected_image_names(config) -> set[str]:
    return set(config.protected_images) | {config.default_image}


def validate_start_vm(image: str, name: str, config) -> None:
    validate_vm_name(name)
    if name == image:
        raise SafetyError(
            "Sandbox VM name must differ from base image to avoid modifying the template",
            details={"image": image, "name": name},
        )
    if name in protected_image_names(config):
        raise SafetyError(
            f"Refusing to start sandbox using protected base image VM name: {name}",
            details={"protected_images": sorted(protected_image_names(config))},
        )


def validate_disposable_vm_operation(name: str, config, *, operation: str) -> str:
    validate_vm_name(name)
    if name in protected_image_names(config):
        raise SafetyError(
            f"Refusing to {operation} protected base image VM: {name}",
            details={
                "operation": operation,
                "protected_images": sorted(protected_image_names(config)),
            },
        )
    return name


def is_disposable_vm_name(name: str) -> bool:
    return name.startswith(DISPOSABLE_VM_PREFIX)


def validate_vm_name(name: str) -> str:
    if not VM_NAME_RE.match(name):
        raise SafetyError(
            f"Invalid VM name: {name!r}",
            details={"pattern": VM_NAME_RE.pattern},
        )
    return name


def _path_parts(path: Path) -> set[str]:
    return set(path.parts)


def is_denied_secret_path(path: Path, *, allow_override: bool = False) -> bool:
    if allow_override:
        return False

    resolved = expand_path(path)
    parts = _path_parts(resolved)

    if parts & DENIED_PATH_PARTS:
        return True

    path_str = str(resolved)
    for substring in DENIED_PATH_SUBSTRINGS:
        if substring in path_str:
            return True

    name = resolved.name
    for pattern in SECRET_NAME_PATTERNS:
        if pattern.search(name):
            return True

    if name == ".env" or name.endswith(".env"):
        return True

    return False


def validate_upload_path(
    local_path: str | Path,
    *,
    allow_override: bool = False,
    mcp_mode: bool = False,
) -> Path:
    resolved = expand_path(local_path)

    if not resolved.exists():
        raise SafetyError(
            f"Upload path does not exist: {resolved}",
            details={"path": str(resolved)},
        )

    if is_denied_secret_path(resolved, allow_override=allow_override):
        raise SafetyError(
            f"Refusing to upload sensitive path: {resolved}",
            details={"path": str(resolved)},
        )

    suffix = _upload_suffix(resolved)
    if mcp_mode and suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise SafetyError(
            f"MCP uploads must be .app or .pkg bundles/files: {resolved}",
            details={"path": str(resolved), "allowed": list(ALLOWED_UPLOAD_SUFFIXES)},
        )

    if suffix not in ALLOWED_UPLOAD_SUFFIXES and mcp_mode is False:
        raise SafetyError(
            f"Upload must be a .app bundle or .pkg file: {resolved}",
            details={"path": str(resolved), "allowed": list(ALLOWED_UPLOAD_SUFFIXES)},
        )

    return resolved


def _upload_suffix(path: Path) -> str:
    if path.suffix == ".pkg":
        return ".pkg"
    if path.suffix == ".app" or path.name.endswith(".app"):
        return ".app"
    if path.is_dir() and path.name.endswith(".app"):
        return ".app"
    return path.suffix


def validate_guest_path(guest_path: str) -> str:
    if not guest_path.startswith("/"):
        raise SafetyError(
            f"Guest path must be absolute: {guest_path!r}",
            details={"path": guest_path},
        )
    if "\x00" in guest_path:
        raise SafetyError("Guest path contains invalid characters")
    return guest_path


def validate_guest_command(command: str) -> str:
    if not command.strip():
        raise SafetyError("Guest command must not be empty")
    return command

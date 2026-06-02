"""Typed errors for macbox."""

from __future__ import annotations

from typing import Any

from macbox.redact import redact_secrets, redact_value


class MacboxError(Exception):
    """Base exception with a stable error code."""

    code: str = "MACBOX_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": redact_secrets(self.message),
            "details": redact_value(self.details),
        }


class ConfigError(MacboxError):
    code = "CONFIG_ERROR"


class SafetyError(MacboxError):
    code = "SAFETY_ERROR"


class TartError(MacboxError):
    code = "TART_ERROR"


class SSHError(MacboxError):
    code = "SSH_ERROR"


class VMNotReadyError(MacboxError):
    code = "VM_NOT_READY"


class AppError(MacboxError):
    code = "APP_ERROR"


class AppCrashedError(MacboxError):
    code = "APP_CRASHED"


class ProcessError(MacboxError):
    code = "PROCESS_ERROR"


class RunError(MacboxError):
    code = "RUN_ERROR"

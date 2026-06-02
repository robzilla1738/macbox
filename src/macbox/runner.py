"""Centralized subprocess execution."""

from __future__ import annotations

import subprocess
from typing import Mapping, Sequence

from macbox.errors import ProcessError
from macbox.models import ProcessResult
from macbox.redact import redact_secrets


def run_command(
    argv: Sequence[str],
    *,
    timeout: int | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    input_text: str | None = None,
) -> ProcessResult:
    if not argv:
        raise ProcessError("Command argv must not be empty")

    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=None if env is None else dict(env),
            cwd=cwd,
            input=input_text,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = redact_secrets(exc.stdout or "")
        stderr = redact_secrets(exc.stderr or "")
        raise ProcessError(
            f"Command timed out after {timeout}s: {' '.join(argv)}",
            details={
                "argv": list(argv),
                "stdout": stdout,
                "stderr": stderr,
            },
        ) from exc
    except OSError as exc:
        raise ProcessError(
            f"Failed to execute command: {' '.join(argv)}",
            details={"argv": list(argv), "reason": str(exc)},
        ) from exc

    return ProcessResult(
        argv=list(argv),
        exit_code=completed.returncode,
        stdout=redact_secrets(completed.stdout or ""),
        stderr=redact_secrets(completed.stderr or ""),
        timed_out=False,
    )


def start_background_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.Popen[str]:
    if not argv:
        raise ProcessError("Command argv must not be empty")
    try:
        return subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=None if env is None else dict(env),
            cwd=cwd,
            start_new_session=True,
        )
    except OSError as exc:
        raise ProcessError(
            f"Failed to start background command: {' '.join(argv)}",
            details={"argv": list(argv), "reason": str(exc)},
        ) from exc

"""Pydantic models for macbox JSON contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class MacboxResponse(BaseModel):
    ok: bool
    command: str
    vm: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[ErrorDetail] = Field(default_factory=list)


class MacboxConfig(BaseModel):
    state_dir: str = "~/.macbox"
    guest_user: str = "admin"
    ssh_identity_file: str = "~/.ssh/macbox_id"
    ssh_known_hosts_policy: str = "accept-new"
    default_image: str = "macos-sequoia-clean"
    protected_images: list[str] = Field(default_factory=list)
    tart_path: str = "tart"
    run_app_timeout_seconds: int = 120
    log_collect_duration: str = "5m"
    profiles: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RunArtifacts(BaseModel):
    screenshots: list[str] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    crashes: list[str] = Field(default_factory=list)
    uploads: list[str] = Field(default_factory=list)
    downloads: list[str] = Field(default_factory=list)
    reports: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class RunMetadata(BaseModel):
    run_id: str
    vm: str
    image: str
    created_at: str
    status: Literal["running", "stopped", "destroyed", "failed"] = "running"
    artifacts: RunArtifacts = Field(default_factory=RunArtifacts)


class GuestCommandResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class ProcessResult(BaseModel):
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class VMInfo(BaseModel):
    name: str
    state: str = "unknown"
    source: str | None = None


class DoctorCheck(BaseModel):
    name: str
    ok: bool
    message: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_run_id(vm_name: str, when: datetime | None = None) -> str:
    ts = (when or datetime.now(timezone.utc)).replace(microsecond=0)
    stamp = ts.strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{stamp}-{vm_name}"


def expand_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()

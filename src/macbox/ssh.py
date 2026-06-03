"""SSH guest session abstraction."""

from __future__ import annotations

import shlex
from pathlib import Path

from macbox.config import get_state_dir
from macbox.errors import SSHError
from macbox.models import GuestCommandResult, MacboxConfig, expand_path
from macbox.runner import run_command


class GuestSession:
    def __init__(
        self,
        *,
        host: str,
        config: MacboxConfig,
    ) -> None:
        self.host = host
        self.config = config
        self.user = config.guest_user
        self.identity = expand_path(config.ssh_identity_file)

    def _known_hosts_path(self) -> str:
        path = get_state_dir(self.config) / "known_hosts"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        return str(path)

    def _base_ssh_argv(self) -> list[str]:
        return [
            "ssh",
            "-i",
            str(self.identity),
            "-o",
            "BatchMode=yes",
            "-o",
            f"StrictHostKeyChecking={self.config.ssh_known_hosts_policy}",
            "-o",
            f"UserKnownHostsFile={self._known_hosts_path()}",
            f"{self.user}@{self.host}",
        ]

    def _base_scp_argv(self) -> list[str]:
        return [
            "scp",
            "-i",
            str(self.identity),
            "-o",
            "BatchMode=yes",
            "-o",
            f"StrictHostKeyChecking={self.config.ssh_known_hosts_policy}",
            "-o",
            f"UserKnownHostsFile={self._known_hosts_path()}",
        ]

    def exec(self, command: str, timeout: int) -> GuestCommandResult:
        argv = self._base_ssh_argv() + ["--", command]
        result = run_command(argv, timeout=timeout)
        return GuestCommandResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=False,
        )

    def upload(self, local_path: Path, guest_path: str) -> None:
        local = expand_path(local_path)
        remote = f"{self.user}@{self.host}:{guest_path}"
        if local.is_dir():
            argv = self._base_scp_argv() + ["-r", str(local), remote]
        else:
            argv = self._base_scp_argv() + [str(local), remote]
        result = run_command(argv, timeout=600)
        if result.exit_code != 0:
            raise SSHError(
                f"Failed to upload {local} to {guest_path}",
                details={
                    "argv": argv,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )

    def download(self, guest_path: str, local_path: Path, *, recursive: bool = False) -> None:
        local = expand_path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        remote = f"{self.user}@{self.host}:{guest_path}"
        scp_args = ["-r"] if recursive else []
        argv = self._base_scp_argv() + scp_args + [remote, str(local)]
        result = run_command(argv, timeout=600)
        if result.exit_code != 0:
            raise SSHError(
                f"Failed to download {guest_path} to {local}",
                details={
                    "argv": argv,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )

    def list_remote_files(self, remote_dir: str) -> list[str]:
        quoted = shlex.quote(remote_dir)
        result = self.exec(f"ls -1 {quoted} 2>/dev/null || true", timeout=30)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def remote_path_exists(self, guest_path: str) -> bool:
        quoted = shlex.quote(guest_path)
        result = self.exec(f"test -e {quoted}", timeout=15)
        return result.exit_code == 0

    def remote_is_directory(self, guest_path: str) -> bool:
        quoted = shlex.quote(guest_path)
        result = self.exec(f"test -d {quoted}", timeout=15)
        return result.exit_code == 0

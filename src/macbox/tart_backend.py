"""VM backend abstraction and Tart implementation."""

from __future__ import annotations

import json
import time
from typing import Protocol

from macbox.errors import TartError, VMNotReadyError
from macbox.models import VMInfo
from macbox.runner import run_command, start_background_command


class VMBackend(Protocol):
    def list_vms(self) -> list[VMInfo]: ...

    def clone(self, source: str, name: str) -> None: ...

    def run(self, name: str, headless: bool) -> None: ...

    def stop(self, name: str) -> None: ...

    def delete(self, name: str) -> None: ...

    def ip(self, name: str, *, timeout: int = 120, poll_interval: float = 2.0) -> str: ...


class TartBackend:
    def __init__(self, tart_path: str = "tart") -> None:
        self.tart_path = tart_path

    def _tart(self, *args: str, timeout: int | None = None) -> str:
        argv = [self.tart_path, *args]
        result = run_command(argv, timeout=timeout)
        if result.exit_code != 0:
            raise TartError(
                f"Tart command failed: {' '.join(argv)}",
                details={
                    "argv": argv,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )
        return result.stdout.strip()

    def list_vms(self) -> list[VMInfo]:
        result = run_command([self.tart_path, "list", "--format", "json"])
        if result.exit_code == 0 and result.stdout.strip():
            return self._parse_json_list(result.stdout)
        result = run_command([self.tart_path, "list"])
        if result.exit_code != 0:
            raise TartError(
                "Failed to list Tart VMs",
                details={
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )
        return self._parse_plain_list(result.stdout)

    def clone(self, source: str, name: str) -> None:
        if source == name:
            raise TartError(
                "Refusing to clone VM onto itself; this would modify the base template",
                details={"source": source, "name": name},
            )
        self._tart("clone", source, name, timeout=None)

    def run(self, name: str, headless: bool) -> None:
        args = [self.tart_path, "run"]
        if headless:
            args.append("--no-graphics")
        args.append(name)
        start_background_command(args)

    def stop(self, name: str) -> None:
        result = run_command([self.tart_path, "stop", name], timeout=60)
        if result.exit_code != 0 and "not running" not in result.stderr.lower():
            raise TartError(
                f"Failed to stop VM {name}",
                details={
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )

    def delete(self, name: str) -> None:
        self._tart("delete", name, timeout=120)

    def ip(self, name: str, *, timeout: int = 120, poll_interval: float = 2.0) -> str:
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            result = run_command([self.tart_path, "ip", name], timeout=15)
            ip = result.stdout.strip()
            if result.exit_code == 0 and ip:
                return ip
            last_error = result.stderr.strip() or result.stdout.strip()
            time.sleep(poll_interval)
        raise VMNotReadyError(
            f"Timed out waiting for VM IP: {name}",
            details={"vm": name, "timeout": timeout, "last_error": last_error},
        )

    def vm_exists(self, name: str) -> bool:
        return any(vm.name == name for vm in self.list_vms())

    def _parse_json_list(self, stdout: str) -> list[VMInfo]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise TartError(
                "Failed to parse tart list JSON",
                details={"stdout": stdout, "reason": str(exc)},
            ) from exc

        items: list[VMInfo] = []
        if isinstance(payload, list):
            for entry in payload:
                if isinstance(entry, str):
                    items.append(VMInfo(name=entry))
                elif isinstance(entry, dict):
                    items.append(
                        VMInfo(
                            name=str(entry.get("name") or entry.get("Name") or ""),
                            state=str(entry.get("state") or entry.get("State") or "unknown"),
                            source=entry.get("source") or entry.get("Source"),
                        )
                    )
        return [item for item in items if item.name]

    def _parse_plain_list(self, stdout: str) -> list[VMInfo]:
        items: list[VMInfo] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("name"):
                continue
            name = line.split()[0]
            items.append(VMInfo(name=name))
        return items

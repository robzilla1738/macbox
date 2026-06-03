"""Run directory and artifact management."""

from __future__ import annotations

import json
from pathlib import Path

from macbox.config import get_state_dir, load_config
from macbox.errors import RunError
from typing import Literal

from macbox.models import DisplayMode, RunMetadata, make_run_id, utc_now_iso

RunStatus = Literal["running", "stopped", "destroyed", "failed"]


class RunManager:
    """Tracks active runs and artifact directories per VM."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.runs_root = state_dir / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self._active: dict[str, RunMetadata] = {}

    @classmethod
    def from_config(cls) -> RunManager:
        config = load_config()
        return cls(get_state_dir(config))

    def create_run(
        self,
        vm: str,
        image: str,
        *,
        display_mode: DisplayMode = "headless",
        watch: dict | None = None,
    ) -> RunMetadata:
        run_id = make_run_id(vm)
        metadata = RunMetadata(
            run_id=run_id,
            vm=vm,
            image=image,
            created_at=utc_now_iso(),
            status="running",
            display_mode=display_mode,
            watch=watch or {},
        )
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("screenshots", "logs", "crashes", "uploads", "downloads", "reports", "diagnostics"):
            (run_dir / sub).mkdir(exist_ok=True)
        self.write_metadata(metadata)
        self._active[vm] = metadata
        return metadata

    def get_or_create_run(self, vm: str, image: str = "unknown") -> RunMetadata:
        if vm in self._active:
            return self._active[vm]
        existing = self.find_latest_run_for_vm(vm)
        if existing is not None and existing.status == "running":
            self._active[vm] = existing
            return existing
        return self.create_run(vm, image)

    def find_latest_run_for_vm(self, vm: str) -> RunMetadata | None:
        candidates: list[tuple[str, RunMetadata]] = []
        for path in self.runs_root.iterdir():
            if not path.is_dir():
                continue
            meta_path = path / "metadata.json"
            if not meta_path.exists():
                continue
            metadata = self.read_metadata(path.name)
            if metadata.vm == vm:
                candidates.append((metadata.run_id, metadata))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def run_dir(self, run_id: str) -> Path:
        return self.runs_root / run_id

    def write_metadata(self, metadata: RunMetadata) -> None:
        path = self.run_dir(metadata.run_id) / "metadata.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(metadata.model_dump(), indent=2) + "\n",
            encoding="utf-8",
        )

    def read_metadata(self, run_id: str) -> RunMetadata:
        path = self.run_dir(run_id) / "metadata.json"
        if not path.exists():
            raise RunError(
                f"Run metadata not found: {run_id}",
                details={"run_id": run_id},
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        return RunMetadata.model_validate(raw)

    def update_status(self, vm: str, status: RunStatus) -> None:
        metadata = self.find_latest_run_for_vm(vm)
        if metadata is None:
            return
        metadata.status = status
        self.write_metadata(metadata)
        self._active[vm] = metadata

    def artifact_path(self, vm: str, kind: str, filename: str) -> Path:
        metadata = self.get_or_create_run(vm)
        allowed = {"screenshots", "logs", "crashes", "uploads", "downloads", "reports", "diagnostics"}
        if kind not in allowed:
            raise RunError(
                f"Unknown artifact kind: {kind}",
                details={"kind": kind, "allowed": sorted(allowed)},
            )
        run_dir = self.run_dir(metadata.run_id).resolve()
        path = (run_dir / kind / filename).resolve()
        try:
            path.relative_to(run_dir)
        except ValueError as exc:
            raise RunError(
                "Artifact path escapes run directory",
                details={"path": str(path), "run_dir": str(run_dir)},
            ) from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def register_artifact(self, vm: str, kind: str, path: Path) -> None:
        metadata = self.get_or_create_run(vm)
        key_map = {
            "screenshots": metadata.artifacts.screenshots,
            "logs": metadata.artifacts.logs,
            "crashes": metadata.artifacts.crashes,
            "uploads": metadata.artifacts.uploads,
            "downloads": metadata.artifacts.downloads,
            "reports": metadata.artifacts.reports,
            "diagnostics": metadata.artifacts.diagnostics,
        }
        serialized = str(path)
        bucket = key_map[kind]
        if serialized not in bucket:
            bucket.append(serialized)
        self.write_metadata(metadata)
        self._active[vm] = metadata


CRASH_SUFFIXES = (".crash", ".ips", ".panic")


def list_crash_basenames(filenames: list[str]) -> set[str]:
    return {
        name
        for name in filenames
        if any(name.endswith(suffix) for suffix in CRASH_SUFFIXES)
    }


def detect_new_crashes(before: set[str], after: set[str]) -> list[str]:
    return sorted(after - before)

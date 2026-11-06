"""Job model and queue persistence."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .state import StateCorruptError, load_state, save_state


class JobStatus(str, Enum):
    PENDING = "PENDING"
    UPLOADING = "UPLOADING"
    PAUSED = "PAUSED"
    UPLOADED = "UPLOADED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    VERIFY_FAILED = "VERIFY FAILED"
    FAILED = "FAILED"


BADGES = {
    JobStatus.PENDING: "[WAIT]",
    JobStatus.UPLOADING: "[UP]",
    JobStatus.PAUSED: "[PAUSE]",
    JobStatus.UPLOADED: "[UPLD]",
    JobStatus.VERIFYING: "[VRFY]",
    JobStatus.VERIFIED: "[DONE]",
    JobStatus.VERIFY_FAILED: "[VFAIL]",
    JobStatus.FAILED: "[FAIL]",
}

_FULLY_UPLOADED = {
    JobStatus.UPLOADED,
    JobStatus.VERIFYING,
    JobStatus.VERIFIED,
    JobStatus.VERIFY_FAILED,
}


@dataclass
class LiveStats:
    """Transient numbers, never persisted."""

    speed: float | None = None
    eta: float | None = None
    last_event: float | None = None
    last_activity: float | None = None


@dataclass
class Job:
    id: str
    source: str
    destination: str
    size: int
    status: JobStatus = JobStatus.PENDING
    percent: float = 0.0
    resumable: bool = False
    remote_crc64: str | None = None
    message: str = ""
    is_folder: bool = False
    folder_files: int = 0
    folder_dirs: int = 0
    # None marks a legacy job that still uses the old shared checkpoint directory.
    checkpoint_dir: str | None = None

    @property
    def name(self) -> str:
        return self.source.rstrip("\\/").replace("\\", "/").split("/")[-1]

    @property
    def object_uri(self) -> str:
        if self.is_folder:
            return f"{self.destination}{self.name}/" if self.destination.endswith("/") else f"{self.destination}/{self.name}/"
        return self.destination + self.name if self.destination.endswith("/") else self.destination

    @property
    def badge(self) -> str:
        return BADGES[self.status]

    @property
    def done_bytes(self) -> int:
        if self.status in _FULLY_UPLOADED:
            return self.size
        return min(self.size, int(self.size * self.percent / 100))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "destination": self.destination,
            "size": self.size,
            "status": self.status.value,
            "percent": self.percent,
            "resumable": self.resumable,
            "remote_crc64": self.remote_crc64,
            "message": self.message,
            "is_folder": self.is_folder,
            "folder_files": self.folder_files,
            "folder_dirs": self.folder_dirs,
            "checkpoint_dir": self.checkpoint_dir,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Job":
        message = str(raw.get("message", ""))
        try:
            status = JobStatus(raw.get("status", "PENDING"))
        except ValueError:
            status = JobStatus.FAILED
            message = "unknown status in state file"
        return cls(
            id=str(raw["id"]),
            source=str(raw["source"]),
            destination=str(raw["destination"]),
            size=int(raw["size"]),
            status=status,
            percent=float(raw.get("percent", 0.0)),
            resumable=bool(raw.get("resumable", False)),
            remote_crc64=raw.get("remote_crc64"),
            message=message,
            is_folder=bool(raw.get("is_folder", False)),
            folder_files=int(raw.get("folder_files", 0)),
            folder_dirs=int(raw.get("folder_dirs", 0)),
            checkpoint_dir=(str(raw["checkpoint_dir"]) if raw.get("checkpoint_dir") else None),
        )


def new_job(source: str, destination: str, size: int) -> Job:
    return Job(id=uuid.uuid4().hex[:8], source=source, destination=destination, size=size)


def new_folder_job(source: str, destination: str, size: int, files: int, directories: int) -> Job:
    return Job(
        id=uuid.uuid4().hex[:8],
        source=source,
        destination=destination,
        size=size,
        is_folder=True,
        folder_files=files,
        folder_dirs=directories,
    )


def recover_after_crash(job: Job) -> bool:
    """A job left UPLOADING or VERIFYING means the app died; make that state honest."""
    if job.status is JobStatus.UPLOADING:
        job.status = JobStatus.PAUSED
        job.resumable = True
        job.message = "Interrupted; resume to continue"
        return True
    if job.status is JobStatus.VERIFYING:
        job.status = JobStatus.UPLOADED
        job.message = "Interrupted during verification; verify again"
        return True
    return False


class JobStore:
    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> list[Job]:
        raw = load_state(self.path, default={"version": 1, "jobs": []})
        try:
            jobs = [Job.from_dict(item) for item in raw["jobs"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise StateCorruptError(f"state file has an invalid job entry: {self.path}") from exc
        changed = [recover_after_crash(job) for job in jobs]
        if any(changed):
            self.save(jobs)
        return jobs

    def save(self, jobs: list[Job]) -> None:
        save_state(self.path, {"version": 1, "jobs": [job.to_dict() for job in jobs]})

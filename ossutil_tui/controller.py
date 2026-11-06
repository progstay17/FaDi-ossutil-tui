"""Orchestrates jobs: preflight, upload, pause, resume, verify. No UI code here."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from .browser import (
    EntryKind, ListPage, folder_of, normalize_dir_uri, parse_dir_listing, parse_du, similar_names, split_uri,
)
from .connection import classify_failure, run_command
from .jobs import Job, JobStatus, JobStore, LiveStats, new_job
from .ossutil import CpRequest, choose_part_size, normalize_region, parse_stat
from .runner import ProgressEvent, RunResult, UploadRunner
from .settings import Settings
from .verify import VerifyStatus, verify_remote

_DEST_RE = re.compile(r"^oss://[a-z0-9][a-z0-9-]{1,61}[a-z0-9](/.*)?$")
_PROGRESS_LINE_RE = re.compile(r"Upload\.\.\. done:")


class JobError(RuntimeError):
    """A user-facing problem (bad input, wrong state). Message is safe to show."""


@dataclass(frozen=True)
class PreflightInfo:
    source: str
    destination: str
    object_uri: str
    size: int
    part_size: int
    parts: int
    region: str
    checkpoint_dir: str
    command: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PrefixCheck:
    exists: bool
    folder: str
    missing: str | None = None     # first folder name on the path that does not exist
    ancestor: str | None = None    # deepest folder that does exist
    similar: list[str] = field(default_factory=list)


def default_argv(settings: Settings, job: Job, part_size: int) -> list[str]:
    return CpRequest(
        exe=settings.exe,
        source=job.source,
        destination=job.destination,
        config_file=settings.config_file,
        region=settings.region,
        checkpoint_dir=settings.checkpoint_dir,
        part_size=part_size,
    ).argv()


async def default_stat(settings: Settings, uri: str, env: dict[str, str] | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        settings.exe, "stat", uri,
        "--config-file", settings.config_file,
        "--region", normalize_region(settings.region),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        detail = (err or out).decode("utf-8", "replace").strip()[-300:]
        raise RuntimeError(detail or f"stat exited with code {proc.returncode}")
    return out.decode("utf-8", "replace")


def _last_meaningful_line(text: str) -> str:
    for line in reversed(re.split(r"[\r\n]", text)):
        stripped = line.strip()
        if stripped and not _PROGRESS_LINE_RE.search(stripped):
            return stripped
    return ""


class JobController:
    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        *,
        runner_factory: Callable[[Callable[[ProgressEvent], None]], UploadRunner] | None = None,
        stat_fn: Callable[[Settings, str], Awaitable[str]] | None = None,
        argv_fn: Callable[[Settings, Job, int], list[str]] | None = None,
        on_change: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        env_fn: Callable[[], dict[str, str] | None] | None = None,
        exec_fn: Callable[[list[str], dict[str, str] | None], Awaitable[tuple[int, str, str]]] | None = None,
    ) -> None:
        self._env_fn = env_fn or (lambda: None)
        self._exec = exec_fn or run_command
        self.default_root = ""  # oss://bucket/ where the browser starts
        self.settings = settings
        self.store = store
        self.jobs: list[Job] = store.load()
        self.live: dict[str, LiveStats] = {}
        self._runner_factory = runner_factory or (
            lambda cb: UploadRunner(on_progress=cb, env=self._env_fn())
        )
        self._stat_fn = stat_fn or (lambda s, u: default_stat(s, u, self._env_fn()))
        self._argv_fn = argv_fn or default_argv
        self._on_change = on_change
        self._clock = clock
        self._runners: dict[str, UploadRunner] = {}

    # --- small helpers -------------------------------------------------
    def set_on_change(self, callback: Callable[[], None] | None) -> None:
        self._on_change = callback

    def now(self) -> float:
        return self._clock()

    def get(self, job_id: str) -> Job:
        for job in self.jobs:
            if job.id == job_id:
                return job
        raise JobError(f"unknown job: {job_id}")

    def is_busy(self) -> bool:
        return bool(self._runners)

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _persist(self) -> None:
        self.store.save(self.jobs)
        self._changed()

    def _log(self, job: Job, message: str) -> None:
        try:
            directory = Path(self.settings.log_dir)
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().isoformat(timespec="seconds")
            with open(directory / f"{job.id}.log", "a", encoding="utf-8") as handle:
                handle.write(f"{stamp} {message}\n")
        except OSError:
            pass  # logging is best effort and must never break an upload

    def _has_checkpoint(self) -> bool:
        root = Path(self.settings.checkpoint_dir)
        return root.is_dir() and any(p.is_file() for p in root.rglob("*"))

    # --- queue management ---------------------------------------------
    def add_job(self, source: str, destination: str) -> Job:
        source = source.strip().strip('"')
        destination = destination.strip()
        path = Path(source)
        if not path.is_file():
            raise JobError(f"source file not found: {source}")
        if not _DEST_RE.match(destination):
            raise JobError("destination must look like oss://bucket/prefix/ (lowercase bucket name)")
        root = self._dir(self.default_root) if self.default_root else ""
        if root:
            try:
                self._assert_within_root(folder_of(destination))
            except ValueError as exc:
                raise JobError(f"Not a valid oss:// address: {destination!r}") from exc
        size = path.stat().st_size
        if size <= 0:
            raise JobError("source file is empty")
        candidate = new_job(str(path), destination, size)
        for existing in self.jobs:
            if existing.source == candidate.source and existing.object_uri == candidate.object_uri:
                raise JobError("this file is already queued for the same destination")
        self.jobs.append(candidate)
        self._persist()
        self._log(candidate, f"added {candidate.source} -> {candidate.object_uri} ({size} B)")
        return candidate

    def remove(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.status in (JobStatus.UPLOADING, JobStatus.VERIFYING):
            raise JobError("job is running; pause it first")
        if job.resumable and job.status in (JobStatus.PAUSED, JobStatus.FAILED):
            raise JobError("job has a checkpoint; resume it first (discarding checkpoints comes later)")
        self.jobs.remove(job)
        self.live.pop(job.id, None)
        self._persist()

    def preflight(self, job_id: str) -> PreflightInfo:
        job = self.get(job_id)
        part_size = choose_part_size(job.size, self.settings.part_size)
        warnings: list[str] = []
        if part_size != self.settings.part_size:
            warnings.append("Part size was raised to stay under the 10,000 part limit")
        region = normalize_region(self.settings.region)
        if region != self.settings.region.strip():
            warnings.append(f"Region normalized from {self.settings.region!r} to {region!r}")
        argv = self._argv_fn(self.settings, job, part_size)
        return PreflightInfo(
            source=job.source,
            destination=job.destination,
            object_uri=job.object_uri,
            size=job.size,
            part_size=part_size,
            parts=-(-job.size // part_size),
            region=region,
            checkpoint_dir=self.settings.checkpoint_dir,
            command=subprocess.list2cmdline(argv),
            warnings=warnings,
        )

    # --- browsing (read-only) ------------------------------------------
    def _ossutil_args(self, *args: str) -> list[str]:
        return [
            self.settings.exe, *args,
            "--config-file", self.settings.config_file,
            "--region", normalize_region(self.settings.region),
        ]

    async def _run(self, args: list[str]) -> tuple[int, str, str]:
        try:
            return await self._exec(args, self._env_fn())
        except OSError as exc:
            raise JobError(f"Cannot run ossutil: {exc}") from exc

    @staticmethod
    def _dir(uri: str) -> str:
        try:
            return normalize_dir_uri(uri)
        except ValueError as exc:
            raise JobError(f"Not a valid oss:// address: {uri!r}") from exc

    def _assert_within_root(self, uri: str) -> str:
        """Normalize a directory URI and reject paths outside the configured workspace."""
        target = self._dir(uri)
        if not self.default_root:
            return target
        root = self._dir(self.default_root)
        root_bucket, root_key = split_uri(root)
        target_bucket, target_key = split_uri(target)
        if target_bucket != root_bucket or (root_key and target_key != root_key and not target_key.startswith(root_key)):
            raise JobError(f"location is outside the permitted prefix: {root}")
        return target

    async def list_directory(
        self, prefix_uri: str, *, start_after_key: str | None = None, limit: int = 200
    ) -> ListPage:
        prefix = self._assert_within_root(prefix_uri)
        extra = ["--start-after", start_after_key] if start_after_key else []
        code, out, err = await self._run(
            self._ossutil_args("ls", prefix, "-d", "--limited-num", str(limit), *extra)
        )
        if code != 0:
            raise JobError(classify_failure(f"{err}\n{out}")[1])
        return parse_dir_listing(out, prefix, limit)

    async def prefix_exists(self, prefix_uri: str) -> bool:
        page = await self.list_directory(prefix_uri, limit=1)
        return page.total_returned > 0

    async def check_prefix(self, folder_uri: str) -> PrefixCheck:
        """Does the folder exist? If not, where does the path break, and what similar names exist there?"""
        folder = self._assert_within_root(folder_uri)
        bucket, key = split_uri(folder)
        root = self._dir(self.default_root) if self.default_root else f"oss://{bucket}/"
        _, root_key = split_uri(root)
        relative_key = key[len(root_key):]
        parts = [part for part in relative_key.split("/") if part]
        ancestor = root
        for depth, part in enumerate(parts, start=1):
            current = root + "/".join(parts[:depth]) + "/"
            if not await self.prefix_exists(current):
                try:
                    siblings = (await self.list_directory(ancestor, limit=1000)).entries
                    names = [entry.name for entry in siblings if entry.kind is EntryKind.DIR]
                except JobError:
                    names = []  # the typo hint is optional; the missing-folder warning still shows
                return PrefixCheck(False, folder, part, ancestor, similar_names(part, names))
            ancestor = current
        return PrefixCheck(True, folder)

    async def destination_warnings(self, destination: str) -> list[str]:
        try:
            folder = folder_of(destination)
        except ValueError as exc:
            raise JobError(f"Not a valid oss:// address: {destination!r}") from exc
        check = await self.check_prefix(folder)
        if check.exists:
            return []
        warnings = [f"Destination folder does not exist yet and will be created: {check.folder}"]
        if check.similar:
            warnings.append(
                f"Similar existing folders under {check.ancestor}: {', '.join(check.similar)}. "
                "Check for a typo."
            )
        return warnings

    async def stat_object(self, uri: str) -> dict[str, str]:
        try:
            return parse_stat(await self._stat_fn(self.settings, uri))
        except (RuntimeError, OSError) as exc:
            raise JobError(str(exc)) from exc

    async def folder_usage(self, prefix_uri: str) -> tuple[int, int]:
        """(objects, bytes). ossutil may exit 1 here when it cannot list unfinished uploads; the totals are still valid."""
        prefix = self._dir(prefix_uri)
        _code, out, err = await self._run(self._ossutil_args("du", prefix))
        usage = parse_du(out)
        if usage is None:
            raise JobError(classify_failure(f"{err}\n{out}")[1])
        return usage

    # --- running -------------------------------------------------------
    async def run(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.status not in (JobStatus.PENDING, JobStatus.PAUSED, JobStatus.FAILED):
            raise JobError(f"cannot start a job that is {job.status.value}")
        if self._runners:
            raise JobError("another upload is already running (M1 runs one at a time)")

        part_size = choose_part_size(job.size, self.settings.part_size)
        argv = self._argv_fn(self.settings, job, part_size)
        live = LiveStats()
        self.live[job.id] = live

        def on_progress(event: ProgressEvent) -> None:
            job.percent = event.percent
            live.speed = event.speed
            live.eta = event.eta_seconds
            live.last_event = self._clock()
            self._changed()

        runner = self._runner_factory(on_progress)
        self._runners[job.id] = runner
        job.status = JobStatus.UPLOADING
        job.message = ""
        self._persist()
        self._log(job, "upload started")

        result: RunResult | None = None
        start_error = ""
        try:
            result = await runner.run(argv, job.size)
        except OSError as exc:
            start_error = f"cannot start ossutil: {exc}"
        finally:
            self._runners.pop(job.id, None)

        if result is None:
            job.status = JobStatus.FAILED
            job.message = start_error
            job.resumable = False
        elif result.paused:
            job.status = JobStatus.PAUSED
            job.resumable = True
            job.message = "Paused; resume continues from the checkpoint"
            self._log(job, f"paused at {job.percent:.3f}%")
        elif result.success:
            job.percent = 100.0
            job.status = JobStatus.UPLOADED
            job.resumable = False
            job.message = ""
            self._log(job, "upload finished")
        else:
            job.status = JobStatus.FAILED
            job.resumable = self._has_checkpoint()
            job.message = (
                _last_meaningful_line(result.stderr_text)
                or _last_meaningful_line(result.stdout_tail)
                or f"ossutil exited with code {result.exit_code}"
            )
            self._log(job, f"failed (exit {result.exit_code}): {job.message}")
        self._persist()

        if job.status is JobStatus.UPLOADED:
            await self._verify(job)

    def pause(self, job_id: str) -> None:
        runner = self._runners.get(job_id)
        if runner is None:
            raise JobError("job is not uploading")
        runner.pause()

    async def verify(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.status not in (JobStatus.UPLOADED, JobStatus.VERIFY_FAILED):
            raise JobError(f"nothing to verify for a job that is {job.status.value}")
        await self._verify(job)

    async def _verify(self, job: Job) -> None:
        job.status = JobStatus.VERIFYING
        self._persist()
        try:
            text = await self._stat_fn(self.settings, job.object_uri)
        except (RuntimeError, OSError) as exc:
            job.status = JobStatus.VERIFY_FAILED
            job.message = f"stat failed: {exc}"
        else:
            verdict = verify_remote(job.size, parse_stat(text))
            job.remote_crc64 = verdict.remote_crc64
            job.message = verdict.reason
            job.status = (
                JobStatus.VERIFIED if verdict.status is VerifyStatus.VERIFIED else JobStatus.VERIFY_FAILED
            )
        self._log(job, f"{job.status.value}: {job.message}")
        self._persist()

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Pause whatever is running and wait until its state has been saved."""
        for runner in list(self._runners.values()):
            runner.pause()
        deadline = self._clock() + timeout
        while self._runners and self._clock() < deadline:
            await asyncio.sleep(0.05)
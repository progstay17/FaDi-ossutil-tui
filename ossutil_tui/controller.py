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
    EntryKind, ListPage, folder_of, normalize_dir_uri, parse_dir_listing, parse_du, parse_ls_usage, similar_names, split_uri,
)
from .connection import classify_failure, run_command
from .jobs import Job, JobStatus, JobStore, LiveStats, new_job, new_folder_job
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
    is_folder: bool = False
    folder_files: int = 0
    folder_dirs: int = 0


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
        source=job.source + os.sep if job.is_folder else job.source,
        destination=job.object_uri if job.is_folder else job.destination,
        config_file=settings.config_file,
        region=settings.region,
        checkpoint_dir=job.checkpoint_dir or settings.checkpoint_dir,
        part_size=part_size,
        recursive=job.is_folder,
        update=job.is_folder,
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

    def _job_checkpoint_dir(self, job: Job) -> Path:
        return Path(job.checkpoint_dir or self.settings.checkpoint_dir)

    def _has_checkpoint(self, job: Job) -> bool:
        root = self._job_checkpoint_dir(job)
        return root.is_dir() and any(p.is_file() for p in root.rglob("*"))

    def _checkpoint_signature(self, job: Job) -> tuple[int, int]:
        """Return (file count, newest mtime) for detecting active multipart writes."""
        root = self._job_checkpoint_dir(job)
        count = 0
        newest = 0
        try:
            for path in root.rglob("*"):
                if path.is_file():
                    stat = path.stat()
                    count += 1
                    newest = max(newest, stat.st_mtime_ns)
        except OSError:
            pass
        return count, newest

    # --- queue management ---------------------------------------------
    def add_job(self, source: str, destination: str) -> Job:
        source = source.strip().strip('"')
        destination = destination.strip()
        path = Path(source).expanduser()
        if not path.exists():
            raise JobError(f"source file not found (or folder not found): {source}")
        if not _DEST_RE.match(destination):
            raise JobError("destination must look like oss://bucket/prefix/ (lowercase bucket name)")
        if self.default_root:
            try:
                self._assert_within_root(folder_of(destination))
            except (ValueError, JobError) as exc:
                raise JobError(f"destination is outside the permitted prefix: {self.default_root}") from exc

        if path.is_symlink():
            raise JobError("symbolic links cannot be uploaded as a source")
        if path.is_dir():
            size = files = directories = 0
            try:
                for current, dirnames, filenames in os.walk(path, followlinks=False):
                    current_path = Path(current)
                    safe_dirs = []
                    for dirname in dirnames:
                        child = current_path / dirname
                        if child.is_symlink():
                            raise JobError(f"symbolic link directory is not supported: {child}")
                        safe_dirs.append(dirname)
                        directories += 1
                    dirnames[:] = safe_dirs
                    for filename in filenames:
                        child = current_path / filename
                        if child.is_symlink():
                            raise JobError(f"symbolic link file is not supported: {child}")
                        if child.is_file():
                            size += child.stat().st_size
                            files += 1
            except OSError as exc:
                raise JobError(f"cannot scan source folder: {exc}") from exc
            candidate = new_folder_job(str(path.resolve()), destination, size, files, directories)
            if files == 0 and directories == 0:
                candidate.message = "Empty folder; only the directory marker will be created"
        elif path.is_file():
            size = path.stat().st_size
            if size <= 0:
                raise JobError("source file is empty")
            candidate = new_job(str(path.resolve()), destination, size)
        else:
            raise JobError(f"source is not a regular file or folder: {source}")

        for existing in self.jobs:
            if existing.source == candidate.source and existing.object_uri == candidate.object_uri:
                raise JobError("this file or folder is already queued for the same destination")
        candidate.checkpoint_dir = str(Path(self.settings.checkpoint_dir) / candidate.id)
        self.jobs.append(candidate)
        self._persist()
        self._log(candidate, f"added {candidate.source} -> {candidate.object_uri} ({size} B)")
        return candidate

    def remove(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.status in (JobStatus.UPLOADING, JobStatus.VERIFYING):
            raise JobError("job is running; pause it first")
        if job.resumable and job.status in (JobStatus.PAUSED, JobStatus.FAILED):
            raise JobError("job has a checkpoint; discard it before removing the job")
        self.jobs.remove(job)
        self.live.pop(job.id, None)
        self._persist()

    def discard_checkpoint(self, job_id: str) -> bool:
        """Discard one job checkpoint; return True if a legacy shared folder was preserved."""
        job = self.get(job_id)
        if job.status in (JobStatus.UPLOADING, JobStatus.VERIFYING) or job.id in self._runners:
            raise JobError("job is running; pause it before discarding its checkpoint")
        if job.status not in (JobStatus.PAUSED, JobStatus.FAILED):
            raise JobError("discard checkpoint is available only for PAUSED or FAILED jobs")

        legacy_shared = job.checkpoint_dir is None
        target = self._job_checkpoint_dir(job)
        if not legacy_shared:
            root = Path(self.settings.checkpoint_dir).resolve()
            resolved = target.resolve()
            if resolved == root or root not in resolved.parents:
                raise JobError("refusing to delete a checkpoint directory outside this job")
            try:
                if target.exists():
                    for item in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                        if item.is_file() or item.is_symlink():
                            item.unlink()
                        elif item.is_dir():
                            item.rmdir()
                    target.rmdir()
            except OSError as exc:
                raise JobError(f"could not discard checkpoint: {exc}") from exc
        else:
            # Old versions shared one checkpoint folder. Preserve it because
            # another queued job may still depend on its contents.
            job.checkpoint_dir = str(Path(self.settings.checkpoint_dir) / job.id)

        job.status = JobStatus.PENDING
        job.percent = 0.0
        job.elapsed_seconds = 0.0
        job.resumable = False
        job.remote_crc64 = None
        job.message = "Checkpoint discarded; next upload starts from the beginning"
        self.live.pop(job.id, None)
        self._persist()
        self._log(job, "checkpoint discarded")
        return legacy_shared

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
            checkpoint_dir=str(self._job_checkpoint_dir(job)),
            command=subprocess.list2cmdline(argv),
            warnings=warnings,
            is_folder=job.is_folder,
            folder_files=job.folder_files,
            folder_dirs=job.folder_dirs,
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
        if self.default_root:
            try:
                self._assert_within_root(folder_of(job.destination))
            except (ValueError, JobError) as exc:
                raise JobError(f"queued destination is outside the permitted prefix: {self.default_root}") from exc
        if job.status not in (JobStatus.PENDING, JobStatus.PAUSED, JobStatus.FAILED):
            raise JobError(f"cannot start a job that is {job.status.value}")
        if self._runners:
            raise JobError("another upload is already running (M1 runs one at a time)")

        if job.is_folder:
            await self._ensure_folder_marker(job)

        part_size = choose_part_size(job.size, self.settings.part_size)
        argv = self._argv_fn(self.settings, job, part_size)
        started_at = self._clock()
        live = LiveStats(last_activity=started_at, started_at=started_at)
        self.live[job.id] = live

        def on_activity() -> None:
            live.last_activity = self._clock()
            self._changed()

        def on_progress(event: ProgressEvent) -> None:
            job.percent = event.percent
            live.speed = event.speed
            live.eta = event.eta_seconds
            live.last_event = self._clock()
            live.last_activity = live.last_event
            self._changed()

        runner = self._runner_factory(on_progress)
        set_activity_callback = getattr(runner, "set_activity_callback", None)
        if callable(set_activity_callback):
            set_activity_callback(on_activity)
        self._runners[job.id] = runner
        job.status = JobStatus.UPLOADING
        job.message = ""
        self._persist()
        self._log(job, "upload started")

        async def watch_checkpoint_activity() -> None:
            previous = self._checkpoint_signature(job)
            while True:
                await asyncio.sleep(2.0)
                current = self._checkpoint_signature(job)
                if current != previous:
                    previous = current
                    notify = getattr(runner, "notify_activity", None)
                    if callable(notify):
                        notify()
                    else:
                        on_activity()

        activity_task = asyncio.create_task(watch_checkpoint_activity())
        result: RunResult | None = None
        start_error = ""
        try:
            # A stalled attempt is restarted with the same checkpoint. Never discard
            # multipart state; ossutil decides which parts still need uploading.
            for attempt in range(5):
                result = await runner.run(argv, max(job.size, 1))
                if not getattr(result, "stalled", False) or attempt == 4:
                    break
                job.message = f"Connection inactive; retrying from checkpoint ({attempt + 1}/4)"
                self._persist()
                self._changed()
                await asyncio.sleep(min(2 ** (attempt + 1), 30))
        except OSError as exc:
            start_error = f"cannot start ossutil: {exc}"
        finally:
            activity_task.cancel()
            await asyncio.gather(activity_task, return_exceptions=True)
            self._runners.pop(job.id, None)

        if live.started_at is not None:
            job.elapsed_seconds += max(0.0, self._clock() - live.started_at)
            live.started_at = None

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
            job.resumable = self._has_checkpoint(job)
            job.message = (
                "Upload inactive after 5 attempts; checkpoint preserved. Resume to retry."
                if getattr(result, "stalled", False)
                else (
                    _last_meaningful_line(result.stderr_text)
                    or _last_meaningful_line(result.stdout_tail)
                    or f"ossutil exited with code {result.exit_code}"
                )
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

    async def _ensure_folder_marker(self, job: Job) -> None:
        """Create the selected root folder marker once; nested markers come from cp -r."""
        try:
            await self._stat_fn(self.settings, job.object_uri)
            return
        except (RuntimeError, OSError):
            pass
        code, out, err = await self._run(self._ossutil_args("mkdir", job.object_uri))
        if code != 0:
            # A previous interrupted attempt may already have created this marker.
            try:
                await self._stat_fn(self.settings, job.object_uri)
                return
            except (RuntimeError, OSError):
                detail = (err or out).strip()[-300:]
                raise JobError(detail or f"ossutil mkdir exited with code {code}")

    async def _verify(self, job: Job) -> None:
        job.status = JobStatus.VERIFYING
        self._persist()
        if job.is_folder:
            try:
                # Preferred: du provides a compact total, but also queries multipart
                # uploads and can require extra RAM permissions.
                du_code, du_out, du_err = await self._run(self._ossutil_args("du", job.object_uri))
                usage = parse_du(du_out) if du_code in (0, 1) else None
                method = "du"
                if usage is None:
                    # Fallback: recursive ls only needs oss:ListObjects and reports
                    # completed objects with their sizes (no multipart enumeration).
                    ls_code, ls_out, ls_err = await self._run(
                        self._ossutil_args("ls", job.object_uri, "-r")
                    )
                    usage = parse_ls_usage(ls_out) if ls_code == 0 else None
                    method = "ls"
                    if usage is None:
                        detail = (ls_err or ls_out or du_err or du_out).strip()[-300:]
                        raise RuntimeError(
                            f"du verification unavailable ({(du_err or du_out).strip()[-160:]}); "
                            f"recursive ls fallback failed ({detail or f'exit {ls_code}'})"
                        )
                objects, total_bytes = usage
                expected_objects = job.folder_files + job.folder_dirs + 1
                if total_bytes != job.size:
                    job.status = JobStatus.VERIFY_FAILED
                    job.message = f"size mismatch: local {job.size} B, remote {total_bytes} B"
                elif objects != expected_objects:
                    job.status = JobStatus.VERIFY_FAILED
                    job.message = f"object count mismatch: expected {expected_objects}, remote {objects}"
                else:
                    job.status = JobStatus.VERIFIED
                    job.message = (
                        f"verified via {method}: {job.folder_files} file(s), "
                        f"{job.folder_dirs + 1} folder marker(s), {total_bytes} B"
                    )
            except (RuntimeError, OSError, JobError) as exc:
                job.status = JobStatus.VERIFY_FAILED
                job.message = f"folder verification failed: {exc}"
        else:
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

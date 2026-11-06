"""Async runner: starts ossutil as a subprocess, parses progress, supports pause and stall recovery."""
from __future__ import annotations

import asyncio
import codecs
import re
import time
from dataclasses import dataclass
from typing import Callable

from .ossutil import is_success, parse_progress
from .progress import SpeedTracker

_SPLIT_RE = re.compile(r"[\r\n]")
_TAIL_LIMIT = 4096
_STDERR_LIMIT = 8192
STALL_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class ProgressEvent:
    percent: float
    done_bytes: int
    speed: float | None
    eta_seconds: float | None


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    success: bool
    paused: bool
    stdout_tail: str
    stderr_text: str
    stalled: bool = False


class UploadRunner:
    def __init__(
        self,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        env: dict[str, str] | None = None,
        stall_timeout: float = STALL_TIMEOUT_SECONDS,
    ) -> None:
        if stall_timeout <= 0:
            raise ValueError("stall_timeout must be positive")
        self._env = env
        self._on_progress = on_progress
        self._on_activity: Callable[[], None] | None = None
        self._clock = clock
        self._stall_timeout = stall_timeout
        self._last_activity = 0.0
        self._proc: asyncio.subprocess.Process | None = None
        self._paused = False
        self._stalled = False

    def set_activity_callback(self, callback: Callable[[], None] | None) -> None:
        self._on_activity = callback

    def notify_activity(self) -> None:
        """Record external activity, such as a changing ossutil checkpoint."""
        self._last_activity = self._clock()
        if self._on_activity is not None:
            self._on_activity()

    def pause(self) -> None:
        # Also remember a pause requested during the retry backoff, when no
        # subprocess is currently attached to this runner.
        self._paused = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.terminate()

    async def run(self, argv: list[str], total_bytes: int) -> RunResult:
        if self._paused:
            return RunResult(None, False, True, "", "")
        self._stalled = False
        self._last_activity = self._clock()
        tracker = SpeedTracker(total_bytes)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
        )
        self._proc = proc
        tail = ""
        stderr_text = ""

        def handle_segment(segment: str) -> None:
            percent = parse_progress(segment)
            if percent is None:
                return
            tracker.update(percent, self._clock())
            if self._on_progress is not None:
                self._on_progress(
                    ProgressEvent(
                        percent=percent,
                        done_bytes=tracker.done_bytes,
                        speed=tracker.speed,
                        eta_seconds=tracker.eta_seconds(),
                    )
                )

        async def read_stdout() -> None:
            nonlocal tail
            assert proc.stdout is not None
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            buffer = ""
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                self.notify_activity()
                text = decoder.decode(chunk)
                tail = (tail + text)[-_TAIL_LIMIT:]
                buffer += text
                parts = _SPLIT_RE.split(buffer)
                buffer = parts.pop()
                for part in parts:
                    handle_segment(part)
            buffer += decoder.decode(b"", final=True)
            if buffer:
                handle_segment(buffer)

        async def read_stderr() -> None:
            nonlocal stderr_text
            assert proc.stderr is not None
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                self.notify_activity()
                stderr_text = (stderr_text + chunk.decode("utf-8", errors="replace"))[-_STDERR_LIMIT:]

        async def watch_stall() -> None:
            interval = min(0.5, max(0.1, self._stall_timeout / 6))
            while proc.returncode is None:
                await asyncio.sleep(interval)
                if (
                    proc.returncode is None
                    and self._clock() - self._last_activity >= self._stall_timeout
                ):
                    self._stalled = True
                    proc.terminate()
                    return

        watchdog = asyncio.create_task(watch_stall())
        try:
            await asyncio.gather(read_stdout(), read_stderr())
            exit_code = await proc.wait()
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
            self._proc = None

        return RunResult(
            exit_code=exit_code,
            success=(exit_code == 0 and not self._paused and not self._stalled and is_success(tail)),
            paused=self._paused,
            stdout_tail=tail,
            stderr_text=stderr_text,
            stalled=self._stalled,
        )

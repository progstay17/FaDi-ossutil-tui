"""Pure functions that turn state into Rich Text. No Textual imports, so they are easy to test."""
from __future__ import annotations

import math

from rich.text import Text

from .controller import PreflightInfo
from .format import format_eta, format_size, format_speed, render_bar, truncate_middle
from .jobs import Job, JobStatus, LiveStats
from .ossutil import choose_part_size

STATUS_COLORS = {
    JobStatus.PENDING: "#9AA9B8",
    JobStatus.UPLOADING: "#D4F53C",
    JobStatus.PAUSED: "#F2B84B",
    JobStatus.UPLOADED: "#D4F53C",
    JobStatus.VERIFYING: "#D4F53C",
    JobStatus.VERIFIED: "#43C98B",
    JobStatus.VERIFY_FAILED: "#F06A72",
    JobStatus.FAILED: "#F06A72",
}
LABEL = "#8793A1"
VALUE = "#E6EDF3"
DIM = "#9AA9B8"
ERROR = "#F06A72"
STALL_SECONDS = 90.0
PROGRESS_UPDATE_SECONDS = 10.0
EMPTY_TEXT = "NO UPLOAD JOBS\n\nPress A to add a file."


def _ellipsis(unicode: bool) -> str:
    return "…" if unicode else "..."


def render_row(job: Job, *, name_width: int = 22, unicode: bool = True) -> tuple[Text, Text, Text, Text]:
    badge = Text(job.badge, style=STATUS_COLORS[job.status])
    name = Text(truncate_middle(job.name, name_width, _ellipsis(unicode)), style=VALUE)
    size = Text(format_size(job.size), style=DIM, justify="right")
    pct = Text(f"{job.percent:5.1f}%", style=VALUE, justify="right")
    return badge, name, size, pct


def _verify_line(job: Job) -> str:
    if job.status is JobStatus.VERIFIED:
        return "VERIFIED (folder totals)" if job.is_folder else f"VERIFIED (CRC64 {job.remote_crc64})"
    if job.status is JobStatus.VERIFY_FAILED:
        return "VERIFY FAILED"
    if job.status is JobStatus.VERIFYING:
        return "VERIFYING..."
    if job.status is JobStatus.UPLOADED:
        return "NOT VERIFIED YET"
    return "PENDING"


def render_detail(
    job: Job,
    live: LiveStats | None,
    *,
    part_size: int,
    now: float,
    unicode: bool = True,
    bar_width: int = 24,
    path_width: int = 46,
) -> Text:
    text = Text()

    def line(label: str, value: str, style: str = VALUE) -> None:
        text.append(f"{label:<11}", style=LABEL)
        text.append(value + "\n", style=style)

    text.append(job.name + "\n", style=f"bold {VALUE}")
    text.append(truncate_middle(job.object_uri, path_width, _ellipsis(unicode)) + "\n\n", style=DIM)

    status = f"{job.badge} {job.status.value}"
    if job.resumable and job.status in (JobStatus.PAUSED, JobStatus.FAILED):
        status += " [RESUME]"
    line("Status", status, STATUS_COLORS[job.status])
    line("Uploaded", f"{format_size(job.done_bytes)} / {format_size(job.size)}")
    if job.is_folder:
        line("Contents", f"{job.folder_files:,} file(s), {job.folder_dirs + 1:,} folder(s)")
    line("Progress", f"{render_bar(job.percent, bar_width, unicode=unicode)} {job.percent:6.2f}%", "#D4F53C")

    uploading = job.status is JobStatus.UPLOADING
    activity_at = (
        live.last_activity if live and live.last_activity is not None
        else (live.last_event if live else None)
    )
    activity_recent = activity_at is not None and now - activity_at <= STALL_SECONDS
    progress_recent = bool(
        live and live.last_event is not None
        and now - live.last_event <= PROGRESS_UPDATE_SECONDS
    )
    speed = live.speed if (uploading and live and progress_recent) else None
    if not uploading:
        line("Speed", "--")
    elif activity_recent and not progress_recent:
        line("Speed", "Active")
    else:
        line("Speed", format_speed(speed))
    if uploading:
        stalled = not activity_recent
        if stalled:
            eta_text = format_eta(None, stalled=True)
        elif not progress_recent:
            eta_text = "Working..."
        else:
            eta_text = format_eta(live.eta if live else None)
        line("ETA", eta_text)
    elif job.status is JobStatus.PAUSED:
        line("ETA", format_eta(None, paused=True))
    else:
        line("ETA", "--")

    chosen = choose_part_size(job.size, part_size)
    line("Parts", f"{math.ceil(job.size / chosen)} x {format_size(chosen)}")
    line("Verify", _verify_line(job), STATUS_COLORS[job.status] if job.status is JobStatus.VERIFIED else VALUE)
    if job.message:
        failed = job.status in (JobStatus.FAILED, JobStatus.VERIFY_FAILED)
        line("Message", job.message, ERROR if failed else DIM)
    return text


def render_total(
    jobs: list[Job],
    live: dict[str, LiveStats],
    *,
    unicode: bool = True,
    bar_width: int = 32,
) -> Text:
    text = Text()
    if not jobs:
        text.append("Queue is empty", style=DIM)
        return text

    def line(label: str, value: str) -> None:
        text.append(f"{label:<11}", style=LABEL)
        text.append(value + "\n", style=VALUE)

    total = sum(job.size for job in jobs)
    done = sum(job.done_bytes for job in jobs)
    percent = done / total * 100 if total else 0.0
    verified = sum(1 for job in jobs if job.status is JobStatus.VERIFIED)
    active = next((job for job in jobs if job.status is JobStatus.UPLOADING), None)
    speed = live[active.id].speed if active and active.id in live else None
    eta = (total - done) / speed if speed and speed > 0 else None

    line("Completed", f"{verified} / {len(jobs)}    {format_size(done)} / {format_size(total)}")
    line("Progress", f"{render_bar(percent, bar_width, unicode=unicode)} {percent:6.2f}%")
    line(
        "Speed",
        f"{format_speed(speed)}    Remaining {format_size(total - done)}    "
        f"ETA {format_eta(eta) if active else '--'}",
    )

    groups = [
        ("uploading", {JobStatus.UPLOADING}),
        ("verifying", {JobStatus.UPLOADED, JobStatus.VERIFYING}),
        ("pending", {JobStatus.PENDING}),
        ("paused", {JobStatus.PAUSED}),
        ("failed", {JobStatus.FAILED, JobStatus.VERIFY_FAILED}),
        ("done", {JobStatus.VERIFIED}),
    ]
    parts = []
    for label, statuses in groups:
        count = sum(1 for job in jobs if job.status in statuses)
        if count:
            parts.append(f"{count} {label}")
    text.append(f"{'Queue':<11}", style=LABEL)
    text.append(" | ".join(parts), style=VALUE)
    return text


def render_preflight(info: PreflightInfo, *, unicode: bool = True, note: str = "") -> Text:
    text = Text()

    def line(label: str, value: str, style: str = VALUE) -> None:
        text.append(f"{label:<13}", style=LABEL)
        text.append(value + "\n", style=style)

    text.append("UPLOAD PRE-FLIGHT CHECK\n\n", style="bold #D4F53C")
    line("Source", truncate_middle(info.source, 60, _ellipsis(unicode)))
    line("Destination", truncate_middle(info.object_uri, 60, _ellipsis(unicode)))
    line("File size", format_size(info.size))
    if info.is_folder:
        line("Contents", f"{info.folder_files:,} file(s), {info.folder_dirs + 1:,} folder(s)")
        line("Upload mode", "RECURSIVE (keeps relative paths)")
    line("Part size", format_size(info.part_size))
    line("Parts", str(info.parts))
    line("Region", info.region)
    line("Checksum", "CRC64 (checked by ossutil during transfer)")
    line("Resume", "ENABLED (checkpoint dir below)")
    line("Checkpoint", truncate_middle(info.checkpoint_dir, 60, _ellipsis(unicode)))
    text.append("\nCOMMAND\n", style=LABEL)
    text.append(info.command + "\n", style=DIM)
    for warning in info.warnings:
        text.append(f"\n[!] {warning}", style="#F2B84B")
    if note:
        text.append(f"\n{note}", style="#9AA9B8")
    return text

_STAT_KEYS = (
    ("Content-Length", "Size"),
    ("Last-Modified", "Modified"),
    ("X-Oss-Object-Type", "Type"),
    ("X-Oss-Storage-Class", "Storage"),
    ("Etag", "ETag"),
    ("X-Oss-Hash-Crc64ecma", "CRC64"),
)


def render_object_info(name: str, stat: dict[str, str]) -> Text:
    text = Text()
    text.append(name + "\n", style=f"bold {VALUE}")
    shown = False
    for key, label in _STAT_KEYS:
        if key in stat:
            value = stat[key]
            if key == "Content-Length" and value.isdigit():
                value = f"{format_size(int(value))} ({value} B)"
            text.append(f"{label:<10}", style=LABEL)
            text.append(value + "\n", style=VALUE)
            shown = True
    if not shown:
        text.append("No details returned by ossutil.\n", style=DIM)
    return text


def render_folder_info(name: str, objects: int, total_bytes: int) -> Text:
    text = Text()
    text.append(name + "/\n", style=f"bold {VALUE}")
    text.append(f"{'Objects':<10}", style=LABEL)
    text.append(f"{objects:,}\n", style=VALUE)
    text.append(f"{'Size':<10}", style=LABEL)
    text.append(f"{format_size(total_bytes)} ({total_bytes:,} B)\n", style=VALUE)
    text.append("Counted from the bucket; folder markers are included in the object count.\n", style=DIM)
    return text

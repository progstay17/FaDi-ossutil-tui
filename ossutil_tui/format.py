"""Display formatting. Pure functions so they are easy to test."""
from __future__ import annotations

_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_size(num_bytes: int) -> str:
    if num_bytes < 0:
        raise ValueError("size cannot be negative")
    if num_bytes < 1024:
        return f"{num_bytes} B"
    value = float(num_bytes)
    unit = 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    return f"{value:.2f} {_UNITS[unit]}"


def format_speed(bytes_per_second: float | None) -> str:
    if bytes_per_second is None:
        return "--"
    if bytes_per_second <= 0:
        return "0 B/s"
    return f"{format_size(int(bytes_per_second))}/s"


def format_duration(seconds: float | None) -> str:
    """Format elapsed time as a readable, zero-padded duration."""
    total = int(max(0.0, seconds or 0.0))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    clock = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{days}d {clock}" if days else clock


def format_eta(seconds: float | None, *, paused: bool = False, stalled: bool = False) -> str:
    if paused:
        return "Paused"
    if stalled:
        return "Stalled"
    if seconds is None:
        return "Calculating..."
    total = int(max(0, seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins:02d}m"
    days, hrs = divmod(hours, 24)
    return f"{days}d {hrs:02d}h"


def truncate_middle(text: str, width: int, ellipsis: str = "…") -> str:
    """Shorten in the middle, keeping more of the end (file name and extension)."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= len(ellipsis):
        return text[-width:]
    keep = width - len(ellipsis)
    right = (keep + 1) // 2
    left = keep - right
    return text[:left] + ellipsis + text[-right:]


def render_bar(percent: float, width: int, *, unicode: bool = True) -> str:
    clamped = min(100.0, max(0.0, percent))
    filled = int(width * clamped / 100)
    full, empty = ("█", "░") if unicode else ("#", "-")
    return full * filled + empty * (width - filled)
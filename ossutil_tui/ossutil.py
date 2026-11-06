"""Helpers for the ossutil 2.x CLI. Pure functions, no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass

# ossutil 2.x includes object count, transferred size, and directory count
# inside done:(...). Older versions / simpler output may only include objects.
PROGRESS_RE = re.compile(r"done:\([^)]*\)\s*,\s*([\d.]+)\s*%", re.IGNORECASE)
STAT_LINE_RE = re.compile(r"^\s*([A-Za-z0-9-]+)\s*:\s*(.+?)\s*$")

MIN_PART_SIZE = 100 * 1024
MAX_PART_SIZE = 5 * 1024**3


def normalize_region(raw: str) -> str:
    """'oss-ap-southeast-5' / 'oss-ap-southeast-5.aliyuncs.com' -> 'ap-southeast-5'."""
    value = raw.strip().lower()
    for prefix in ("https://", "http://"):
        value = value.removeprefix(prefix)
    if value.endswith(".aliyuncs.com"):
        value = value[: -len(".aliyuncs.com")]
    if value.startswith("oss-"):
        value = value[len("oss-"):]
    return value


@dataclass(frozen=True)
class CpRequest:
    exe: str
    source: str
    destination: str
    config_file: str
    region: str
    checkpoint_dir: str
    part_size: int
    overwrite: bool = False
    recursive: bool = False
    update: bool = False

    def __post_init__(self) -> None:
        if not self.destination.startswith("oss://"):
            raise ValueError("destination must start with oss://")
        if not self.checkpoint_dir.strip():
            raise ValueError("checkpoint_dir is required, otherwise resume is disabled")
        if not MIN_PART_SIZE <= self.part_size <= MAX_PART_SIZE:
            raise ValueError("part_size must be between 100 KiB and 5 GiB")
        if not normalize_region(self.region):
            raise ValueError("region is required")

    def argv(self) -> list[str]:
        args = [
            self.exe, "cp", self.source, self.destination,
            "--config-file", self.config_file,
            "--region", normalize_region(self.region),
            "--checkpoint-dir", self.checkpoint_dir,
            "--part-size", str(self.part_size),
            "--output-format", "json",
        ]
        if self.recursive:
            args.append("-r")
        if self.update:
            args.append("-u")
        if self.overwrite:
            args.append("-f")
        return args


def parse_progress(text: str) -> float | None:
    """Return the last percentage in a chunk of ossutil output, or None.

    ossutil rewrites the same line with carriage returns. Its progress summary
    can contain extra fields such as done:(108 objects,7.572 GiB,5 dirs), so
    match the complete parenthesized payload rather than assuming it only
    contains an object count. The last match wins for multi-update chunks.
    """
    matches = PROGRESS_RE.findall(text)
    return float(matches[-1]) if matches else None


def is_success(text: str) -> bool:
    return "Success:" in text


def parse_stat(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = STAT_LINE_RE.match(line)
        if match:
            result[match.group(1)] = match.group(2)
    return result


MAX_PARTS = 10_000
_MIB = 1024 * 1024


def choose_part_size(file_size: int, preferred: int) -> int:
    """Keep the preferred part size unless it would exceed the 10,000 part limit."""
    if file_size <= 0 or -(-file_size // preferred) <= MAX_PARTS:
        return preferred
    needed = -(-file_size // MAX_PARTS)
    rounded = -(-needed // _MIB) * _MIB
    return min(max(rounded, MIN_PART_SIZE), MAX_PART_SIZE)

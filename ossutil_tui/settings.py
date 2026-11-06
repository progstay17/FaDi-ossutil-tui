"""Runtime settings and their validation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .ossutil import MAX_PART_SIZE, MIN_PART_SIZE, normalize_region

_REGION_RE = re.compile(r"[a-z]{2,}-[a-z0-9-]+")


@dataclass(frozen=True)
class Settings:
    exe: str
    config_file: str
    region: str
    checkpoint_dir: str
    state_file: str
    log_dir: str
    part_size: int = 4 * 1024 * 1024
    unicode: bool = True

    def problems(self) -> list[str]:
        found: list[str] = []
        if not Path(self.exe).is_file():
            found.append(f"ossutil executable not found: {self.exe}")
        if not Path(self.config_file).is_file():
            found.append(f"config file not found: {self.config_file}")
        region = normalize_region(self.region)
        if not region:
            found.append("region is empty")
        elif not _REGION_RE.fullmatch(region):
            found.append(f"region does not look valid: {self.region!r} (expected e.g. ap-southeast-5)")
        if not self.checkpoint_dir.strip():
            found.append("checkpoint dir is required, otherwise resume is disabled")
        if not MIN_PART_SIZE <= self.part_size <= MAX_PART_SIZE:
            found.append("part size must be between 100 KiB and 5 GiB")
        return found
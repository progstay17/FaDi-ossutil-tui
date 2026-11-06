"""Atomic JSON state for the job queue. Written to a temp file, then renamed."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class StateCorruptError(RuntimeError):
    pass


def save_state(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=target.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def load_state(path: str | Path, default: Any) -> Any:
    target = Path(path)
    if not target.exists():
        return default
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateCorruptError(f"state file is corrupt: {target}") from exc
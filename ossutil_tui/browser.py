"""Read-only browsing helpers: parse `ossutil ls -d` and `du` output, and handle oss:// addresses."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from enum import Enum


class EntryKind(str, Enum):
    DIR = "dir"
    FILE = "file"


@dataclass(frozen=True)
class Entry:
    kind: EntryKind
    name: str  # relative to the listed folder; no trailing slash for folders
    uri: str   # full oss:// address; folders end with "/"


@dataclass(frozen=True)
class ListPage:
    prefix: str
    entries: list[Entry]
    has_more: bool
    last_key: str | None  # pass to --start-after to get the next page
    total_returned: int   # includes the folder's own marker object when present


_COUNT_RE = re.compile(r"Object and Directory Number is:\s*(\d+)")
_DU_RE = re.compile(r"total object count:\s*(\d+)\s+total object sum size:\s*(\d+)")


def split_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("oss://"):
        raise ValueError("not an oss:// address")
    bucket, _, key = uri[len("oss://"):].partition("/")
    if not bucket:
        raise ValueError("missing bucket name")
    return bucket, key


def normalize_dir_uri(uri: str) -> str:
    bucket, key = split_uri(uri.strip())
    key = key.strip("/")
    return f"oss://{bucket}/" + (key + "/" if key else "")


def parent_uri(uri: str) -> str | None:
    """The folder one level up, or None when already at the bucket root."""
    bucket, key = split_uri(normalize_dir_uri(uri))
    parts = [part for part in key.split("/") if part]
    if not parts:
        return None
    upper = parts[:-1]
    return f"oss://{bucket}/" + ("/".join(upper) + "/" if upper else "")


def folder_of(destination: str) -> str:
    """The folder an upload destination lives in (a destination may be a folder or a full key)."""
    bucket, key = split_uri(destination.strip())
    if key and not key.endswith("/"):
        key = key.rsplit("/", 1)[0] + "/" if "/" in key else ""
    return normalize_dir_uri(f"oss://{bucket}/{key}")


def parse_dir_listing(text: str, prefix_uri: str, limit: int) -> ListPage:
    """Parse `ossutil ls <folder> -d`: one oss:// address per line, then a count line."""
    uris = [line.rstrip("\r\n") for line in text.splitlines() if line.startswith("oss://")]
    entries: list[Entry] = []
    for uri in uris:
        if uri == prefix_uri or not uri.startswith(prefix_uri):
            continue  # the folder's own marker object, or something unrelated
        name = uri[len(prefix_uri):]
        if name.endswith("/"):
            entries.append(Entry(EntryKind.DIR, name.rstrip("/"), uri))
        else:
            entries.append(Entry(EntryKind.FILE, name, uri))
    match = _COUNT_RE.search(text)
    returned = int(match.group(1)) if match else len(uris)
    last_key = split_uri(uris[-1])[1] if uris else None
    return ListPage(prefix_uri, entries, limit > 0 and returned >= limit, last_key, returned)


def parse_du(text: str) -> tuple[int, int] | None:
    """(object count, total bytes) from `ossutil du`, or None when the summary line is missing."""
    match = _DU_RE.search(text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def similar_names(missing: str, existing: list[str], *, cutoff: float = 0.85, limit: int = 3) -> list[str]:
    """Existing folder names that look like a mistyped version of `missing`."""
    by_lower = {name.lower(): name for name in existing}
    close = difflib.get_close_matches(missing.lower(), list(by_lower), n=limit, cutoff=cutoff)
    return [by_lower[name] for name in close if name != missing.lower()]
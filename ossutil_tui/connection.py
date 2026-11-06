"""Credentials go to ossutil only through the child process environment, never argv or disk."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

ExecFn = Callable[[list[str], dict[str, str]], Awaitable[tuple[int, str, str]]]


def build_env(access_key_id: str, secret: str, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy the environment, drop every OSS_* variable so nothing else can interfere, add ours."""
    source = os.environ if base is None else base
    env = {key: value for key, value in source.items() if not key.upper().startswith("OSS_")}
    env["OSS_ACCESS_KEY_ID"] = access_key_id
    env["OSS_ACCESS_KEY_SECRET"] = secret
    return env


@dataclass(frozen=True)
class ConnectionResult:
    ok: bool
    kind: str  # ok | key | access | bucket | region | network | exe | unknown
    message: str


_RULES: list[tuple[str, str, str]] = [
    ("InvalidAccessKeyId", "key", "AccessKey ID is not recognised. Check it for typos."),
    ("SignatureDoesNotMatch", "key", "AccessKey Secret does not match this AccessKey ID."),
    ("NoSuchBucket", "bucket", "Bucket not found. Check the name, and that it exists in this account."),
    ("IncorrectEndpoint", "region", "The bucket is in a different region. Check the region ID."),
    ("must be addressed using the specified endpoint", "region", "The bucket is in a different region. Check the region ID."),
    ("AuthorizationHeaderMalformed", "region", "The region ID does not match the bucket. Check the region ID."),
    ("region must be set", "region", "Region is missing."),
    ("AccessDenied", "access", "Access denied. The AccessKey has no permission for this bucket, or the bucket belongs to another account."),
    ("no such host", "network", "Cannot reach OSS. Check the network and that the region matches the bucket."),
    ("i/o timeout", "network", "Cannot reach OSS (timeout). Check the network."),
    ("connectex", "network", "Cannot reach OSS. Check the network."),
    ("dial tcp", "network", "Cannot reach OSS. Check the network."),
    ("timeout", "network", "Cannot reach OSS (timeout). Check the network."),
]


def classify_failure(text: str) -> tuple[str, str]:
    lowered = text.lower()
    for needle, kind, message in _RULES:
        if needle.lower() in lowered:
            return kind, message
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "unknown", (lines[-1][:200] if lines else "ossutil failed without a message.")


async def _exec(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        raise
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def check_connection(
    *,
    exe: str,
    config_file: str,
    region: str,
    bucket: str,
    access_key_id: str,
    secret: str,
    exec_fn: ExecFn | None = None,
    timeout: float = 25.0,
) -> ConnectionResult:
    """Read-only check: list at most one object of the bucket with the entered credentials."""
    argv = [
        exe, "ls", f"oss://{bucket}", "--limited-num", "1",
        "--region", region, "--config-file", config_file,
        "--retry-times", "1", "--connect-timeout", "8",
    ]
    env = build_env(access_key_id, secret)
    try:
        code, out, err = await asyncio.wait_for((exec_fn or _exec)(argv, env), timeout)
    except asyncio.TimeoutError:
        return ConnectionResult(False, "network", "Timed out while contacting OSS. Check the network.")
    except OSError as exc:
        return ConnectionResult(False, "exe", f"Cannot run ossutil: {exc}")
    if code == 0:
        return ConnectionResult(True, "ok", "Connection OK.")
    kind, message = classify_failure(f"{err}\n{out}")
    return ConnectionResult(False, kind, message)

run_command = _exec

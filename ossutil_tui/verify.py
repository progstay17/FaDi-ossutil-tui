"""Post-upload verification in M1: size match plus presence of CRC64 from stat.

Local CRC64 comparison is intentionally not done in M1 (decided by the owner).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VerifyStatus(str, Enum):
    VERIFIED = "VERIFIED"
    VERIFY_FAILED = "VERIFY FAILED"


@dataclass(frozen=True)
class VerifyResult:
    status: VerifyStatus
    reason: str
    remote_crc64: str | None = None


def verify_remote(local_size: int, remote_stat: dict[str, str]) -> VerifyResult:
    content_length = remote_stat.get("Content-Length")
    crc64 = remote_stat.get("X-Oss-Hash-Crc64ecma")
    if content_length is None:
        return VerifyResult(VerifyStatus.VERIFY_FAILED, "remote size missing from stat")
    try:
        remote_size = int(content_length)
    except ValueError:
        return VerifyResult(VerifyStatus.VERIFY_FAILED, "remote size is not a number")
    if remote_size != local_size:
        return VerifyResult(
            VerifyStatus.VERIFY_FAILED,
            f"size mismatch: local {local_size}, remote {remote_size}",
        )
    if not crc64:
        return VerifyResult(VerifyStatus.VERIFY_FAILED, "CRC64 missing from stat")
    return VerifyResult(VerifyStatus.VERIFIED, "size matches, CRC64 present", crc64)
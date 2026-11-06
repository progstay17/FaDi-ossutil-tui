"""Login profile: validated input, a Windows-DPAPI protected secret, and a small JSON store."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .ossutil import normalize_region
from .state import save_state

_KEY_RE = re.compile(r"[A-Za-z0-9._-]{8,64}")
_BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
_REGION_RE = re.compile(r"[a-z]{2,}-[a-z0-9-]+")
_ENTROPY = b"FaDi-OSS-TUI-v1"
_UI_FORBIDDEN = 0x1


class ProfileError(RuntimeError):
    """A problem with the saved or entered login. The message is safe to show."""


class SecretProtector(Protocol):
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, data: bytes) -> bytes: ...


if sys.platform == "win32":
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _make_blob(data: bytes):
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buffer  # keep the buffer referenced while the blob is in use

    def _read_blob(blob) -> bytes:
        address = ctypes.cast(blob.pbData, ctypes.c_void_p).value
        try:
            return ctypes.string_at(address, blob.cbData)
        finally:
            _kernel32.LocalFree(address)


class DpapiProtector:
    """Encrypts with the current Windows user's key. Other users and other PCs cannot decrypt."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ProfileError("Secure credential storage needs Windows (DPAPI)")

    def protect(self, data: bytes) -> bytes:
        source, keep_source = _make_blob(data)
        entropy, keep_entropy = _make_blob(_ENTROPY)
        result = _DataBlob()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(source), "FaDi OSS TUI", ctypes.byref(entropy),
            None, None, _UI_FORBIDDEN, ctypes.byref(result),
        )
        if not ok:
            raise ProfileError(f"Cannot protect the secret (Windows error {ctypes.get_last_error()})")
        return _read_blob(result)

    def unprotect(self, data: bytes) -> bytes:
        source, keep_source = _make_blob(data)
        entropy, keep_entropy = _make_blob(_ENTROPY)
        result = _DataBlob()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(source), None, ctypes.byref(entropy),
            None, None, _UI_FORBIDDEN, ctypes.byref(result),
        )
        if not ok:
            raise ProfileError(
                "Cannot decrypt the saved secret. It was saved by another Windows user or PC, "
                "or the file is damaged. Please log in again."
            )
        return _read_blob(result)


@dataclass(frozen=True)
class Profile:
    name: str
    access_key_id: str
    region: str
    bucket: str
    exe: str
    prefix: str = ""

    @property
    def root_uri(self) -> str:
        return f"oss://{self.bucket}/" + (self.prefix.strip("/") + "/" if self.prefix.strip("/") else "")

    def masked_key(self) -> str:
        key = self.access_key_id
        return f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "****"


@dataclass(frozen=True)
class LoginCheck:
    profile: Profile | None
    secret: str
    errors: list[str] = field(default_factory=list)


def _normalize_target(raw: str) -> tuple[str, str]:
    value = raw.strip()
    if value.lower().startswith("oss://"):
        value = value[6:]
    bucket, _, prefix = value.partition("/")
    if "?" in prefix or "#" in prefix or "\\" in prefix:
        return bucket, "!invalid!"
    return bucket, prefix.strip("/")


def validate_login(*, access_key_id: str, secret: str, region: str, bucket: str, exe: str) -> LoginCheck:
    errors: list[str] = []
    key = access_key_id.strip()
    secret_value = secret.strip()
    exe_value = exe.strip().strip('"')
    normalized_region = normalize_region(region)
    normalized_bucket, normalized_prefix = _normalize_target(bucket)

    if not _KEY_RE.fullmatch(key):
        errors.append("AccessKey ID looks invalid (8-64 letters or digits, no spaces).")
    if not secret_value:
        errors.append("AccessKey Secret is empty.")
    elif len(secret_value) < 8 or re.search(r"\s", secret_value):
        errors.append("AccessKey Secret looks invalid (at least 8 characters, no spaces).")
    if not normalized_region:
        errors.append("Region is empty (example: ap-southeast-5).")
    elif not _REGION_RE.fullmatch(normalized_region):
        errors.append(f"Region does not look valid: {region.strip()!r} (example: ap-southeast-5).")
    if not _BUCKET_RE.fullmatch(normalized_bucket):
        errors.append("Bucket name looks invalid (lowercase letters, digits, hyphens; 3-63 characters).")
    if normalized_prefix == "!invalid!":
        errors.append("OSS prefix must not contain query strings, fragments, or backslashes.")
    if not exe_value or not Path(exe_value).is_file():
        errors.append(f"ossutil not found: {exe_value or '(empty)'}")

    if errors:
        return LoginCheck(None, secret_value, errors)
    profile = Profile("default", key, normalized_region, normalized_bucket, exe_value, normalized_prefix)
    return LoginCheck(profile, secret_value, [])


class ProfileStore:
    def __init__(self, path: str | Path, protector: SecretProtector) -> None:
        self.path = Path(path)
        self._protector = protector

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, profile: Profile, secret: str) -> None:
        encrypted = self._protector.protect(secret.encode("utf-8"))
        save_state(
            self.path,
            {
                "version": 1,
                "profile": {**asdict(profile), "secret_enc": base64.b64encode(encrypted).decode("ascii")},
            },
        )

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw["profile"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProfileError(f"The saved profile is missing or damaged: {self.path}") from exc

    def load(self) -> Profile:
        raw = self._read()
        try:
            return Profile(
                name=str(raw["name"]),
                access_key_id=str(raw["access_key_id"]),
                region=str(raw["region"]),
                bucket=str(raw["bucket"]),
                exe=str(raw["exe"]),
                prefix=str(raw.get("prefix", "")),
            )
        except KeyError as exc:
            raise ProfileError("The saved profile is incomplete. Please log in again.") from exc

    def load_secret(self) -> str:
        raw = self._read()
        try:
            encrypted = base64.b64decode(raw["secret_enc"], validate=True)
        except (KeyError, ValueError) as exc:
            raise ProfileError("The saved secret is damaged. Please log in again.") from exc
        try:
            return self._protector.unprotect(encrypted).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProfileError("The saved secret is damaged. Please log in again.") from exc

    def delete(self) -> bool:
        if self.path.is_file():
            os.remove(self.path)
            return True
        return False
"""App-wide state: file locations, the login session, and the controller built from it."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .connection import build_env, check_connection
from .controller import JobController, JobError
from .jobs import JobStore
from .profile import Profile, ProfileError, ProfileStore
from .settings import Settings


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @classmethod
    def default(cls, root: str | None = None) -> "AppPaths":
        if root:
            return cls(Path(root))
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return cls(Path(base) / "FaDi-OSS-TUI")

    @property
    def profile_file(self) -> Path:
        return self.root / "profile.json"

    @property
    def state_file(self) -> Path:
        return self.root / "queue.json"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    @property
    def checkpoint_dir(self) -> Path:
        return self.root / "checkpoints"

    @property
    def empty_config(self) -> Path:
        return self.root / "ossutil.empty.cfg"

    @property
    def bundled_ossutil(self) -> Path:
        """Where install.ps1 -InstallOssutil puts ossutil."""
        return self.root / "ossutil" / "ossutil.exe"


@dataclass(frozen=True)
class LoginDefaults:
    exe: str = ""
    access_key_id: str = ""
    region: str = ""
    bucket: str = ""


class AppContext:
    def __init__(
        self,
        paths: AppPaths,
        store: ProfileStore,
        *,
        part_size: int = 4 * 1024 * 1024,
        unicode: bool = True,
        exe_hint: str = "",
        check_fn=check_connection,
    ) -> None:
        self.paths = paths
        self.store = store
        self.part_size = part_size
        self.unicode = unicode
        self.exe_hint = exe_hint
        self.check_fn = check_fn
        self.profile: Profile | None = None
        self.settings: Settings | None = None
        self.controller: JobController | None = None
        self.session_notice = ""
        self._secret: str | None = None
        self._remembered = LoginDefaults()

    @property
    def logged_in(self) -> bool:
        return self.controller is not None

    def _env(self) -> dict[str, str] | None:
        if self.profile is None or self._secret is None:
            return None
        return build_env(self.profile.access_key_id, self._secret)

    def ensure_empty_config(self) -> str:
        """An app-owned config with no credentials, so ~/.ossutilconfig can never interfere."""
        self.paths.root.mkdir(parents=True, exist_ok=True)
        if not self.paths.empty_config.is_file():
            self.paths.empty_config.write_text("[default]\n", encoding="ascii")
        return str(self.paths.empty_config)

    def _build_settings(self, profile: Profile) -> Settings:
        self.ensure_empty_config()
        settings = Settings(
            exe=profile.exe,
            config_file=str(self.paths.empty_config),
            region=profile.region,
            checkpoint_dir=str(self.paths.checkpoint_dir),
            state_file=str(self.paths.state_file),
            log_dir=str(self.paths.log_dir),
            part_size=self.part_size,
            unicode=self.unicode,
        )
        problems = settings.problems()
        if problems:
            raise ProfileError("; ".join(problems))
        return settings

    def activate(self, profile: Profile, secret: str) -> None:
        settings = self._build_settings(profile)
        controller = JobController(settings, JobStore(settings.state_file), env_fn=self._env)
        controller.default_root = f"oss://{profile.bucket}/"
        self.profile, self._secret, self.settings, self.controller = profile, secret, settings, controller

    def adopt(self, controller: JobController, settings: Settings, profile: Profile | None = None) -> None:
        """Attach an already built controller (used by tests)."""
        self.controller, self.settings, self.profile = controller, settings, profile

    def resume_session(self) -> str | None:
        """Log in from the saved profile. Returns a message if it exists but cannot be used."""
        self.session_notice = ""
        if not self.store.exists():
            return None
        try:
            profile = self.store.load()
            secret = self.store.load_secret()
            self.activate(profile, secret)
        except ProfileError as exc:
            self.session_notice = str(exc)
            return self.session_notice
        return None

    def save_login(self, profile: Profile, secret: str) -> None:
        self.store.save(profile, secret)
        try:
            self.activate(profile, secret)
        except Exception:
            self.store.delete()
            raise

    def logout(self) -> None:
        if self.controller is not None and self.controller.is_busy():
            raise JobError("An upload is running. Pause it first, then log out.")
        if self.profile is not None:
            self._remembered = LoginDefaults(self.profile.exe, "", self.profile.region, self.profile.bucket)
        self.store.delete()
        if self.controller is not None:
            self.controller.set_on_change(None)
        self.profile = self.settings = self.controller = None
        self._secret = None

    def login_defaults(self) -> LoginDefaults:
        if self.store.exists():
            try:
                saved = self.store.load()
                return LoginDefaults(saved.exe, saved.access_key_id, saved.region, saved.bucket)
            except ProfileError:
                pass
        bundled = str(self.paths.bundled_ossutil) if self.paths.bundled_ossutil.is_file() else ""
        exe = self._remembered.exe or self.exe_hint or bundled or shutil.which("ossutil") or ""
        return LoginDefaults(exe, "", self._remembered.region, self._remembered.bucket)
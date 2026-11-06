import json

import pytest

from ossutil_tui.context import AppContext, AppPaths
from ossutil_tui.controller import JobError
from ossutil_tui.profile import Profile, ProfileStore
from tests.fakes import BrokenProtector, FakeProtector

SECRET = "S3cretValue-123456"


def make_ctx(tmp_path, protector=None):
    exe = tmp_path / "ossutil.exe"
    exe.write_text("x")
    paths = AppPaths(tmp_path / "app")
    store = ProfileStore(paths.profile_file, protector or FakeProtector())
    return AppContext(paths, store, exe_hint=str(exe)), exe


def make_profile(exe):
    return Profile("default", "FAKEKEYID12345678", "ap-southeast-5", "bucket-sandbox", str(exe))


def test_no_profile_means_not_logged_in(tmp_path):
    ctx, exe = make_ctx(tmp_path)
    assert ctx.resume_session() is None
    assert not ctx.logged_in
    assert ctx.login_defaults().exe == str(exe)


def test_login_then_resume_in_a_new_session(tmp_path):
    ctx, exe = make_ctx(tmp_path)
    ctx.save_login(make_profile(exe), SECRET)
    assert ctx.logged_in
    again, _ = make_ctx(tmp_path)
    assert again.resume_session() is None
    assert again.logged_in and again.profile == make_profile(exe)
    env = again.controller._env_fn()
    assert env["OSS_ACCESS_KEY_ID"] == "FAKEKEYID12345678"
    assert env["OSS_ACCESS_KEY_SECRET"] == SECRET


def test_ossutil_gets_an_app_owned_config_without_credentials(tmp_path):
    ctx, exe = make_ctx(tmp_path)
    ctx.save_login(make_profile(exe), SECRET)
    config = ctx.paths.empty_config
    assert config.is_file()
    assert SECRET not in config.read_text(encoding="ascii")
    assert ctx.settings.config_file == str(config)
    assert ctx.settings.state_file == str(ctx.paths.state_file)
    assert ctx.settings.checkpoint_dir == str(ctx.paths.checkpoint_dir)


def test_secret_is_not_stored_in_plain_text_anywhere(tmp_path):
    ctx, exe = make_ctx(tmp_path)
    ctx.save_login(make_profile(exe), SECRET)
    for file in (tmp_path / "app").rglob("*"):
        if file.is_file():
            assert SECRET not in file.read_text(encoding="utf-8", errors="ignore")


def test_undecryptable_secret_returns_a_notice_and_stays_logged_out(tmp_path):
    ctx, exe = make_ctx(tmp_path)
    ctx.save_login(make_profile(exe), SECRET)
    broken, _ = make_ctx(tmp_path, protector=BrokenProtector())
    message = broken.resume_session()
    assert "Cannot decrypt" in message and broken.session_notice == message
    assert not broken.logged_in
    assert broken.login_defaults().access_key_id == "FAKEKEYID12345678"


def test_moved_ossutil_falls_back_to_the_bundled_binary_for_relogin(tmp_path):
    ctx, exe = make_ctx(tmp_path)
    ctx.save_login(make_profile(exe), SECRET)
    exe.unlink()
    bundled = ctx.paths.bundled_ossutil
    bundled.parent.mkdir(parents=True)
    bundled.write_text("bundled")
    again = AppContext(AppPaths(tmp_path / "app"), ProfileStore(ctx.paths.profile_file, FakeProtector()))
    assert "ossutil executable not found" in again.resume_session()
    assert not again.logged_in
    assert again.login_defaults().exe == str(bundled)


def test_failed_activation_does_not_leave_a_half_saved_profile(tmp_path):
    ctx, exe = make_ctx(tmp_path)
    bad = make_profile(tmp_path / "missing.exe")
    with pytest.raises(Exception, match="executable not found"):
        ctx.save_login(bad, SECRET)
    assert not ctx.store.exists() and not ctx.logged_in


def test_logout_removes_profile_but_keeps_queue_and_remembers_non_secret_defaults(tmp_path):
    ctx, exe = make_ctx(tmp_path)
    ctx.save_login(make_profile(exe), SECRET)
    source = tmp_path / "FaDi_a.bin"
    source.write_bytes(b"x" * 10)
    ctx.controller.add_job(str(source), "oss://bucket-sandbox/FaDi/")
    ctx.logout()
    assert not ctx.store.exists() and not ctx.logged_in
    assert ctx.controller is None and ctx._secret is None
    queue = json.loads(ctx.paths.state_file.read_text(encoding="utf-8"))
    assert len(queue["jobs"]) == 1
    defaults = ctx.login_defaults()
    assert defaults.region == "ap-southeast-5" and defaults.bucket == "oss://bucket-sandbox/"
    assert defaults.access_key_id == ""


def test_logout_is_refused_while_an_upload_runs(tmp_path):
    ctx, exe = make_ctx(tmp_path)
    ctx.save_login(make_profile(exe), SECRET)
    ctx.controller._runners["job"] = object()
    with pytest.raises(JobError, match="Pause it first"):
        ctx.logout()
    assert ctx.store.exists() and ctx.logged_in

def test_login_defaults_use_the_ossutil_installed_next_to_the_app(tmp_path):
    paths = AppPaths(tmp_path / "app")
    paths.bundled_ossutil.parent.mkdir(parents=True)
    paths.bundled_ossutil.write_text("x")
    ctx = AppContext(paths, ProfileStore(paths.profile_file, FakeProtector()))
    assert ctx.login_defaults().exe == str(paths.bundled_ossutil)


def test_a_valid_explicit_exe_hint_beats_the_bundled_one(tmp_path):
    paths = AppPaths(tmp_path / "app")
    paths.bundled_ossutil.parent.mkdir(parents=True)
    paths.bundled_ossutil.write_text("x")
    explicit = tmp_path / "custom" / "ossutil.exe"
    explicit.parent.mkdir()
    explicit.write_text("custom")
    ctx = AppContext(paths, ProfileStore(paths.profile_file, FakeProtector()), exe_hint=str(explicit))
    assert ctx.login_defaults().exe == str(explicit)
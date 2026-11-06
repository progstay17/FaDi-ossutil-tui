import asyncio

from textual.widgets import Button, DataTable, Input

from ossutil_tui.app import FaDiApp, LoginScreen, LogoutScreen, MainScreen
from ossutil_tui.connection import ConnectionResult
from ossutil_tui.context import AppContext, AppPaths
from ossutil_tui.jobs import JobStatus
from ossutil_tui.profile import Profile, ProfileStore
from tests.fakes import (
    FAKE_SLOW, BrokenProtector, FakeProtector, make_context, make_controller, make_source, press_start,
)

SIZE = (100, 30)
SECRET = "S3cretValue-123456"
OK = ConnectionResult(True, "ok", "Connection OK.")
BAD_KEY = ConnectionResult(False, "key", "AccessKey ID is not recognised. Check it for typos.")
DEST = "oss://bucket-sandbox/FaDi-spike/"


def run(coro, timeout=40):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


async def wait_for(pilot, predicate, timeout=15.0):
    for _ in range(int(timeout / 0.1)):
        if predicate():
            return True
        await pilot.pause(0.1)
    return False


def make_login_app(tmp_path, result=OK):
    calls = []

    async def check_fn(**kwargs):
        calls.append(kwargs)
        return result

    context = make_context(tmp_path, check_fn=check_fn)
    return FaDiApp(context), context, calls


def fill(screen, **overrides):
    values = dict(key="FAKEKEYID12345678", secret=SECRET, region="oss-ap-southeast-5", bucket="bucket-sandbox")
    values.update(overrides)
    for name, value in values.items():
        screen.query_one(f"#login-{name}", Input).value = value


def test_starts_on_login_when_there_is_no_profile(tmp_path):
    app, context, _ = make_login_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, LoginScreen)
            assert not app.screen.query("#login-exe")
            assert app.screen.query_one("#login-key", Input).has_focus
            assert app.screen.query_one("#login-secret", Input).password is True
            login = app.screen.query_one("#login", Button)
            quit_button = app.screen.query_one("#quit", Button)
            for widget in (login, quit_button):
                region = widget.region
                assert region.right <= SIZE[0] and region.bottom <= SIZE[1]

    run(scenario())


def test_successful_login_saves_profile_and_opens_main_screen(tmp_path):
    app, context, calls = make_login_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            fill(app.screen)
            app.screen.query_one("#login", Button).press()
            assert await wait_for(pilot, lambda: isinstance(app.screen, MainScreen))
            await pilot.pause(0.6)
            assert context.logged_in and context.store.exists()
            assert calls[0]["region"] == "ap-southeast-5" and calls[0]["bucket"] == "bucket-sandbox"
            assert calls[0]["secret"] == SECRET
            assert "bucket-sandbox" in app.screen.last_header_text
            assert "FAKE...5678" in app.screen.last_header_text
            assert SECRET not in app.screen.last_header_text

    run(scenario())


def test_wrong_credentials_keep_the_login_screen_and_save_nothing(tmp_path):
    app, context, calls = make_login_app(tmp_path, result=BAD_KEY)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            fill(app.screen)
            app.screen.query_one("#login", Button).press()
            assert await wait_for(pilot, lambda: "not recognised" in app.screen.last_status_text)
            assert isinstance(app.screen, LoginScreen)
            assert not context.store.exists() and not context.logged_in
            assert app.screen.query_one("#login", Button).disabled is False

    run(scenario())


def test_invalid_input_is_reported_without_contacting_oss(tmp_path):
    app, context, calls = make_login_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            fill(app.screen, region="Jakarta", secret="")
            app.screen.query_one("#login", Button).press()
            assert await wait_for(pilot, lambda: "Region does not look valid" in app.screen.last_status_text)
            assert "Secret is empty" in app.screen.last_status_text
            assert calls == []

    run(scenario())


def test_already_logged_in_skips_the_login_screen(tmp_path):
    controller = make_controller(tmp_path)
    app = FaDiApp(make_context(tmp_path, controller=controller))

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MainScreen)

    run(scenario())


def test_saved_profile_with_undecryptable_secret_shows_a_notice_and_prefills(tmp_path):
    exe = tmp_path / "ossutil.exe"
    exe.write_text("x")
    paths = AppPaths(tmp_path / "ctx")
    ProfileStore(paths.profile_file, FakeProtector()).save(
        Profile("default", "FAKEKEYID12345678", "ap-southeast-5", "bucket-sandbox", str(exe)), SECRET
    )
    context = AppContext(paths, ProfileStore(paths.profile_file, BrokenProtector()))
    context.resume_session()
    app = FaDiApp(context)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, LoginScreen)
            assert "Cannot decrypt" in app.screen.last_status_text
            assert app.screen.query_one("#login-key", Input).value == "FAKEKEYID12345678"
            assert app.screen.query_one("#login-secret", Input).value == ""

    run(scenario())


async def log_in(app, pilot):
    await pilot.pause()
    fill(app.screen)
    app.screen.query_one("#login", Button).press()
    assert await wait_for(pilot, lambda: isinstance(app.screen, MainScreen))
    await pilot.pause(0.6)


def test_logout_returns_to_login_removes_profile_and_keeps_the_queue(tmp_path):
    app, context, _ = make_login_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await log_in(app, pilot)
            context.controller.add_job(str(make_source(tmp_path)), DEST)
            await pilot.pause(0.6)
            await pilot.press("l")
            await pilot.pause()
            assert isinstance(app.screen, LogoutScreen)
            app.screen.query_one("#confirm", Button).press()
            assert await wait_for(pilot, lambda: isinstance(app.screen, LoginScreen))
            assert not context.store.exists() and not context.logged_in
            assert context.paths.state_file.is_file()
            assert "Logged out" in app.screen.last_status_text
            assert app.screen.query_one("#login-region", Input).value == "ap-southeast-5"
            assert app.screen.query_one("#login-key", Input).value == ""

    run(scenario())


def test_logout_can_be_cancelled(tmp_path):
    app, context, _ = make_login_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await log_in(app, pilot)
            await pilot.press("l")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainScreen)
            assert context.logged_in and context.store.exists()

    run(scenario())


def test_logout_warns_about_resumable_jobs(tmp_path):
    app, context, _ = make_login_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await log_in(app, pilot)
            job = context.controller.add_job(str(make_source(tmp_path)), DEST)
            job.status = JobStatus.PAUSED
            job.resumable = True
            await pilot.pause(0.6)
            await pilot.press("l")
            await pilot.pause()
            assert isinstance(app.screen, LogoutScreen)
            body = " ".join(str(w.render()) for w in app.screen.query("Static"))
            assert "1 job(s) can be resumed" in body

    run(scenario())


def test_logout_is_refused_while_an_upload_runs(tmp_path):
    controller = make_controller(tmp_path, code=FAKE_SLOW)
    controller.add_job(str(make_source(tmp_path)), DEST)
    context = make_context(tmp_path, controller=controller)
    app = FaDiApp(context)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await press_start(pilot, app)
            job = controller.jobs[0]
            assert await wait_for(pilot, lambda: job.status is JobStatus.UPLOADING and job.percent >= 30)
            await pilot.press("l")
            await pilot.pause()
            assert isinstance(app.screen, MainScreen)
            assert context.logged_in
            await pilot.press("p")
            assert await wait_for(pilot, lambda: job.status is JobStatus.PAUSED)

    run(scenario())
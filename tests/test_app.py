import asyncio
from dataclasses import replace

from textual.widgets import Button, DataTable, Input

from ossutil_tui.app import AddJobScreen, FaDiApp, MainScreen, PreflightScreen
from ossutil_tui.jobs import JobStatus
from tests.fakes import FAKE_OK, FAKE_SLOW, make_context, make_controller, make_source, press_start

DEST = "oss://bucket-sandbox/FaDi-spike/"
SIZE = (100, 30)


def make_app(tmp_path, code=FAKE_OK, unicode=True):
    controller = make_controller(tmp_path, code=code)
    settings = replace(controller.settings, unicode=unicode)
    controller.settings = settings
    return FaDiApp(make_context(tmp_path, controller=controller, settings=settings)), controller


def run(coro, timeout=40):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


async def wait_for(pilot, predicate, timeout=15.0):
    for _ in range(int(timeout / 0.1)):
        if predicate():
            return True
        await pilot.pause(0.1)
    return False


def test_empty_queue_shows_empty_state(tmp_path):
    app, _ = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MainScreen)
            assert "NO UPLOAD JOBS" in app.screen.last_detail_text
            assert app.screen.last_total_text == "Queue is empty"

    run(scenario())


def test_existing_jobs_are_listed_on_start(tmp_path):
    app, controller = make_app(tmp_path)
    controller.add_job(str(make_source(tmp_path)), DEST)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            table = app.screen.query_one("#queue", DataTable)
            assert table.row_count == 1
            assert "FaDi_test.bin" in app.screen.last_detail_text

    run(scenario())


def test_add_then_preflight_then_upload_ends_verified(tmp_path):
    app, controller = make_app(tmp_path)
    source = make_source(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, AddJobScreen)
            app.screen.query_one("#source", Input).value = str(source)
            app.screen.query_one("#destination", Input).value = DEST
            app.screen.query_one("#add", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, PreflightScreen)
            await press_start(pilot, app)
            assert await wait_for(pilot, lambda: controller.jobs and controller.jobs[0].status is JobStatus.VERIFIED)
            await pilot.pause(0.6)
            table = app.screen.query_one("#queue", DataTable)
            assert str(table.get_row_at(0)[0]) == "[DONE]"
            assert "VERIFIED" in app.screen.last_detail_text

    run(scenario())


def test_add_dialog_shows_error_and_stays_open(tmp_path):
    app, controller = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            app.screen.query_one("#source", Input).value = str(tmp_path / "missing.bin")
            app.screen.query_one("#destination", Input).value = DEST
            app.screen.query_one("#add", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, AddJobScreen)
            assert controller.jobs == []

    run(scenario())


def test_cancelling_preflight_keeps_job_pending(tmp_path):
    app, controller = make_app(tmp_path)
    controller.add_job(str(make_source(tmp_path)), DEST)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, PreflightScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainScreen)
            assert controller.jobs[0].status is JobStatus.PENDING

    run(scenario())


def test_pause_key_pauses_running_upload_and_resume_key_is_offered(tmp_path):
    app, controller = make_app(tmp_path, code=FAKE_SLOW)
    controller.add_job(str(make_source(tmp_path)), DEST)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await press_start(pilot, app)
            job = controller.jobs[0]
            assert await wait_for(pilot, lambda: job.status is JobStatus.UPLOADING and job.percent >= 30)
            await pilot.press("p")
            assert await wait_for(pilot, lambda: job.status is JobStatus.PAUSED)
            await pilot.pause(0.6)
            assert "[RESUME]" in app.screen.last_detail_text
            assert "Paused" in app.screen.last_detail_text

    run(scenario())


def test_quit_while_uploading_pauses_and_saves_state(tmp_path):
    app, controller = make_app(tmp_path, code=FAKE_SLOW)
    controller.add_job(str(make_source(tmp_path)), DEST)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await press_start(pilot, app)
            job = controller.jobs[0]
            assert await wait_for(pilot, lambda: job.status is JobStatus.UPLOADING and job.percent >= 30)
            await pilot.press("q")
            assert await wait_for(pilot, lambda: job.status is JobStatus.PAUSED)

    run(scenario())
    from ossutil_tui.jobs import JobStore

    assert JobStore(controller.settings.state_file).load()[0].status is JobStatus.PAUSED


def test_remove_blocked_for_resumable_job_keeps_it(tmp_path):
    app, controller = make_app(tmp_path)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    job.status = JobStatus.PAUSED
    job.resumable = True

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()
            assert len(controller.jobs) == 1

    run(scenario())


def test_ascii_mode_renders_without_unicode_bars(tmp_path):
    controller = make_controller(tmp_path)
    from dataclasses import replace

    settings = replace(controller.settings, unicode=False)
    controller.settings = settings
    controller.add_job(str(make_source(tmp_path)), DEST)
    app = FaDiApp(make_context(tmp_path, controller=controller, settings=settings))

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert "█" not in app.screen.last_detail_text
            assert "-" in app.screen.last_detail_text

    run(scenario())

def test_removing_a_finished_job_does_not_crash(tmp_path):
    # Regression: removing a job left a stale row id and the next refresh raised JobError.
    app, controller = make_app(tmp_path)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    job.status = JobStatus.VERIFIED
    job.percent = 100.0

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause(1.3)
            assert app.is_running
            assert controller.jobs == []
            assert app.screen.query_one("#queue", DataTable).row_count == 0
            assert "NO UPLOAD JOBS" in app.screen.last_detail_text

    run(scenario())

def test_cursor_selection_survives_periodic_refresh(tmp_path):
    app, controller = make_app(tmp_path)
    controller.add_job(str(make_source(tmp_path, name="FaDi_a.bin")), DEST)
    controller.add_job(str(make_source(tmp_path, name="FaDi_b.bin")), DEST)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause(1.3)
            table = app.screen.query_one("#queue", DataTable)
            assert table.cursor_row == 1
            assert "FaDi_b.bin" in app.screen.last_detail_text

    run(scenario())


def test_highlight_events_do_not_cause_a_refresh_storm(tmp_path):
    app, controller = make_app(tmp_path)
    for name in ("FaDi_a.bin", "FaDi_b.bin", "FaDi_c.bin"):
        controller.add_job(str(make_source(tmp_path, name=name)), DEST)
    calls = {"n": 0}

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            original = app.screen._render_side_panels

            def counting():
                calls["n"] += 1
                original()

            app.screen._render_side_panels = counting
            await pilot.pause(1.6)

    run(scenario())
    assert calls["n"] <= 12, f"refresh storm: {calls['n']} renders in 1.6s"


def test_removing_one_of_several_jobs_keeps_the_others_and_a_valid_selection(tmp_path):
    app, controller = make_app(tmp_path)
    first = controller.add_job(str(make_source(tmp_path, name="FaDi_a.bin")), DEST)
    controller.add_job(str(make_source(tmp_path, name="FaDi_b.bin")), DEST)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause(1.3)
            assert app.is_running
            assert [j.name for j in controller.jobs] == ["FaDi_b.bin"]
            assert first.id not in [j.id for j in controller.jobs]
            assert app.screen.query_one("#queue", DataTable).row_count == 1
            assert "FaDi_b.bin" in app.screen.last_detail_text

    run(scenario())


def test_add_dialog_accepts_a_local_folder(tmp_path):
    app, controller = make_app(tmp_path)
    source = tmp_path / "SESSION_001"
    (source / "CAMERA_01" / "VIDEO").mkdir(parents=True)
    (source / "CAMERA_01" / "VIDEO" / "001.mcap").write_bytes(b"x" * 50)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, AddJobScreen)
            app.screen.query_one("#source", Input).value = str(source)
            app.screen.query_one("#destination", Input).value = DEST
            app.screen.query_one("#add", Button).press()
            await pilot.pause()
            assert controller.jobs[0].is_folder
            assert controller.jobs[0].object_uri == DEST + "SESSION_001/"
            assert controller.jobs[0].folder_files == 1

    run(scenario())
def test_discard_checkpoint_requires_confirmation_and_resets_job(tmp_path):
    app, controller = make_app(tmp_path)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    job.status = JobStatus.PAUSED
    job.resumable = True

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            assert "DISCARD CHECKPOINT" in str(app.screen)
            await pilot.press("escape")
            await pilot.pause()
            assert job.status is JobStatus.PAUSED
            await pilot.press("d")
            await pilot.pause()
            app.screen.query_one("#confirm", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, MainScreen)
            assert job.status is JobStatus.PENDING and not job.resumable

    run(scenario())

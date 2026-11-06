import asyncio

from textual.widgets import Button, DataTable, Input

from ossutil_tui.app import AddJobScreen, BrowserScreen, FaDiApp, MainScreen, PreflightScreen
from ossutil_tui.jobs import JobStatus
from tests.fakes import BIG_STAT, TREE, make_context, make_controller, make_source, make_tree_exec, press_start

SIZE = (100, 30)
ROOT = "oss://bucket-sandbox/"
TYPO_DEST = "oss://bucket-sandbox/data/team-alpah-black/incoming/"


def run(coro, timeout=40):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


async def wait_for(pilot, predicate, timeout=10.0):
    for _ in range(int(timeout / 0.1)):
        if predicate():
            return True
        await pilot.pause(0.1)
    return False


def make_app(tmp_path, tree=None, exec_fn=None):
    controller = make_controller(
        tmp_path, exec_fn=exec_fn or make_tree_exec(tree), stat_text=BIG_STAT
    )
    controller.default_root = ROOT
    return FaDiApp(make_context(tmp_path, controller=controller)), controller


async def open_browser(app, pilot):
    await pilot.pause()
    await pilot.press("b")
    assert await wait_for(pilot, lambda: isinstance(app.screen, BrowserScreen))
    assert await wait_for(pilot, lambda: len(app.screen.entries) > 0)


def names(screen):
    return [entry.name for entry in screen.entries]


def test_browser_opens_at_the_bucket_root_and_lists_folders_and_files(tmp_path):
    app, _ = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await open_browser(app, pilot)
            screen = app.screen
            assert screen.current_uri == ROOT
            assert names(screen) == ["data", "readme.txt"]
            table = screen.query_one("#browser-table", DataTable)
            assert table.row_count == 2
            assert str(table.get_row_at(0)[0]) == "[DIR]" and str(table.get_row_at(1)[0]) == "[FILE]"
            assert "2 item(s)" in screen.last_status_text

    run(scenario())


def test_enter_opens_a_folder_and_backspace_goes_up(tmp_path):
    app, _ = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await open_browser(app, pilot)
            await pilot.press("enter")
            assert await wait_for(pilot, lambda: app.screen.current_uri == ROOT + "data/" and len(app.screen.entries) == 4)
            assert names(app.screen)[0] == "team-alpha-black"
            await pilot.press("backspace")
            assert await wait_for(pilot, lambda: app.screen.current_uri == ROOT and names(app.screen) == ["data", "readme.txt"])
            await pilot.press("backspace")
            await pilot.pause(0.3)
            assert "bucket root" in app.screen.last_status_text

    run(scenario())


def test_paging_loads_every_entry_once(tmp_path):
    tree = {ROOT: [f"d{i:03d}/" for i in range(450)]}
    app, _ = make_app(tmp_path, tree=tree)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await open_browser(app, pilot)
            screen = app.screen
            assert len(screen.entries) == 199 and "press M" in screen.last_status_text
            await pilot.press("m")
            assert await wait_for(pilot, lambda: len(screen.entries) == 399)
            await pilot.press("m")
            assert await wait_for(pilot, lambda: len(screen.entries) == 450)
            assert len(set(names(screen))) == 450
            assert "press M" not in screen.last_status_text
            await pilot.press("m")
            await pilot.pause(0.2)
            assert "No more items" in screen.last_status_text

    run(scenario())


def test_info_shows_file_details_and_folder_totals(tmp_path):
    app, _ = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await open_browser(app, pilot)
            await pilot.press("i")  # row 0 is the data folder
            assert await wait_for(pilot, lambda: "60,902" in app.screen.last_info_text)
            assert "13.21 TB" in app.screen.last_info_text
            await pilot.press("down")
            await pilot.press("i")  # row 1 is readme.txt
            assert await wait_for(pilot, lambda: "1.00 GB" in app.screen.last_info_text)
            assert "3534425600523290380" in app.screen.last_info_text

    run(scenario())


def test_errors_are_shown_in_the_status_line(tmp_path):
    async def denied(argv, env):
        return 1, "", "Http Status Code: 403. Error Code: AccessDenied."

    app, _ = make_app(tmp_path, exec_fn=denied)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("b")
            assert await wait_for(pilot, lambda: isinstance(app.screen, BrowserScreen))
            assert await wait_for(pilot, lambda: "Access denied" in app.screen.last_status_text)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainScreen)

    run(scenario())


def test_browse_only_mode_does_not_select_a_folder(tmp_path):
    app, _ = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await open_browser(app, pilot)
            await pilot.press("s")
            await pilot.pause(0.2)
            assert isinstance(app.screen, BrowserScreen)
            assert "Add dialog" in app.screen.last_status_text

    run(scenario())


def test_add_dialog_browse_button_fills_the_destination(tmp_path):
    app, _ = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, AddJobScreen)
            app.screen.query_one("#browse", Button).press()
            assert await wait_for(pilot, lambda: isinstance(app.screen, BrowserScreen) and len(app.screen.entries) > 0)
            assert app.screen.current_uri == ROOT
            await pilot.press("enter")
            assert await wait_for(pilot, lambda: app.screen.current_uri == ROOT + "data/" and len(app.screen.entries) == 4)
            await pilot.press("s")
            assert await wait_for(pilot, lambda: isinstance(app.screen, AddJobScreen))
            assert app.screen.query_one("#destination", Input).value == ROOT + "data/"

    run(scenario())


def test_browse_button_starts_at_the_typed_destination_folder(tmp_path):
    app, _ = make_app(tmp_path)

    async def scenario():
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            app.screen.query_one("#destination", Input).value = ROOT + "data/team-alpha-black/incoming/file.bin"
            app.screen.query_one("#browse", Button).press()
            assert await wait_for(pilot, lambda: isinstance(app.screen, BrowserScreen))
            assert app.screen.current_uri == ROOT + "data/team-alpha-black/incoming/"
            assert await wait_for(pilot, lambda: len(app.screen.entries) == 3)

    run(scenario())


def add_job_and_open_preflight(app, controller, tmp_path, destination):
    controller.add_job(str(make_source(tmp_path)), destination)

    async def scenario(check):
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, PreflightScreen)
            await check(pilot)

    return scenario


def test_preflight_warns_about_a_mistyped_destination_and_waits_for_the_check(tmp_path):
    app, controller = make_app(tmp_path)
    scenario = add_job_and_open_preflight(app, controller, tmp_path, TYPO_DEST)

    async def check(pilot):
        assert await wait_for(pilot, lambda: "typo" in app.screen.last_text)
        assert "does not exist yet" in app.screen.last_text
        assert "team-alpha-black" in app.screen.last_text
        assert app.screen.query_one("#start", Button).disabled is False

    run(scenario(check))


def test_preflight_is_quiet_for_an_existing_destination(tmp_path):
    app, controller = make_app(tmp_path)
    scenario = add_job_and_open_preflight(app, controller, tmp_path, ROOT + "data/team-alpha-black/incoming/")

    async def check(pilot):
        assert await wait_for(pilot, lambda: not app.screen.query_one("#start", Button).disabled)
        assert "does not exist yet" not in app.screen.last_text
        assert "Checking destination folder" not in app.screen.last_text

    run(scenario(check))


def test_start_stays_disabled_until_the_destination_check_finishes(tmp_path):
    gate = {}

    async def slow_exec(argv, env):
        if "event" not in gate:
            gate["event"] = asyncio.Event()
        await gate["event"].wait()
        return 0, f"{argv[2]}\nObject and Directory Number is: 1\n", ""

    app, controller = make_app(tmp_path, exec_fn=slow_exec)
    scenario = add_job_and_open_preflight(app, controller, tmp_path, ROOT + "data/")

    async def check(pilot):
        await pilot.pause(0.3)
        assert app.screen.query_one("#start", Button).disabled is True
        assert "Checking destination folder" in app.screen.last_text
        gate["event"].set()
        assert await wait_for(pilot, lambda: not app.screen.query_one("#start", Button).disabled)

    run(scenario(check))


def test_a_failing_destination_check_is_a_warning_not_a_blocker(tmp_path):
    async def broken(argv, env):
        return 1, "", "Error Code: AccessDenied"

    app, controller = make_app(tmp_path, exec_fn=broken)
    scenario = add_job_and_open_preflight(app, controller, tmp_path, ROOT + "data/")

    async def check(pilot):
        assert await wait_for(pilot, lambda: "Could not check the destination folder" in app.screen.last_text)
        assert app.screen.query_one("#start", Button).disabled is False

    run(scenario(check))


def test_cancelling_preflight_before_the_check_ends_does_not_crash(tmp_path):
    gate = {}

    async def slow_exec(argv, env):
        gate.setdefault("event", asyncio.Event())
        await gate["event"].wait()
        return 0, f"{argv[2]}\nObject and Directory Number is: 1\n", ""

    app, controller = make_app(tmp_path, exec_fn=slow_exec)
    scenario = add_job_and_open_preflight(app, controller, tmp_path, ROOT + "data/")

    async def check(pilot):
        await pilot.press("escape")
        await pilot.pause(0.2)
        gate["event"].set()
        await pilot.pause(0.5)
        assert app.is_running and isinstance(app.screen, MainScreen)
        assert controller.jobs[0].status is JobStatus.PENDING

    run(scenario(check))
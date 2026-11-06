"""Textual UI for M1: one Upload Manager screen plus add and pre-flight dialogs."""
from __future__ import annotations

from dataclasses import replace

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Input, Static

from . import __version__
from .browser import Entry, EntryKind, folder_of, normalize_dir_uri, parent_uri
from .context import AppContext, LoginDefaults
from .controller import JobController, JobError, PreflightInfo
from .jobs import Job, JobStatus
from .format import format_size, format_speed, truncate_middle
from .ossutil import normalize_region
from .profile import validate_login
from .settings import Settings
from .views import (
    EMPTY_TEXT, render_detail, render_folder_info, render_object_info, render_preflight, render_row, render_total,
)

APP_CSS = """
Screen { background: #0D1117; color: #E6EDF3; }
#header { height: 3; background: #151B23; padding: 0 2; border-bottom: solid #303843; }
#tabs { height: 1; padding: 0 2; color: #8B98A7; background: #10161D; }
#layout { height: 1fr; }
#sidebar { width: 18; height: 1fr; padding: 1; background: #121820; border-right: solid #2A323D; }
#sidebar-brand { height: 3; color: #D4F53C; text-style: bold; }
#sidebar-kicker { height: 1; color: #748191; margin-top: 1; }
#nav-active { height: 3; padding: 1; color: #D4F53C; background: #242D24; border-left: tall #D4F53C; }
#nav-item, .nav-item { height: 2; padding: 0 1; color: #AAB4C0; }
#sidebar-rule { height: 1; color: #3A424D; margin-top: 1; }
#connection-label { height: 1; color: #748191; margin-top: 1; }
#sidebar-connection { height: auto; color: #D4F53C; }
#sidebar-meta { height: 1fr; color: #748191; content-align: left bottom; }
#workspace { width: 1fr; height: 1fr; padding: 1; }
#workspace-title { height: 2; color: #E6EDF3; text-style: bold; }
#metrics { height: 5; }
.metric { width: 1fr; height: 5; padding: 0 1; border: solid #303843; background: #151B23; }
#body { height: 1fr; min-height: 8; }
#queue { width: 56%; height: 1fr; border: solid #303843; border-title-color: #D4F53C; background: #151B23; }
#detail { width: 1fr; height: 1fr; border: solid #303843; border-title-color: #D4F53C; background: #151B23; padding: 0 1; }
#total { height: 6; border: solid #303843; border-title-color: #D4F53C; background: #151B23; padding: 0 1; }
DataTable > .datatable--header { background: #1C242E; color: #AAB4C0; }
DataTable > .datatable--cursor { background: #293326; color: #F0F4E8; }
Footer { background: #151B23; color: #AAB4C0; }
.ascii #queue, .ascii #detail, .ascii #total, .ascii .metric { border: ascii #303843; }
AddJobScreen, PreflightScreen, LogoutScreen, LoginScreen, BrowserScreen { align: center middle; }
#add-dialog, #preflight-dialog, #logout-dialog { width: 90%; max-width: 96; height: auto; border: double #D4F53C; background: #19232E; padding: 1 2; }
#login-dialog { width: 90%; max-width: 84; height: auto; max-height: 100%; border: double #D4F53C; background: #19232E; padding: 1 2; }
#login-status { height: auto; min-height: 1; margin-top: 1; }
#dest-row { height: 3; }
#dest-row Input { width: 1fr; }
#dest-row Button { width: 14; margin-left: 1; }
#browser-dialog { width: 96%; height: 90%; border: double #D4F53C; background: #19232E; padding: 0 1; }
#browser-crumb { height: 1; color: #D4F53C; }
#browser-table { height: 1fr; background: #151B23; }
#browser-info { height: 7; border: solid #303843; background: #151B23; padding: 0 1; }
#browser-status { height: 1; color: #9AA9B8; }
#add-error { color: #F06A72; height: auto; }
#dialog-buttons { height: 3; margin-top: 1; }
"""


class AddJobScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, controller: JobController) -> None:
        super().__init__()
        self._controller = controller

    def compose(self) -> ComposeResult:
        with Vertical(id="add-dialog"):
            yield Static(Text("ADD UPLOAD", style="bold #35C6D8"))
            yield Static("Source file (local path)")
            yield Input(placeholder=r"C:\Data\file.tar", id="source")
            yield Static("Destination (end with / to keep the file name)")
            with Horizontal(id="dest-row"):
                yield Input(placeholder="oss://bucket/prefix/", id="destination")
                yield Button("Browse...", id="browse")
            yield Static("", id="add-error")
            with Horizontal(id="dialog-buttons"):
                yield Button("Add", id="add", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add":
            self._submit()
        elif event.button.id == "browse":
            self._browse()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _browse(self) -> None:
        typed = self.query_one("#destination", Input).value.strip()
        start = ""
        if typed.startswith("oss://"):
            try:
                start = folder_of(typed)
            except ValueError:
                start = ""
        start = start or self._controller.default_root
        if not start:
            self.query_one("#add-error", Static).update(Text("No bucket is configured for browsing."))
            return
        self.app.push_screen(BrowserScreen(self._controller, start, choose=True), self._after_browse)

    def _after_browse(self, uri: str | None) -> None:
        if uri:
            self.query_one("#destination", Input).value = uri

    def _submit(self) -> None:
        source = self.query_one("#source", Input).value
        destination = self.query_one("#destination", Input).value
        try:
            job = self._controller.add_job(source, destination)
        except JobError as exc:
            self.query_one("#add-error", Static).update(Text(str(exc)))
            return
        self.dismiss(job.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PreflightScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, info: PreflightInfo, unicode: bool) -> None:
        super().__init__()
        self._info = info
        self._unicode = unicode
        self._finished = False
        self._shown = False
        self._pending_warnings: list[str] | None = None
        self.last_text = ""

    def on_mount(self) -> None:
        self._shown = True
        if self._pending_warnings is not None:
            self._apply(self._pending_warnings)

    def _text(self, note: str = "", extra: list[str] | None = None) -> Text:
        info = replace(self._info, warnings=[*self._info.warnings, *(extra or [])])
        text = render_preflight(info, unicode=self._unicode, note=note)
        self.last_text = text.plain
        return text

    def compose(self) -> ComposeResult:
        with Vertical(id="preflight-dialog"):
            yield Static(self._text("Checking destination folder..."), id="preflight-text")
            with Horizontal(id="dialog-buttons"):
                yield Button("Start upload", id="start", variant="primary", disabled=True)
                yield Button("Cancel", id="cancel")

    def set_check_result(self, warnings: list[str]) -> None:
        """Called when the destination check finishes; only then can the upload be started."""
        if self._finished:
            return
        if not self._shown:
            self._pending_warnings = warnings  # the check beat the dialog to the screen; apply on mount
            return
        self._apply(warnings)

    def _apply(self, warnings: list[str]) -> None:
        self.query_one("#preflight-text", Static).update(self._text(extra=warnings))
        start = self.query_one("#start", Button)
        start.disabled = False
        start.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self._finished = True
        self.dismiss(event.button.id == "start")

    def action_cancel(self) -> None:
        self._finished = True
        self.dismiss(False)


class BrowserScreen(ModalScreen[str | None]):
    """Read-only bucket browser. In choose mode, S returns the folder you are looking at."""

    PAGE_SIZE = 200
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("backspace", "up", "Up"),
        Binding("m", "more", "More"),
        Binding("r", "reload", "Refresh"),
        Binding("i", "info", "Info"),
        Binding("s", "choose", "Select folder"),
    ]

    def __init__(self, controller: JobController, start_uri: str, *, choose: bool) -> None:
        super().__init__()
        self._controller = controller
        self._choose = choose
        self.current_uri = normalize_dir_uri(start_uri)
        self.entries: list[Entry] = []
        self._last_key: str | None = None
        self._has_more = False
        self._busy = False
        self._generation = 0
        self.last_status_text = ""
        self.last_info_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="browser-dialog"):
            yield Static(id="browser-crumb")
            yield DataTable(id="browser-table")
            yield Static(id="browser-info")
            yield Static(id="browser-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#browser-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("", "NAME")
        self.query_one("#browser-info", Static).border_title = "DETAILS"
        table.focus()
        self._start_load(append=False)

    # --- small helpers -------------------------------------------------
    def _set_status(self, message: str, error: bool = False) -> None:
        self.last_status_text = message
        self.query_one("#browser-status", Static).update(Text(message, style="#F06A72" if error else "#9AA9B8"))

    def _set_info(self, text: Text) -> None:
        self.last_info_text = text.plain
        self.query_one("#browser-info", Static).update(text)

    def _entry_at(self, index: int) -> Entry | None:
        return self.entries[index] if 0 <= index < len(self.entries) else None

    def _selected(self) -> Entry | None:
        return self._entry_at(self.query_one("#browser-table", DataTable).cursor_row)

    # --- loading -------------------------------------------------------
    def _start_load(self, append: bool) -> None:
        if append and self._busy:
            return
        self._generation += 1
        self.run_worker(self._load(append, self._generation), exit_on_error=False)

    async def _load(self, append: bool, generation: int) -> None:
        self._busy = True
        self._set_status("Loading...")
        try:
            page = await self._controller.list_directory(
                self.current_uri,
                start_after_key=self._last_key if append else None,
                limit=self.PAGE_SIZE,
            )
        except JobError as exc:
            if generation == self._generation:
                self._busy = False
                self._set_status(str(exc), error=True)
                if not append:
                    self.entries = []
                    self._render_entries(keep_cursor=False)
            return
        if generation != self._generation:
            return  # the user moved on; drop this stale page
        self._busy = False
        self.entries = [*self.entries, *page.entries] if append else list(page.entries)
        self._last_key = page.last_key
        self._has_more = page.has_more
        self._render_entries(keep_cursor=append)
        if not self.entries and not append:
            empty = "This folder does not exist or is empty." if page.total_returned == 0 else "This folder is empty."
            self._set_status(empty)
        else:
            more = " - more available, press M" if self._has_more else ""
            self._set_status(f"{len(self.entries)} item(s){more}")

    def _render_entries(self, keep_cursor: bool) -> None:
        table = self.query_one("#browser-table", DataTable)
        previous = table.cursor_row if keep_cursor else 0
        table.clear()
        for entry in self.entries:
            is_dir = entry.kind is EntryKind.DIR
            badge = Text("[DIR]", style="#35C6D8") if is_dir else Text("[FILE]", style="#9AA9B8")
            label = entry.name + ("/" if is_dir else "")
            table.add_row(badge, Text(truncate_middle(label, 70), style="#E6EDF3"))
        if self.entries:
            table.move_cursor(row=min(previous, len(self.entries) - 1))
        crumb = Text(truncate_middle(self.current_uri, 92), style="bold #35C6D8")
        self.query_one("#browser-crumb", Static).update(crumb)

    def _navigate(self, uri: str) -> None:
        self.current_uri = normalize_dir_uri(uri)
        self.entries = []
        self._last_key = None
        self._has_more = False
        self._set_info(Text(""))
        self._start_load(append=False)

    # --- events and actions --------------------------------------------
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        entry = self._entry_at(event.cursor_row)
        if entry is None:
            return
        if entry.kind is EntryKind.DIR:
            self._navigate(entry.uri)
        else:
            self._show_info(entry)

    def _show_info(self, entry: Entry) -> None:
        self.run_worker(self._info(entry), exit_on_error=False)

    async def _info(self, entry: Entry) -> None:
        self._set_info(Text("Loading details...", style="#9AA9B8"))
        try:
            if entry.kind is EntryKind.FILE:
                self._set_info(render_object_info(entry.name, await self._controller.stat_object(entry.uri)))
            else:
                objects, size = await self._controller.folder_usage(entry.uri)
                self._set_info(render_folder_info(entry.name, objects, size))
        except JobError as exc:
            self._set_info(Text(str(exc), style="#F06A72"))

    def action_info(self) -> None:
        entry = self._selected()
        if entry is not None:
            self._show_info(entry)

    def action_up(self) -> None:
        parent = parent_uri(self.current_uri)
        root = self._controller.default_root
        if parent is None or (root and not parent.startswith(normalize_dir_uri(root))):
            self._set_status("Already at the permitted prefix root.")
        else:
            self._navigate(parent)

    def action_more(self) -> None:
        if self._has_more:
            self._start_load(append=True)
        else:
            self._set_status("No more items.")

    def action_reload(self) -> None:
        self._navigate(self.current_uri)

    def action_choose(self) -> None:
        if self._choose:
            self.dismiss(self.current_uri)
        else:
            self._set_status("Open the browser from the Add dialog to select a destination.")

    def action_close(self) -> None:
        self.dismiss(None)

class LoginScreen(Screen):
    def __init__(self, defaults: LoginDefaults, notice: str = "") -> None:
        super().__init__()
        self._defaults = defaults
        self._notice = notice
        self._busy = False
        self.last_status_text = notice

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="login-dialog"):
            yield Static(Text("OSS CONTROL CENTER  -  LOGIN", style="bold #35C6D8"))
            yield Input(value=self._defaults.access_key_id, placeholder="LTAI...", id="login-key")
            yield Input(placeholder="AccessKey Secret (stored encrypted)", password=True, id="login-secret")
            yield Input(value=self._defaults.region, placeholder="ap-southeast-5", id="login-region")
            yield Input(
                value=self._defaults.bucket, placeholder="bucket-name or oss://bucket/allowed-prefix/", id="login-bucket"
            )
            yield Static(Text(self._notice, style="#F2B84B"), id="login-status")
            with Horizontal(id="dialog-buttons"):
                yield Button("Login", id="login", variant="primary")
                yield Button("Quit", id="quit")

    def on_mount(self) -> None:
        titles = {
            "login-key": "AccessKey ID",
            "login-secret": "AccessKey Secret",
            "login-region": "Region",
            "login-bucket": "Bucket / OSS URI (connection test)",
        }
        for widget_id, title in titles.items():
            self.query_one(f"#{widget_id}", Input).border_title = title
        target = "login-secret" if self._defaults.access_key_id else "login-key"
        self.query_one(f"#{target}", Input).focus()

    def _set_status(self, message: str, error: bool = False) -> None:
        self.last_status_text = message
        style = "#F06A72" if error else "#9AA9B8"
        self.query_one("#login-status", Static).update(Text(message, style=style))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit":
            self.app.exit()
        else:
            self._start_login()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._start_login()

    def _start_login(self) -> None:
        if not self._busy:
            self.run_worker(self._login(), exit_on_error=False)

    async def _login(self) -> None:
        self._busy = True
        self.query_one("#login", Button).disabled = True
        success = False
        try:
            check = validate_login(
                access_key_id=self.query_one("#login-key", Input).value,
                secret=self.query_one("#login-secret", Input).value,
                region=self.query_one("#login-region", Input).value,
                bucket=self.query_one("#login-bucket", Input).value,
                exe=self._defaults.exe,
            )
            if check.errors:
                self._set_status("\n".join(check.errors), error=True)
                return
            profile = check.profile
            self._set_status("Testing connection...")
            context = self.app.context
            result = await context.check_fn(
                exe=profile.exe,
                config_file=context.ensure_empty_config(),
                region=profile.region,
                bucket=profile.root_uri,
                access_key_id=profile.access_key_id,
                secret=check.secret,
            )
            if not result.ok:
                self._set_status(result.message, error=True)
                return
            context.save_login(profile, check.secret)
            self.query_one("#login-secret", Input).value = ""
            success = True
        except Exception as exc:  # keep the login screen alive and show the reason
            self._set_status(f"Login failed: {exc}", error=True)
        finally:
            self._busy = False
            if not success:
                self.query_one("#login", Button).disabled = False
        if success:
            self.app.switch_screen(MainScreen())


class LogoutScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, resumable: int) -> None:
        super().__init__()
        self._resumable = resumable

    def compose(self) -> ComposeResult:
        body = "This removes the saved AccessKey and profile from this PC.\nYour upload queue is kept."
        if self._resumable:
            body += (
                f"\n\n[!] {self._resumable} job(s) can be resumed, but only after you log in again "
                "to the same account and bucket."
            )
        with Vertical(id="logout-dialog"):
            yield Static(Text("LOG OUT", style="bold #F2B84B"))
            yield Static(Text(body))
            with Horizontal(id="dialog-buttons"):
                yield Button("Log out", id="confirm", variant="error")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)

class MainScreen(Screen):
    BINDINGS = [
        Binding("a", "add_job", "Add"),
        Binding("b", "browse", "Browse"),
        Binding("s", "start_job", "Start"),
        Binding("p", "pause_job", "Pause"),
        Binding("r", "resume_job", "Resume"),
        Binding("v", "verify_job", "Verify"),
        Binding("x", "remove_job", "Remove"),
        Binding("l", "logout", "Logout"),
        Binding("q", "quit_app", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._row_ids: list[str] = []
        self.last_detail_text = ""
        self.last_total_text = ""
        self.last_header_text = ""

    # --- layout --------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="header")
        yield Static(id="tabs")
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Static("FaDi  /  OSS", id="sidebar-brand")
                yield Static("WORKSPACE", id="sidebar-kicker")
                yield Static("▸  OVERVIEW", id="nav-active")
                yield Static("   UPLOAD QUEUE", classes="nav-item")
                yield Static("   BUCKET BROWSER", classes="nav-item")
                yield Static("────────────────", id="sidebar-rule")
                yield Static("CONNECTION", id="connection-label")
                yield Static(id="sidebar-connection")
                yield Static(f"v{__version__}", id="sidebar-meta")
            with Vertical(id="workspace"):
                yield Static("OPERATIONS OVERVIEW", id="workspace-title")
                with Horizontal(id="metrics"):
                    yield Static(id="metric-jobs", classes="metric")
                    yield Static(id="metric-transfer", classes="metric")
                    yield Static(id="metric-speed", classes="metric")
                    yield Static(id="metric-verified", classes="metric")
                with Horizontal(id="body"):
                    yield DataTable(id="queue")
                    yield Static(id="detail")
                yield Static(id="total")
        yield Footer()

    def on_mount(self) -> None:
        self.set_class(not self.app.settings.unicode, "ascii")
        table = self.query_one("#queue", DataTable)
        table.cursor_type = "row"
        table.add_columns("ST", "NAME", "SIZE", "%")
        table.border_title = "UPLOAD QUEUE"
        self.query_one("#detail", Static).border_title = "SELECTED JOB"
        self.query_one("#total", Static).border_title = "TRANSFER SUMMARY"
        self.app.controller.set_on_change(self.refresh_view)
        self.refresh_view()
        self.set_interval(0.5, self.refresh_view)

    # --- rendering -----------------------------------------------------
    def _selected_job(self) -> Job | None:
        """Look the selected row's job up safely; it may have been removed since the last render."""
        if not self._row_ids:
            return None
        index = self.query_one("#queue", DataTable).cursor_row
        if not 0 <= index < len(self._row_ids):
            return None
        wanted = self._row_ids[index]
        return next((job for job in self.app.controller.jobs if job.id == wanted), None)

    def refresh_view(self) -> None:
        # The periodic refresh timer can fire once more while the login screen is
        # replacing this screen during logout. The session context is intentionally
        # cleared at that point, so do not dereference settings/controller here.
        if not self.app.context.logged_in:
            return
        settings: Settings = self.app.settings
        controller: JobController = self.app.controller
        header = Text()
        header.append("  FaDi  /  OSS OPERATIONS CONSOLE", style="bold #D4F53C")
        header.append(f"    v{__version__}   ", style="#748191")
        header.append(f"REGION  {normalize_region(settings.region)}", style="#AAB4C0")
        profile = self.app.context.profile
        if profile is not None:
            header.append(f"     ● CONNECTED   {profile.bucket}", style="bold #D4F53C")
            header.append(f"   KEY {profile.masked_key()}", style="#8793A1")
        self.last_header_text = header.plain
        self.query_one("#header", Static).update(header)
        self.query_one("#tabs", Static).update(Text("OPERATIONS   /   UPLOAD QUEUE   /   BUCKET BROWSER", style="#8B98A7"))
        self.query_one("#workspace-title", Static).update(Text("OPERATIONS OVERVIEW", style="bold #E6EDF3"))
        if profile is not None:
            self.query_one("#sidebar-connection", Static).update(
                Text(f"● {profile.bucket}\n{profile.masked_key()}", style="#D4F53C")
            )
        else:
            self.query_one("#sidebar-connection", Static).update(Text("● OFFLINE", style="#F2B84B"))

        jobs = controller.jobs
        active = next((job for job in jobs if job.status is JobStatus.UPLOADING), None)
        transferred = sum(job.done_bytes for job in jobs)
        verified = sum(1 for job in jobs if job.status is JobStatus.VERIFIED)
        metrics = (
            ("#metric-jobs", "QUEUE ITEMS", f"{len(jobs):02d}", "#D4F53C"),
            ("#metric-transfer", "TRANSFERRED", format_size(transferred), "#E6EDF3"),
            ("#metric-speed", "LIVE SPEED", format_speed(controller.live.get(active.id).speed if active and active.id in controller.live else None), "#D4F53C"),
            ("#metric-verified", "VERIFIED", f"{verified} / {len(jobs)}", "#43C98B"),
        )
        for widget_id, label, value, color in metrics:
            card = Text()
            card.append(label + "\n", style="#8793A1")
            card.append(value, style=f"bold {color}")
            self.query_one(widget_id, Static).update(card)

        table = self.query_one("#queue", DataTable)
        ids = [job.id for job in controller.jobs]
        if ids == self._row_ids:
            # Same rows: update cells in place so the cursor and highlight events are left alone.
            for row, job in enumerate(controller.jobs):
                for column, cell in enumerate(render_row(job, unicode=settings.unicode)):
                    table.update_cell_at(Coordinate(row, column), cell)
        else:
            old_index = table.cursor_row
            old_selected = self._row_ids[old_index] if 0 <= old_index < len(self._row_ids) else None
            table.clear()
            for job in controller.jobs:
                table.add_row(*render_row(job, unicode=settings.unicode))
            self._row_ids = ids
            if ids:
                target = ids.index(old_selected) if old_selected in ids else min(old_index, len(ids) - 1)
                table.move_cursor(row=max(0, target))
        self._render_side_panels()

    def _render_side_panels(self) -> None:
        """Detail and total panels only; safe to call from highlight events (no table rebuild)."""
        settings: Settings = self.app.settings
        controller: JobController = self.app.controller
        current = self._selected_job()
        detail = self.query_one("#detail", Static)
        if current is None:
            self.last_detail_text = EMPTY_TEXT
            detail.update(Text(EMPTY_TEXT, style="#9AA9B8"))
        else:
            text = render_detail(
                current,
                controller.live.get(current.id),
                part_size=settings.part_size,
                now=controller.now(),
                unicode=settings.unicode,
            )
            self.last_detail_text = text.plain
            detail.update(text)
        total = render_total(controller.jobs, controller.live, unicode=settings.unicode)
        self.last_total_text = total.plain
        self.query_one("#total", Static).update(total)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._render_side_panels()
    # --- helpers -------------------------------------------------------
    def _toast(self, message: str, severity: str = "information") -> None:
        self.app.notify(escape(message), severity=severity)

    def _launch(self, coro) -> None:
        self.run_worker(self._guarded(coro), exit_on_error=False)

    async def _guarded(self, coro) -> None:
        try:
            await coro
        except JobError as exc:
            self._toast(str(exc), "error")
        except Exception as exc:  # last line of defence so the TUI never dies mid-upload
            self._toast(f"Unexpected error: {exc}", "error")

    def _open_preflight(self, job_id: str) -> None:
        try:
            info = self.app.controller.preflight(job_id)
        except JobError as exc:
            self._toast(str(exc), "error")
            return
        screen = PreflightScreen(info, self.app.settings.unicode)
        self.app.push_screen(screen, lambda ok: self._after_preflight(job_id, bool(ok)))
        self._launch(self._check_destination(job_id, screen))

    async def _check_destination(self, job_id: str, screen: PreflightScreen) -> None:
        controller = self.app.controller
        try:
            warnings = await controller.destination_warnings(controller.get(job_id).destination)
        except Exception as exc:  # the check is advisory; never leave the dialog stuck
            warnings = [f"Could not check the destination folder: {exc}"]
        screen.set_check_result(warnings)

    def _after_preflight(self, job_id: str, confirmed: bool) -> None:
        if confirmed:
            self._launch(self.app.controller.run(job_id))
        else:
            self._toast("Job stays PENDING. Select it and press S to start.")

    def _after_add(self, job_id: str | None) -> None:
        if job_id:
            self._open_preflight(job_id)

    # --- actions -------------------------------------------------------
    def action_add_job(self) -> None:
        self.app.push_screen(AddJobScreen(self.app.controller), self._after_add)

    def action_start_job(self) -> None:
        job = self._selected_job()
        if job is None or job.status is not JobStatus.PENDING:
            self._toast("Select a PENDING job to start.", "warning")
            return
        self._open_preflight(job.id)

    def action_resume_job(self) -> None:
        job = self._selected_job()
        if job is None or job.status not in (JobStatus.PAUSED, JobStatus.FAILED):
            self._toast("Select a PAUSED or FAILED job to resume.", "warning")
            return
        self._launch(self.app.controller.run(job.id))

    def action_pause_job(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        try:
            self.app.controller.pause(job.id)
        except JobError as exc:
            self._toast(str(exc), "warning")

    def action_verify_job(self) -> None:
        job = self._selected_job()
        if job is None or job.status not in (JobStatus.UPLOADED, JobStatus.VERIFY_FAILED):
            self._toast("Verify works on UPLOADED or VERIFY FAILED jobs.", "warning")
            return
        self._launch(self.app.controller.verify(job.id))

    def action_remove_job(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        try:
            self.app.controller.remove(job.id)
        except JobError as exc:
            self._toast(str(exc), "warning")

    def action_browse(self) -> None:
        root = self.app.controller.default_root
        if not root:
            self._toast("No bucket is configured for browsing.", "warning")
            return
        self.app.push_screen(BrowserScreen(self.app.controller, root, choose=False))

    def action_logout(self) -> None:
        controller = self.app.controller
        if controller.is_busy():
            self._toast("An upload is running. Pause it first, then log out.", "warning")
            return
        resumable = sum(
            1 for job in controller.jobs if job.resumable and job.status in (JobStatus.PAUSED, JobStatus.FAILED)
        )
        self.app.push_screen(LogoutScreen(resumable), self._after_logout)

    def _after_logout(self, confirmed: bool | None) -> None:
        if not confirmed:
            return
        try:
            self.app.context.logout()
        except JobError as exc:
            self._toast(str(exc), "warning")
            return
        self.app.switch_screen(
            LoginScreen(self.app.context.login_defaults(), "Logged out. Your upload queue was kept.")
        )

    async def action_quit_app(self) -> None:
        if self.app.controller.is_busy():
            self._toast("Pausing the running upload before exit...")
            await self.app.controller.shutdown()
        self.app.exit()


class FaDiApp(App):
    CSS = APP_CSS
    TITLE = "OSS Control Center"

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context

    @property
    def controller(self) -> JobController:
        return self.context.controller

    @property
    def settings(self) -> Settings:
        return self.context.settings

    def on_mount(self) -> None:
        if self.context.logged_in:
            self.push_screen(MainScreen())
        else:
            self.push_screen(LoginScreen(self.context.login_defaults(), self.context.session_notice))
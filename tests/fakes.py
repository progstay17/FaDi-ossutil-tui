"""Shared fakes for controller and app tests: a fake ossutil process and a fake stat."""
import sys

from ossutil_tui.controller import JobController
from ossutil_tui.jobs import JobStore
from ossutil_tui.settings import Settings

FAKE_OK = r"""
import sys, time
for p in ("10.000", "40.000", "70.000", "100.000"):
    sys.stdout.write("Total 1 objects,1 KiB, Upload... done:(0 objects), %s%%, avg 20.000 MiB/s\r" % p)
    sys.stdout.flush()
    time.sleep(0.05)
sys.stdout.write("\nSuccess: Total 1 file, size 1000 B, Upload done:(1 objects, 1000 B)\n")
"""

FAKE_FAIL = r"""
import sys
sys.stdout.write("Total 1 objects,1 KiB, Upload... done:(0 objects), 10.000%, avg 1.000 MiB/s\r")
sys.stderr.write("Error: simulated failure\n")
sys.exit(3)
"""

FAKE_SLOW = r"""
import sys, time
sys.stdout.write("Total 1 objects,1 KiB, Upload... done:(0 objects), 30.000%, avg 20.000 MiB/s\r")
sys.stdout.flush()
time.sleep(30)
"""

STAT_OK = "Content-Length              : 1000\nX-Oss-Hash-Crc64ecma        : 123456\n"
STAT_WRONG_SIZE = "Content-Length              : 999\nX-Oss-Hash-Crc64ecma        : 123456\n"


def make_settings(tmp_path, **overrides):
    base = dict(
        exe="ossutil",
        config_file="cfg",
        region="oss-ap-southeast-5",
        checkpoint_dir=str(tmp_path / "ck"),
        state_file=str(tmp_path / "q.json"),
        log_dir=str(tmp_path / "logs"),
    )
    base.update(overrides)
    return Settings(**base)


def make_source(tmp_path, name="FaDi_test.bin", size=1000):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def make_controller(tmp_path, code=FAKE_OK, stat_text=STAT_OK, on_change=None, exec_fn=None):
    settings = make_settings(tmp_path)
    store = JobStore(settings.state_file)

    async def fake_stat(_settings, _uri):
        if stat_text is None:
            raise RuntimeError("stat not available")
        return stat_text

    return JobController(
        settings,
        store,
        argv_fn=lambda s, j, p: [sys.executable, "-c", code],
        stat_fn=fake_stat,
        on_change=on_change,
        exec_fn=exec_fn or exists_exec,
    )

from ossutil_tui.profile import ProfileError


class FakeProtector:
    """Reversible stand-in for DPAPI so tests run anywhere."""

    def protect(self, data):
        return b"FAKE:" + data[::-1]

    def unprotect(self, data):
        if not data.startswith(b"FAKE:"):
            raise ProfileError("cannot decrypt")
        return data[5:][::-1]


class BrokenProtector(FakeProtector):
    def unprotect(self, data):
        raise ProfileError("Cannot decrypt the saved secret. Please log in again.")

def make_context(tmp_path, controller=None, settings=None, check_fn=None):
    from ossutil_tui.context import AppContext, AppPaths
    from ossutil_tui.profile import ProfileStore

    paths = AppPaths(tmp_path / "ctx")
    store = ProfileStore(paths.profile_file, FakeProtector())
    exe = tmp_path / "ossutil.exe"
    exe.write_text("x")
    extra = {"check_fn": check_fn} if check_fn else {}
    context = AppContext(paths, store, exe_hint=str(exe), **extra)
    if controller is not None:
        context.adopt(controller, settings or controller.settings)
    return context

TREE = {
    "oss://bucket-sandbox/": ["data/", "readme.txt"],
    "oss://bucket-sandbox/data/": ["team-alpha-black/", "team-alpha-white/", "web-archive/", "zz-notes.txt"],
    "oss://bucket-sandbox/data/team-alpha-black/": ["incoming/"],
    "oss://bucket-sandbox/data/team-alpha-black/incoming/": ["20261008/", "20261009/", "20261012/"],
    "oss://bucket-sandbox/data/team-alpha-white/": ["ft/", "incoming/", "reports/"],
    "oss://bucket-sandbox/FaDi-spike/": [],
}

DU_TEXT = (
    "Standard      \t60902               \t14521495894902\n"
    "total object count: 60902               \ttotal object sum size: 14521495894902\n"
    "total du size:14521495894902\n"
)

BIG_STAT = "Content-Length : 1073741824\nLast-Modified : 2026-11-03 03:33:24\nX-Oss-Hash-Crc64ecma : 3534425600523290380\nEtag : ABC-256\n"


def make_tree_exec(tree=None, *, du_code=1, calls=None):
    """A fake ossutil for browsing: serves `ls -d` from a dict and `du` with a canned summary."""
    data = TREE if tree is None else tree

    async def fake_exec(argv, env):
        if calls is not None:
            calls.append(list(argv))
        command = argv[1]
        if command == "du":
            return du_code, DU_TEXT, "Error: ListMultipartUploads AccessDenied"
        if command != "ls":
            return 1, "", "unsupported in fake"
        uri = argv[2]
        limit = int(argv[argv.index("--limited-num") + 1]) if "--limited-num" in argv else 1000
        if uri not in data:
            return 0, "Object and Directory Number is: 0\n\n0.1(s) elapsed\n", ""
        assert data[uri] == sorted(data[uri]), f"fake bucket listing for {uri} must be sorted like real OSS"
        lines = [uri + child for child in data[uri]]
        if "--start-after" in argv:
            after = "oss://bucket-sandbox/" + argv[argv.index("--start-after") + 1]
            lines = [line for line in lines if line > after]
        else:
            lines = [uri] + lines  # first page starts with the folder's own marker
        lines = lines[:limit]
        return 0, "\n".join(lines) + f"\nObject and Directory Number is: {len(lines)}\n\n0.1(s) elapsed\n", ""

    return fake_exec


async def exists_exec(argv, env):
    """Minimal fake: every folder exists. Used where tests do not care about browsing."""
    if argv[1] == "ls":
        return 0, f"{argv[2]}\nObject and Directory Number is: 1\n", ""
    return 1, "", "unsupported in fake"

async def press_start(pilot, app, timeout=10.0):
    """Wait for the pre-flight dialog to finish its destination check, then press Start."""
    from textual.widgets import Button

    for _ in range(int(timeout / 0.1)):
        try:
            button = app.screen.query_one("#start", Button)
        except Exception:
            button = None
        if button is not None and not button.disabled:
            button.press()
            return
        await pilot.pause(0.1)
    raise AssertionError("the pre-flight dialog never became ready to start")
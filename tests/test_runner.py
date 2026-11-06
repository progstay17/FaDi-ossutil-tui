import asyncio
import sys

from ossutil_tui.runner import ProgressEvent, UploadRunner

LINE = "Total 1 objects,1 GiB, Upload... done:(0 objects), {pct}%, avg 20.000 MiB/s"

FAKE_OK = r"""
import sys, time
for p in ("10.000", "40.000", "70.000", "100.000"):
    sys.stdout.write("Total 1 objects,1 GiB, Upload... done:(0 objects), %s%%, avg 20.000 MiB/s\r" % p)
    sys.stdout.flush()
    time.sleep(0.05)
sys.stdout.write("\nSuccess: Total 1 file, size 1000 B, Upload done:(1 objects, 1000 B)\n")
"""

FAKE_FAIL = r"""
import sys
sys.stderr.write("Error: simulated failure\n")
sys.exit(3)
"""

FAKE_SLOW = r"""
import sys, time
sys.stdout.write("Total 1 objects,1 GiB, Upload... done:(0 objects), 30.000%, avg 20.000 MiB/s\r")
sys.stdout.flush()
time.sleep(30)
"""

FAKE_SPLIT = r"""
import sys, time
sys.stdout.write("Total 1 objects,1 GiB, Upload... done:(0 objects), 5")
sys.stdout.flush()
time.sleep(0.3)
sys.stdout.write("5.500%, avg 1.000 MiB/s\r")
sys.stdout.flush()
"""

FAKE_EXPANDED_PROGRESS = r"""
import sys
sys.stdout.write("Total 660 objects,71.7 GiB, Upload... done:(108 objects,7.572 GiB,5 dirs), 10.825%, avg 20.463 MiB/s\r")
sys.stdout.flush()
"""


def argv_for(code):
    return [sys.executable, "-c", code]


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=20))


def test_success_run_reports_progress_and_success():
    events: list[ProgressEvent] = []
    runner = UploadRunner(on_progress=events.append)
    result = run(runner.run(argv_for(FAKE_OK), total_bytes=1000))
    assert result.exit_code == 0
    assert result.success is True
    assert result.paused is False
    assert [e.percent for e in events] == [10.0, 40.0, 70.0, 100.0]
    assert events[-1].done_bytes == 1000


def test_failure_run_captures_exit_code_and_stderr():
    runner = UploadRunner()
    result = run(runner.run(argv_for(FAKE_FAIL), total_bytes=1000))
    assert result.exit_code == 3
    assert result.success is False
    assert "simulated failure" in result.stderr_text


def test_pause_terminates_process_and_is_not_success():
    holder = {}

    def on_progress(event: ProgressEvent) -> None:
        if event.percent >= 30:
            holder["runner"].pause()

    runner = UploadRunner(on_progress=on_progress)
    holder["runner"] = runner
    result = run(runner.run(argv_for(FAKE_SLOW), total_bytes=1000))
    assert result.paused is True
    assert result.success is False


def test_progress_line_split_across_chunks_is_parsed_once_complete():
    events: list[ProgressEvent] = []
    runner = UploadRunner(on_progress=events.append)
    run(runner.run(argv_for(FAKE_SPLIT), total_bytes=1000))
    assert [e.percent for e in events] == [55.5]


def test_runner_emits_progress_for_expanded_ossutil_summary():
    events: list[ProgressEvent] = []
    runner = UploadRunner(on_progress=events.append)
    run(runner.run(argv_for(FAKE_EXPANDED_PROGRESS), total_bytes=1000))
    assert [e.percent for e in events] == [10.825]


def test_pause_without_running_process_is_harmless():
    UploadRunner().pause()


FAKE_NO_OUTPUT_SUCCESS = r"""
import sys, time
time.sleep(0.55)
sys.stdout.write("Success: Total 1 file, size 1000 B, Upload done:(1 objects, 1000 B)\\n")
sys.stdout.flush()
"""


def test_runner_detects_real_inactivity():
    runner = UploadRunner(stall_timeout=0.2)
    result = run(runner.run(argv_for(FAKE_SLOW), total_bytes=1000))
    assert result.stalled is True
    assert result.success is False
    assert result.paused is False


def test_checkpoint_activity_resets_stall_watchdog():
    async def scenario():
        runner = UploadRunner(stall_timeout=0.25)
        task = asyncio.create_task(runner.run(argv_for(FAKE_NO_OUTPUT_SUCCESS), total_bytes=1000))
        for _ in range(8):
            await asyncio.sleep(0.08)
            runner.notify_activity()
        return await asyncio.wait_for(task, timeout=3)

    result = asyncio.run(scenario())
    assert result.stalled is False
    assert result.success is True

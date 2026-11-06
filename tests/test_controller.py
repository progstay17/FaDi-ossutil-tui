import asyncio
import json

import pytest

from ossutil_tui.controller import JobController, JobError
from ossutil_tui.jobs import JobStatus, JobStore
from tests.fakes import (
    FAKE_FAIL, FAKE_OK, FAKE_SLOW, STAT_OK, STAT_WRONG_SIZE,
    make_controller, make_settings, make_source,
)

DEST = "oss://bucket-sandbox/FaDi-spike/"


def run(coro, timeout=20):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


def test_add_job_validates_input(tmp_path):
    controller = make_controller(tmp_path)
    source = make_source(tmp_path)
    with pytest.raises(JobError, match="source file not found"):
        controller.add_job(str(tmp_path / "missing.bin"), DEST)
    with pytest.raises(JobError, match="destination"):
        controller.add_job(str(source), "bucket/path")
    with pytest.raises(JobError, match="destination"):
        controller.add_job(str(source), "oss://UPPER/x/")
    empty = make_source(tmp_path, name="FaDi_empty.bin", size=0)
    with pytest.raises(JobError, match="empty"):
        controller.add_job(str(empty), DEST)
    job = controller.add_job(f'"{source}"', DEST)
    assert job.size == 1000 and job.status is JobStatus.PENDING
    with pytest.raises(JobError, match="already queued"):
        controller.add_job(str(source), DEST)


def test_add_job_cannot_escape_the_configured_prefix(tmp_path):
    controller = make_controller(tmp_path)
    controller.default_root = "oss://bucket-sandbox/EGO/"
    source = make_source(tmp_path)
    controller.add_job(str(source), "oss://bucket-sandbox/EGO/sub/")
    other = make_source(tmp_path, name="outside.bin")
    with pytest.raises(JobError, match="outside the permitted prefix"):
        controller.add_job(str(other), "oss://bucket-sandbox/OTHER/")


def test_prefix_scoped_directory_checks_stay_inside_root(tmp_path):
    controller = make_controller(tmp_path)
    controller.default_root = "oss://bucket-sandbox/EGO/"
    requested = []

    async def prefix_exists(uri):
        requested.append(uri)
        return True

    controller.prefix_exists = prefix_exists
    result = run(controller.check_prefix("oss://bucket-sandbox/EGO/new-folder/"))
    assert result.exists
    assert requested == ["oss://bucket-sandbox/EGO/new-folder/"]
    with pytest.raises(JobError, match="outside the permitted prefix"):
        run(controller.list_directory("oss://bucket-sandbox/"))


def test_preexisting_queued_job_cannot_upload_outside_new_prefix(tmp_path):
    controller = make_controller(tmp_path)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    controller.default_root = "oss://bucket-sandbox/EGO/"
    with pytest.raises(JobError, match="queued destination is outside the permitted prefix"):
        run(controller.run(job.id))
    assert job.status is JobStatus.PENDING


def test_add_job_is_persisted(tmp_path):
    controller = make_controller(tmp_path)
    controller.add_job(str(make_source(tmp_path)), DEST)
    on_disk = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))
    assert len(on_disk["jobs"]) == 1


def test_preflight_reports_parts_command_and_region_warning(tmp_path):
    controller = make_controller(tmp_path)
    job = controller.add_job(str(make_source(tmp_path, size=10 * 1024 * 1024)), DEST)
    info = controller.preflight(job.id)
    assert info.parts == 3 and info.part_size == 4 * 1024 * 1024
    assert info.region == "ap-southeast-5"
    assert info.object_uri == DEST + "FaDi_test.bin"
    assert any("Region normalized" in w for w in info.warnings)


def test_successful_run_ends_verified_and_is_persisted_and_logged(tmp_path):
    controller = make_controller(tmp_path)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    run(controller.run(job.id))
    assert job.status is JobStatus.VERIFIED
    assert job.percent == 100.0
    assert job.remote_crc64 == "123456"
    reloaded = JobStore(str(tmp_path / "q.json")).load()
    assert reloaded[0].status is JobStatus.VERIFIED
    log = (tmp_path / "logs" / f"{job.id}.log").read_text(encoding="utf-8")
    assert "upload started" in log and "VERIFIED" in log


def test_size_mismatch_ends_verify_failed(tmp_path):
    controller = make_controller(tmp_path, stat_text=STAT_WRONG_SIZE)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    run(controller.run(job.id))
    assert job.status is JobStatus.VERIFY_FAILED
    assert "size mismatch" in job.message


def test_stat_error_ends_verify_failed_and_can_be_retried(tmp_path):
    controller = make_controller(tmp_path, stat_text=None)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    run(controller.run(job.id))
    assert job.status is JobStatus.VERIFY_FAILED
    assert "stat failed" in job.message

    async def fixed_stat(_s, _u):
        return STAT_OK

    controller._stat_fn = fixed_stat
    run(controller.verify(job.id))
    assert job.status is JobStatus.VERIFIED


def test_failed_run_keeps_error_message(tmp_path):
    controller = make_controller(tmp_path, code=FAKE_FAIL)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    run(controller.run(job.id))
    assert job.status is JobStatus.FAILED
    assert "simulated failure" in job.message


def test_cannot_start_ossutil_marks_failed(tmp_path):
    settings = make_settings(tmp_path)
    controller = JobController(
        settings, JobStore(settings.state_file), argv_fn=lambda s, j, p: [str(tmp_path / "nope.exe")]
    )
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    run(controller.run(job.id))
    assert job.status is JobStatus.FAILED
    assert "cannot start ossutil" in job.message


async def _pause_when_started(controller, job):
    task = asyncio.create_task(controller.run(job.id))
    for _ in range(100):
        if job.percent >= 30:
            break
        await asyncio.sleep(0.05)
    return task


def test_pause_marks_paused_and_resumable(tmp_path):
    controller = make_controller(tmp_path, code=FAKE_SLOW)
    job = controller.add_job(str(make_source(tmp_path)), DEST)

    async def scenario():
        task = await _pause_when_started(controller, job)
        assert job.status is JobStatus.UPLOADING and controller.is_busy()
        controller.pause(job.id)
        await task

    run(scenario())
    assert job.status is JobStatus.PAUSED and job.resumable is True
    assert not controller.is_busy()


def test_only_one_upload_at_a_time(tmp_path):
    controller = make_controller(tmp_path, code=FAKE_SLOW)
    first = controller.add_job(str(make_source(tmp_path)), DEST)
    second = controller.add_job(str(make_source(tmp_path, name="FaDi_other.bin")), DEST)

    async def scenario():
        task = await _pause_when_started(controller, first)
        with pytest.raises(JobError, match="another upload"):
            await controller.run(second.id)
        controller.pause(first.id)
        await task

    run(scenario())


def test_pause_without_running_job_is_an_error(tmp_path):
    controller = make_controller(tmp_path)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    with pytest.raises(JobError, match="not uploading"):
        controller.pause(job.id)


def test_remove_rules(tmp_path):
    controller = make_controller(tmp_path, code=FAKE_SLOW)
    pending = controller.add_job(str(make_source(tmp_path)), DEST)
    controller.remove(pending.id)
    assert controller.jobs == []

    job = controller.add_job(str(make_source(tmp_path)), DEST)

    async def scenario():
        task = await _pause_when_started(controller, job)
        with pytest.raises(JobError, match="running"):
            controller.remove(job.id)
        controller.pause(job.id)
        await task
        with pytest.raises(JobError, match="checkpoint"):
            controller.remove(job.id)

    run(scenario())


def test_shutdown_pauses_running_job_and_saves_state(tmp_path):
    controller = make_controller(tmp_path, code=FAKE_SLOW)
    job = controller.add_job(str(make_source(tmp_path)), DEST)

    async def scenario():
        task = await _pause_when_started(controller, job)
        await controller.shutdown()
        await task

    run(scenario())
    assert JobStore(str(tmp_path / "q.json")).load()[0].status is JobStatus.PAUSED


def test_interrupted_job_is_paused_after_restart(tmp_path):
    controller = make_controller(tmp_path)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    job.status = JobStatus.UPLOADING
    controller.store.save(controller.jobs)
    restarted = make_controller(tmp_path)
    assert restarted.jobs[0].status is JobStatus.PAUSED and restarted.jobs[0].resumable


def test_resume_after_pause_completes(tmp_path):
    controller = make_controller(tmp_path, code=FAKE_OK)
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    job.status = JobStatus.PAUSED
    job.resumable = True
    run(controller.run(job.id))
    assert job.status is JobStatus.VERIFIED

FAKE_ENV = r"""
import os, sys
if os.environ.get("OSS_ACCESS_KEY_ID") == "K" and os.environ.get("OSS_ACCESS_KEY_SECRET") == "S":
    sys.stdout.write("Total 1 objects,1 KiB, Upload... done:(0 objects), 100.000%, avg 1.000 MiB/s\r")
    sys.stdout.write("\nSuccess: ok\n")
else:
    sys.stderr.write("Error: credentials missing\n")
    sys.exit(4)
"""


def test_credentials_reach_the_ossutil_process_environment(tmp_path):
    import os
    from ossutil_tui.controller import JobController
    from ossutil_tui.jobs import JobStore
    from tests.fakes import STAT_OK, make_settings
    import sys

    settings = make_settings(tmp_path)

    async def fake_stat(_s, _u):
        return STAT_OK

    env = {**os.environ, "OSS_ACCESS_KEY_ID": "K", "OSS_ACCESS_KEY_SECRET": "S"}
    controller = JobController(
        settings, JobStore(settings.state_file),
        argv_fn=lambda s, j, p: [sys.executable, "-c", FAKE_ENV],
        stat_fn=fake_stat, env_fn=lambda: env,
    )
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    run(controller.run(job.id))
    assert job.status is JobStatus.VERIFIED


def test_without_credentials_the_process_fails_and_job_is_failed(tmp_path):
    import os
    import sys
    from ossutil_tui.controller import JobController
    from ossutil_tui.jobs import JobStore
    from tests.fakes import make_settings

    settings = make_settings(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith("OSS_")}
    controller = JobController(
        settings, JobStore(settings.state_file),
        argv_fn=lambda s, j, p: [sys.executable, "-c", FAKE_ENV], env_fn=lambda: env,
    )
    job = controller.add_job(str(make_source(tmp_path)), DEST)
    run(controller.run(job.id))
    assert job.status is JobStatus.FAILED and "credentials missing" in job.message


def test_add_folder_job_scans_tree_and_preserves_root_name(tmp_path):
    controller = make_controller(tmp_path)
    source = tmp_path / "SESSION_001"
    (source / "CAMERA_01" / "VIDEO").mkdir(parents=True)
    (source / "CAMERA_02" / "EMPTY").mkdir(parents=True)
    (source / "CAMERA_01" / "VIDEO" / "001.mcap").write_bytes(b"x" * 1200)
    (source / "root.txt").write_bytes(b"root")

    job = controller.add_job(str(source), DEST)

    assert job.is_folder
    assert job.name == "SESSION_001"
    assert job.object_uri == DEST + "SESSION_001/"
    assert job.folder_files == 2
    assert job.folder_dirs == 4
    assert job.size == 1204
    restored = JobStore(controller.settings.state_file).load()[0]
    assert restored.is_folder and restored.folder_files == 2 and restored.folder_dirs == 4


def test_folder_argv_is_recursive_resumable_and_update_safe(tmp_path):
    from ossutil_tui.controller import default_argv

    controller = make_controller(tmp_path)
    source = tmp_path / "SESSION_001"
    source.mkdir()
    job = controller.add_job(str(source), DEST)
    argv = default_argv(controller.settings, job, controller.settings.part_size)

    assert argv[1] == "cp"
    assert argv[2].endswith("SESSION_001" + __import__("os").sep)
    assert argv[3] == DEST + "SESSION_001/"
    assert "-r" in argv
    assert "-u" in argv
    assert "--checkpoint-dir" in argv


def test_folder_upload_verifies_total_size_and_object_count(tmp_path):
    source = tmp_path / "SESSION_001"
    (source / "CAMERA_01" / "VIDEO").mkdir(parents=True)
    (source / "CAMERA_02" / "EMPTY").mkdir(parents=True)
    (source / "CAMERA_01" / "VIDEO" / "001.mcap").write_bytes(b"x" * 1000)
    calls = []

    async def fake_exec(argv, env):
        calls.append(argv)
        if argv[1] == "du":
            return 0, "total object count: 6 total object sum size: 1000\n", ""
        return 0, "", ""

    controller = make_controller(tmp_path, code=FAKE_OK, exec_fn=fake_exec)
    job = controller.add_job(str(source), DEST)
    run(controller.run(job.id))

    assert job.status is JobStatus.VERIFIED
    assert "1 file(s)" in job.message
    assert any(call[1] == "du" for call in calls)



def test_folder_verification_falls_back_to_recursive_ls_when_du_is_denied(tmp_path):
    source = tmp_path / "SESSION_001"
    source.mkdir()
    (source / "a.mcap").write_bytes(b"a" * 600)
    (source / "b.mcap").write_bytes(b"b" * 400)
    calls = []

    async def fake_exec(argv, env):
        calls.append(argv)
        if argv[1] == "du":
            return 1, "", "AccessDenied: ListMultipartUploads is not authorized"
        if argv[1] == "ls":
            return 0, (
                "LastModifiedTime                    Size(B)  StorageClass   ETag                                    ObjectName\n"
                "2026-11-06 15:00:00 +0800 CST           0      Standard   ABCD        oss://bucket-sandbox/FaDi-spike/SESSION_001/\n"
                "2026-11-06 15:00:01 +0800 CST         600      Standard   EFGH        oss://bucket-sandbox/FaDi-spike/SESSION_001/a.mcap\n"
                "2026-11-06 15:00:02 +0800 CST         400      Standard   IJKL        oss://bucket-sandbox/FaDi-spike/SESSION_001/b.mcap\n"
                "Object Number is: 3\n"
            ), ""
        return 0, "", ""

    controller = make_controller(tmp_path, code=FAKE_OK, exec_fn=fake_exec)
    job = controller.add_job(str(source), DEST)
    run(controller.run(job.id))

    assert job.status is JobStatus.VERIFIED
    assert "verified via ls" in job.message
    assert any(call[1] == "du" for call in calls)
    assert any(call[1] == "ls" and "-r" in call for call in calls)

def test_empty_folder_upload_creates_and_verifies_marker(tmp_path):
    source = tmp_path / "EMPTY_SESSION"
    source.mkdir()
    calls = []

    async def fake_exec(argv, env):
        calls.append(argv)
        if argv[1] == "du":
            return 0, "total object count: 1 total object sum size: 0\n", ""
        return 0, "", ""

    controller = make_controller(tmp_path, code=FAKE_OK, exec_fn=fake_exec)
    job = controller.add_job(str(source), DEST)
    run(controller.run(job.id))

    assert job.is_folder and job.folder_files == 0 and job.size == 0
    assert job.status is JobStatus.VERIFIED
    assert "0 file(s)" in job.message



def test_folder_upload_creates_root_marker_when_missing(tmp_path):
    source = tmp_path / "EMPTY_SESSION"
    source.mkdir()
    calls = []

    async def missing_marker(_settings, _uri):
        raise RuntimeError("NoSuchKey")

    async def fake_exec(argv, env):
        calls.append(argv)
        if argv[1] == "du":
            return 0, "total object count: 1 total object sum size: 0\n", ""
        return 0, "", ""

    controller = make_controller(tmp_path, code=FAKE_OK, exec_fn=fake_exec)
    controller._stat_fn = missing_marker
    job = controller.add_job(str(source), DEST)
    run(controller.run(job.id))

    assert job.status is JobStatus.VERIFIED
    assert any(call[1] == "mkdir" and call[2] == job.object_uri for call in calls)
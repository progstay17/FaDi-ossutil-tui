import json

import pytest

from ossutil_tui.jobs import Job, JobStatus, JobStore, new_job
from ossutil_tui.state import StateCorruptError, save_state


def test_object_uri_for_prefix_and_explicit_key():
    job = new_job(r"C:\Data\FaDi_test_1gb.bin", "oss://b/FaDi-spike/", 10)
    assert job.name == "FaDi_test_1gb.bin"
    assert job.object_uri == "oss://b/FaDi-spike/FaDi_test_1gb.bin"
    job2 = new_job("/tmp/a.bin", "oss://b/key.bin", 10)
    assert job2.object_uri == "oss://b/key.bin"


def test_done_bytes_by_status():
    job = new_job("a.bin", "oss://b/", 1000)
    job.percent = 25.0
    assert job.done_bytes == 250
    job.status = JobStatus.VERIFIED
    assert job.done_bytes == 1000


def test_store_roundtrip(tmp_path):
    store = JobStore(str(tmp_path / "q.json"))
    job = new_job("a.bin", "oss://b/p/", 1000)
    job.status = JobStatus.VERIFIED
    job.remote_crc64 = "123"
    store.save([job])
    loaded = JobStore(str(tmp_path / "q.json")).load()
    assert loaded == [job]


def test_interrupted_jobs_are_made_honest_and_persisted(tmp_path):
    path = tmp_path / "q.json"
    store = JobStore(str(path))
    uploading = new_job("a.bin", "oss://b/", 1000)
    uploading.status = JobStatus.UPLOADING
    verifying = new_job("b.bin", "oss://b/", 1000)
    verifying.status = JobStatus.VERIFYING
    store.save([uploading, verifying])
    jobs = store.load()
    assert jobs[0].status is JobStatus.PAUSED and jobs[0].resumable is True
    assert jobs[1].status is JobStatus.UPLOADED
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert [j["status"] for j in on_disk["jobs"]] == ["PAUSED", "UPLOADED"]


def test_legacy_job_without_elapsed_seconds_loads_with_zero_elapsed():
    job = Job.from_dict(
        {"id": "legacy", "source": "a.bin", "destination": "oss://b/", "size": 10, "status": "PAUSED"}
    )
    assert job.status is JobStatus.PAUSED
    assert job.elapsed_seconds == 0.0


def test_unknown_status_becomes_failed_with_message():
    job = Job.from_dict(
        {"id": "1", "source": "a", "destination": "oss://b/", "size": 1, "status": "WEIRD"}
    )
    assert job.status is JobStatus.FAILED
    assert "unknown status" in job.message


def test_invalid_entry_raises_instead_of_dropping_jobs(tmp_path):
    path = tmp_path / "q.json"
    save_state(path, {"version": 1, "jobs": [{"id": "1"}]})
    with pytest.raises(StateCorruptError):
        JobStore(str(path)).load()


def test_missing_file_gives_empty_queue(tmp_path):
    assert JobStore(str(tmp_path / "none.json")).load() == []
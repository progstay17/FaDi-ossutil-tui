from rich.text import Text

from ossutil_tui.controller import PreflightInfo
from ossutil_tui.jobs import JobStatus, LiveStats, new_job
from ossutil_tui.views import render_detail, render_preflight, render_row, render_total

GB = 1024**3


def make_job(status=JobStatus.PENDING, size=GB, percent=0.0, name="FaDi_test.bin"):
    job = new_job(rf"C:\Data\{name}", "oss://b/p/", size)
    job.status = status
    job.percent = percent
    return job


def test_row_has_badge_name_size_percent():
    badge, name, size, pct = render_row(make_job(JobStatus.UPLOADING, percent=68.4))
    assert str(badge) == "[UP]"
    assert str(name) == "FaDi_test.bin"
    assert str(size) == "1.00 GB"
    assert str(pct).strip() == "68.4%"


def test_detail_while_uploading_shows_speed_and_eta():
    job = make_job(JobStatus.UPLOADING, percent=50.0)
    live = LiveStats(speed=20 * 1024**2, eta=25.6, last_event=100.0)
    plain = render_detail(job, live, part_size=4 * 1024**2, now=101.0).plain
    assert "[UP] UPLOADING" in plain
    assert "512.00 MB / 1.00 GB" in plain
    assert "20.00 MB/s" in plain
    assert "25s" in plain
    assert "256 x 4.00 MB" in plain


def test_detail_marks_stall_after_ten_seconds_without_events():
    job = make_job(JobStatus.UPLOADING, percent=50.0)
    live = LiveStats(speed=1.0, eta=5.0, last_event=100.0)
    assert "Stalled" in render_detail(job, live, part_size=4 * 1024**2, now=111.0).plain


def test_detail_paused_shows_resume_hint_and_paused_eta():
    job = make_job(JobStatus.PAUSED, percent=40.0)
    job.resumable = True
    plain = render_detail(job, None, part_size=4 * 1024**2, now=0.0).plain
    assert "[RESUME]" in plain and "Paused" in plain


def test_detail_verified_and_failed_lines():
    ok = make_job(JobStatus.VERIFIED, percent=100.0)
    ok.remote_crc64 = "999"
    assert "VERIFIED (CRC64 999)" in render_detail(ok, None, part_size=4 * 1024**2, now=0.0).plain
    bad = make_job(JobStatus.VERIFY_FAILED, percent=100.0)
    bad.message = "size mismatch: local 1, remote 2"
    plain = render_detail(bad, None, part_size=4 * 1024**2, now=0.0).plain
    assert "VERIFY FAILED" in plain and "size mismatch" in plain


def test_detail_ascii_mode_uses_ascii_bar():
    plain = render_detail(make_job(percent=50.0), None, part_size=4 * 1024**2, now=0.0, unicode=False).plain
    assert "#" in plain and "█" not in plain


def test_total_is_weighted_by_bytes_not_by_job():
    small = make_job(JobStatus.VERIFIED, size=100, percent=100.0, name="a.bin")
    big = make_job(JobStatus.PAUSED, size=900, percent=50.0, name="b.bin")
    plain = render_total([small, big], {}, unicode=True).plain
    assert "55.00%" in plain
    assert "1 / 2" in plain
    assert "1 paused" in plain and "1 done" in plain


def test_total_shows_speed_and_eta_for_active_job():
    job = make_job(JobStatus.UPLOADING, size=1000, percent=50.0)
    live = {job.id: LiveStats(speed=100.0, eta=5.0, last_event=0.0)}
    plain = render_total([job], live).plain
    assert "100 B/s" in plain and "ETA 5s" in plain and "1 uploading" in plain


def test_total_empty_queue():
    assert render_total([], {}).plain == "Queue is empty"


def test_preflight_text_contains_key_facts():
    info = PreflightInfo(
        source=r"C:\Data\FaDi_test.bin", destination="oss://b/p/", object_uri="oss://b/p/FaDi_test.bin",
        size=GB, part_size=4 * 1024**2, parts=256, region="ap-southeast-5",
        checkpoint_dir=r"C:\ck", command="ossutil cp a b", warnings=["Region normalized from 'x'"],
    )
    plain = render_preflight(info).plain
    for needle in ("PRE-FLIGHT", "oss://b/p/FaDi_test.bin", "256", "ap-southeast-5", "CRC64", "ossutil cp a b", "[!] Region normalized"):
        assert needle in plain
    assert isinstance(render_preflight(info), Text)

def test_object_and_folder_info_text():
    from ossutil_tui.views import render_folder_info, render_object_info

    stat = {"Content-Length": "1073741824", "X-Oss-Hash-Crc64ecma": "35344", "Etag": "ABC-256"}
    plain = render_object_info("a.mcap", stat).plain
    assert "1.00 GB (1073741824 B)" in plain and "35344" in plain and "ABC-256" in plain
    assert "No details" in render_object_info("a.mcap", {}).plain
    folder = render_folder_info("20261008", 1132, 202278510266).plain
    assert "1,132" in folder and "188.39 GB" in folder


def test_preflight_note_is_shown():
    from ossutil_tui.controller import PreflightInfo
    from ossutil_tui.views import render_preflight

    info = PreflightInfo("s", "oss://b/p/", "oss://b/p/s", 1, 4194304, 1, "ap-southeast-5", "c", "cmd", [])
    assert "Checking destination folder..." in render_preflight(info, note="Checking destination folder...").plain
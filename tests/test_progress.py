import pytest

from ossutil_tui.progress import SpeedTracker


def test_speed_from_byte_deltas():
    tracker = SpeedTracker(total_bytes=1000)
    tracker.update(percent=10, now=0.0)
    tracker.update(percent=20, now=1.0)
    assert tracker.done_bytes == 200
    assert tracker.speed == pytest.approx(100.0)


def test_eta_uses_remaining_bytes():
    tracker = SpeedTracker(total_bytes=1000)
    tracker.update(percent=10, now=0.0)
    tracker.update(percent=20, now=1.0)
    assert tracker.eta_seconds() == pytest.approx(8.0)


def test_eta_unknown_before_second_sample():
    tracker = SpeedTracker(total_bytes=1000)
    tracker.update(percent=10, now=0.0)
    assert tracker.speed is None
    assert tracker.eta_seconds() is None


def test_stall_keeps_speed_and_eta_is_none_when_zero():
    tracker = SpeedTracker(total_bytes=1000)
    tracker.update(percent=10, now=0.0)
    tracker.update(percent=10, now=1.0)
    assert tracker.speed == pytest.approx(0.0)
    assert tracker.eta_seconds() is None


def test_resume_start_does_not_produce_bogus_speed():
    # Resume jumps from 0 to 30% at first sample; no earlier sample means no speed yet.
    tracker = SpeedTracker(total_bytes=1000)
    tracker.update(percent=30, now=0.0)
    assert tracker.speed is None
    assert tracker.done_bytes == 300


def test_percent_over_100_is_clamped():
    tracker = SpeedTracker(total_bytes=1000)
    tracker.update(percent=100.5, now=0.0)
    assert tracker.done_bytes == 1000


def test_rejects_non_positive_total():
    with pytest.raises(ValueError):
        SpeedTracker(total_bytes=0)
import pytest

from ossutil_tui.format import format_eta, format_size, format_speed, render_bar, truncate_middle


@pytest.mark.parametrize(
    "value, expected",
    [(0, "0 B"), (1023, "1023 B"), (1024, "1.00 KB"), (1536 * 1024**2, "1.50 GB"), (3 * 1024**4, "3.00 TB")],
)
def test_format_size(value, expected):
    assert format_size(value) == expected


def test_format_size_rejects_negative():
    with pytest.raises(ValueError):
        format_size(-1)


def test_format_speed():
    assert format_speed(None) == "--"
    assert format_speed(0) == "0 B/s"
    assert format_speed(12.8 * 1024**2) == "12.80 MB/s"


@pytest.mark.parametrize(
    "seconds, expected",
    [(42, "42s"), (752, "12m 32s"), (8040, "2h 14m"), (97200, "1d 03h"), (-5, "0s")],
)
def test_format_eta_values(seconds, expected):
    assert format_eta(seconds) == expected


def test_format_eta_special_states():
    assert format_eta(None) == "Calculating..."
    assert format_eta(100, paused=True) == "Paused"
    assert format_eta(100, stalled=True) == "Stalled"


def test_truncate_middle_keeps_file_name_and_width():
    path = r"C:\Users\user\Downloads\2026\archive\dataset.tar"
    short = truncate_middle(path, 30)
    assert len(short) == 30
    assert short.endswith("dataset.tar")
    assert "…" in short


def test_truncate_middle_short_text_untouched_and_ascii_ellipsis():
    assert truncate_middle("abc", 10) == "abc"
    assert "..." in truncate_middle("x" * 50, 20, "...")


def test_render_bar_unicode_and_ascii():
    assert render_bar(50, 10) == "█████░░░░░"
    assert render_bar(50, 10, unicode=False) == "#####-----"
    assert render_bar(150, 4) == "████"
    assert render_bar(-5, 4) == "░░░░"
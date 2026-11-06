import pytest

from ossutil_tui.ossutil import (
    CpRequest,
    is_success,
    normalize_region,
    parse_progress,
    parse_stat,
)

GiB = 1024**3


def make_request(**overrides):
    base = dict(
        exe="ossutil.exe",
        source=r"C:\spike\FaDi-dummy\FaDi_test_1gb.bin",
        destination="oss://bucket-sandbox/FaDi-spike/FaDi_test_1gb.bin",
        config_file=r"C:\spike\FaDi.ossutilconfig",
        region="oss-ap-southeast-5",
        checkpoint_dir=r"C:\spike\FaDi-checkpoint",
        part_size=4 * 1024 * 1024,
    )
    base.update(overrides)
    return CpRequest(**base)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("oss-ap-southeast-5", "ap-southeast-5"),
        ("oss-ap-southeast-5.aliyuncs.com", "ap-southeast-5"),
        ("https://oss-ap-southeast-5.aliyuncs.com", "ap-southeast-5"),
        ("ap-southeast-5", "ap-southeast-5"),
        ("  OSS-CN-HANGZHOU ", "cn-hangzhou"),
    ],
)
def test_normalize_region(raw, expected):
    assert normalize_region(raw) == expected


def test_argv_contains_required_flags():
    argv = make_request().argv()
    assert argv[:2] == ["ossutil.exe", "cp"]
    assert argv[argv.index("--region") + 1] == "ap-southeast-5"
    assert argv[argv.index("--checkpoint-dir") + 1] == r"C:\spike\FaDi-checkpoint"
    assert argv[argv.index("--part-size") + 1] == str(4 * 1024 * 1024)
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "-f" not in argv


def test_overwrite_adds_force_flag():
    assert "-f" in make_request(overwrite=True).argv()


def test_rejects_empty_checkpoint_dir():
    with pytest.raises(ValueError, match="checkpoint_dir"):
        make_request(checkpoint_dir="  ")


def test_rejects_non_oss_destination():
    with pytest.raises(ValueError, match="oss://"):
        make_request(destination="bucket/path")


@pytest.mark.parametrize("size", [0, 100 * 1024 - 1, 5 * GiB + 1])
def test_rejects_part_size_out_of_range(size):
    with pytest.raises(ValueError, match="part_size"):
        make_request(part_size=size)


def test_parse_progress_takes_last_value_from_carriage_return_chunk():
    chunk = (
        "Total 1 objects,1 GiB, Upload... done:(0 objects), 30.469%, avg 20.973 MiB/s\r"
        "Total 1 objects,1 GiB, Upload... done:(0 objects), 59.766%, avg 21.365 MiB/s\r"
        "Total 1 objects,1 GiB, Upload... done:(0 objects), 70.312%, avg 21.408 MiB/s"
    )
    assert parse_progress(chunk) == 70.312


def test_parse_progress_accepts_ossutil_summary_with_size_and_directory_fields():
    line = "Total 660 objects,71.7 GiB, Upload... done:(108 objects,7.572 GiB,5 dirs), 10.825%, avg 20.463 MiB/s"
    assert parse_progress(line) == 10.825


def test_parse_progress_accepts_both_old_and_expanded_summaries_in_same_chunk():
    chunk = (
        "Upload... done:(0 objects), 5.000%, avg 1.000 MiB/s\r"
        "Upload... done:(108 objects,7.572 GiB,5 dirs), 10.825%, avg 20.463 MiB/s"
    )
    assert parse_progress(chunk) == 10.825


def test_parse_progress_returns_none_without_match():
    assert parse_progress("nothing useful here") is None


def test_success_line_detected():
    line = "Success: Total 1 file, size 1073741824 B, Upload done:(1 objects, 1073741824 B)"
    assert is_success(line)
    assert not is_success("Total 1 objects,1 GiB, Upload... done:(0 objects), 100.000%")


def test_parse_stat_extracts_size_and_crc64():
    text = (
        "ACL                         : default\n"
        "Content-Length              : 1073741824\n"
        "Etag                        : \"E835F3CC826F8862B0DCA1963E7223F5-256\"\n"
        "X-Oss-Hash-Crc64ecma        : 3534425600523290380\n"
        "X-Oss-Object-Type           : Multipart\n"
    )
    stat = parse_stat(text)
    assert stat["Content-Length"] == "1073741824"
    assert stat["X-Oss-Hash-Crc64ecma"] == "3534425600523290380"
    assert stat["X-Oss-Object-Type"] == "Multipart"


def test_choose_part_size_keeps_preferred_when_within_limit():
    from ossutil_tui.ossutil import choose_part_size
    assert choose_part_size(1024**3, 4 * 1024**2) == 4 * 1024**2


def test_choose_part_size_bumps_to_stay_under_10000_parts():
    from ossutil_tui.ossutil import choose_part_size
    preferred = 4 * 1024**2
    for size in (40 * 1024**3, 100 * 1024**3, 500 * 1024**3):
        chosen = choose_part_size(size, preferred)
        assert chosen >= preferred
        assert chosen % (1024**2) == 0
        assert -(-size // chosen) <= 10_000

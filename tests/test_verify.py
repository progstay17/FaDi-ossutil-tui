from ossutil_tui.verify import VerifyStatus, verify_remote

LOCAL = 1073741824


def test_verified_when_size_matches_and_crc_present():
    stat = {"Content-Length": str(LOCAL), "X-Oss-Hash-Crc64ecma": "3534425600523290380"}
    result = verify_remote(LOCAL, stat)
    assert result.status is VerifyStatus.VERIFIED
    assert result.remote_crc64 == "3534425600523290380"


def test_size_mismatch_fails():
    stat = {"Content-Length": str(LOCAL - 1), "X-Oss-Hash-Crc64ecma": "1"}
    result = verify_remote(LOCAL, stat)
    assert result.status is VerifyStatus.VERIFY_FAILED
    assert "size mismatch" in result.reason


def test_missing_crc_fails():
    result = verify_remote(LOCAL, {"Content-Length": str(LOCAL)})
    assert result.status is VerifyStatus.VERIFY_FAILED
    assert "CRC64" in result.reason


def test_missing_size_fails():
    result = verify_remote(LOCAL, {"X-Oss-Hash-Crc64ecma": "1"})
    assert result.status is VerifyStatus.VERIFY_FAILED


def test_non_numeric_size_fails():
    result = verify_remote(LOCAL, {"Content-Length": "abc", "X-Oss-Hash-Crc64ecma": "1"})
    assert result.status is VerifyStatus.VERIFY_FAILED
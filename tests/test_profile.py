import sys

import pytest

from ossutil_tui.profile import DpapiProtector, Profile, ProfileError, ProfileStore, validate_login
from tests.fakes import FakeProtector

SECRET = "S3cretValue-123456"


def good_input(tmp_path, **overrides):
    exe = tmp_path / "ossutil.exe"
    exe.write_text("x")
    base = dict(
        access_key_id="FAKEKEYID12345678",
        secret=SECRET,
        region="oss-ap-southeast-5",
        bucket="oss://bucket-sandbox/some/prefix/",
        exe=f'"{exe}"',
    )
    base.update(overrides)
    return base


def test_valid_input_is_normalized(tmp_path):
    check = validate_login(**good_input(tmp_path))
    assert check.errors == []
    assert check.profile.region == "ap-southeast-5"
    assert check.profile.bucket == "bucket-sandbox"
    assert check.profile.prefix == "some/prefix"
    assert check.profile.root_uri == "oss://bucket-sandbox/some/prefix/"
    assert check.profile.exe.endswith("ossutil.exe") and '"' not in check.profile.exe
    assert check.secret == SECRET


@pytest.mark.parametrize(
    "field, value, needle",
    [
        ("access_key_id", "short", "AccessKey ID"),
        ("access_key_id", "has space 12345", "AccessKey ID"),
        ("secret", "", "Secret is empty"),
        ("secret", "abc", "Secret looks invalid"),
        ("secret", "has a space inside", "Secret looks invalid"),
        ("region", "", "Region is empty"),
        ("region", "Jakarta", "Region does not look valid"),
        ("bucket", "UPPER_case", "Bucket name"),
        ("bucket", "", "Bucket name"),
    ],
)
def test_each_invalid_field_is_reported(tmp_path, field, value, needle):
    check = validate_login(**good_input(tmp_path, **{field: value}))
    assert check.profile is None
    assert any(needle in error for error in check.errors)


def test_missing_exe_is_reported(tmp_path):
    check = validate_login(**good_input(tmp_path, exe=str(tmp_path / "nope.exe")))
    assert any("ossutil not found" in error for error in check.errors)


def test_all_problems_are_reported_together(tmp_path):
    check = validate_login(access_key_id="", secret="", region="", bucket="", exe="")
    assert len(check.errors) == 5


def make_profile():
    return Profile("default", "FAKEKEYID12345678", "ap-southeast-5", "bucket-sandbox", r"C:\x\ossutil.exe")


def test_store_roundtrip_and_secret_is_not_in_the_file(tmp_path):
    store = ProfileStore(tmp_path / "profile.json", FakeProtector())
    store.save(make_profile(), SECRET)
    assert store.exists()
    assert store.load() == make_profile()
    assert store.load_secret() == SECRET
    on_disk = (tmp_path / "profile.json").read_text(encoding="utf-8")
    assert SECRET not in on_disk
    assert "secret_enc" in on_disk


def test_load_without_file_raises(tmp_path):
    with pytest.raises(ProfileError, match="missing or damaged"):
        ProfileStore(tmp_path / "none.json", FakeProtector()).load()


def test_corrupt_file_raises(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError):
        ProfileStore(path, FakeProtector()).load_secret()


def test_damaged_secret_field_raises(tmp_path):
    store = ProfileStore(tmp_path / "profile.json", FakeProtector())
    store.save(make_profile(), SECRET)
    path = tmp_path / "profile.json"
    path.write_text(path.read_text(encoding="utf-8").replace("secret_enc", "other"), encoding="utf-8")
    with pytest.raises(ProfileError, match="damaged"):
        store.load_secret()


def test_delete_removes_the_file(tmp_path):
    store = ProfileStore(tmp_path / "profile.json", FakeProtector())
    store.save(make_profile(), SECRET)
    assert store.delete() is True
    assert not store.exists()
    assert store.delete() is False


def test_masked_key_hides_the_middle():
    assert make_profile().masked_key() == "FAKE...5678"
    assert Profile("d", "short", "r", "b", "e").masked_key() == "****"


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
class TestDpapi:
    def test_roundtrip_and_ciphertext_hides_plaintext(self):
        protector = DpapiProtector()
        encrypted = protector.protect(SECRET.encode())
        assert SECRET.encode() not in encrypted
        assert protector.unprotect(encrypted).decode() == SECRET

    def test_tampered_data_fails_cleanly(self):
        protector = DpapiProtector()
        encrypted = bytearray(protector.protect(SECRET.encode()))
        encrypted[len(encrypted) // 2] ^= 0xFF
        with pytest.raises(ProfileError, match="Cannot decrypt"):
            protector.unprotect(bytes(encrypted))

    def test_unicode_and_long_secrets(self):
        protector = DpapiProtector()
        value = ("kunci-法迪-" * 40).encode()
        assert protector.unprotect(protector.protect(value)) == value

    def test_store_with_real_dpapi(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json", DpapiProtector())
        store.save(make_profile(), SECRET)
        assert store.load_secret() == SECRET
        assert SECRET not in (tmp_path / "profile.json").read_text(encoding="utf-8")
import pytest

from ossutil_tui.browser import (
    EntryKind, folder_of, normalize_dir_uri, parent_uri, parse_dir_listing, parse_du, parse_ls_usage, similar_names, split_uri,
)

PREFIX = "oss://b/data/"


def test_split_and_normalize():
    assert split_uri("oss://b/a/c.txt") == ("b", "a/c.txt")
    assert split_uri("oss://b") == ("b", "")
    assert normalize_dir_uri("  oss://b/a/b  ") == "oss://b/a/b/"
    assert normalize_dir_uri("oss://b/a/b") == "oss://b/a/b/"
    assert normalize_dir_uri("oss://b/") == "oss://b/"
    assert normalize_dir_uri("oss://b/test FaDI/") == "oss://b/test FaDI/"
    for bad in ("b/a", "http://b/a", "oss:///a"):
        with pytest.raises(ValueError):
            split_uri(bad)


def test_parent_uri_walks_up_to_the_bucket_root():
    assert parent_uri("oss://b/a/b/c/") == "oss://b/a/b/"
    assert parent_uri("oss://b/a/") == "oss://b/"
    assert parent_uri("oss://b/") is None


def test_folder_of_handles_folders_keys_and_bucket_root():
    assert folder_of("oss://b/p/q/") == "oss://b/p/q/"
    assert folder_of("oss://b/p/q/file.bin") == "oss://b/p/q/"
    assert folder_of("oss://b/file.bin") == "oss://b/"
    assert folder_of("oss://b") == "oss://b/"


LISTING = (
    "oss://b/data/\n"
    "oss://b/data/team-alpha-black/\n"
    "oss://b/data/团队-demo/\n"
    "oss://b/data/test FaDI/\n"
    "oss://b/data/zz-notes.txt\n"
    "Object and Directory Number is: 5\n\n0.2(s) elapsed\n"
)


def test_listing_skips_the_folder_marker_and_splits_dirs_and_files():
    page = parse_dir_listing(LISTING, PREFIX, limit=200)
    assert [(e.kind, e.name) for e in page.entries] == [
        (EntryKind.DIR, "team-alpha-black"),
        (EntryKind.DIR, "团队-demo"),
        (EntryKind.DIR, "test FaDI"),
        (EntryKind.FILE, "zz-notes.txt"),
    ]
    assert page.entries[0].uri == "oss://b/data/team-alpha-black/"
    assert page.total_returned == 5 and page.has_more is False
    assert page.last_key == "data/zz-notes.txt"


def test_has_more_when_the_page_is_full():
    page = parse_dir_listing(LISTING, PREFIX, limit=5)
    assert page.has_more is True


def test_nonexistent_or_empty_prefix():
    page = parse_dir_listing("Object and Directory Number is: 0\n\n0.2(s) elapsed\n", PREFIX, 200)
    assert page.entries == [] and page.total_returned == 0 and page.last_key is None and not page.has_more


def test_folder_with_only_its_marker_exists_but_is_empty():
    page = parse_dir_listing("oss://b/data/\nObject and Directory Number is: 1\n", PREFIX, 200)
    assert page.entries == [] and page.total_returned == 1


def test_parse_du_with_real_format_and_missing_summary():
    text = (
        "Standard      \t60902               \t14521495894902\n"
        "total object count: 60902               \ttotal object sum size: 14521495894902\n"
    )
    assert parse_du(text) == (60902, 14521495894902)
    assert parse_du("Error: AccessDenied") is None


    
def test_parse_recursive_ls_long_format_counts_objects_and_bytes():
    text = (
        "LastModifiedTime                    Size(B)  StorageClass   ETag                                    ObjectName\n"
        "2026-11-06 15:00:00 +0800 CST           0      Standard   ABCD        oss://b/data/session/\n"
        "2026-11-06 15:00:01 +0800 CST         600      Standard   EFGH        oss://b/data/session/a.mcap\n"
        "2026-11-06 15:00:02 +0800 CST         400      Standard   IJKL        oss://b/data/session/b.mcap\n"
        "Object Number is: 3\n"
    )
    assert parse_ls_usage(text) == (3, 1000)


def test_parse_recursive_ls_returns_none_for_errors_or_empty_output():
    assert parse_ls_usage("Error: AccessDenied") is None
    assert parse_ls_usage("Object Number is: 0\n") is None


def test_similar_names_catches_the_real_typo_and_ignores_unrelated():
    existing = ["team-alpha-black", "team-alpha-white", "archive", "_upload_acceptance"]
    assert similar_names("team-alpah-black", existing) == ["team-alpha-black"]
    assert similar_names("something-else", existing) == []
    assert similar_names("team-alpha-black", existing) == []  # an exact match is not a typo
    assert similar_names("TEAM-alpah-black", existing) == ["team-alpha-black"]  # case-insensitive
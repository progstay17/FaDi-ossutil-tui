import asyncio

import pytest

from ossutil_tui.browser import EntryKind
from ossutil_tui.controller import JobError
from tests.fakes import BIG_STAT, make_controller, make_tree_exec


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


def controller(tmp_path, **kwargs):
    return make_controller(tmp_path, exec_fn=make_tree_exec(**kwargs))


def test_list_root_and_a_folder(tmp_path):
    c = controller(tmp_path)
    root = run(c.list_directory("oss://bucket-sandbox/"))
    assert [(e.kind, e.name) for e in root.entries] == [(EntryKind.DIR, "data"), (EntryKind.FILE, "readme.txt")]
    ego = run(c.list_directory("oss://bucket-sandbox/data"))  # no trailing slash is fine
    assert [e.name for e in ego.entries] == ["team-alpha-black", "team-alpha-white", "web-archive", "zz-notes.txt"]


def test_paging_with_start_after_gives_every_entry_once(tmp_path):
    calls = []
    c = controller(tmp_path, calls=calls)
    first = run(c.list_directory("oss://bucket-sandbox/data/", limit=3))
    assert first.has_more and [e.name for e in first.entries] == ["team-alpha-black", "team-alpha-white"]
    second = run(c.list_directory("oss://bucket-sandbox/data/", start_after_key=first.last_key, limit=3))
    assert [e.name for e in second.entries] == ["web-archive", "zz-notes.txt"]
    assert "--start-after" in calls[-1]
    assert not second.has_more


def test_commands_carry_region_and_config_and_never_credentials(tmp_path):
    calls = []
    c = controller(tmp_path, calls=calls)
    run(c.list_directory("oss://bucket-sandbox/"))
    argv = calls[0]
    assert argv[1:3] == ["ls", "oss://bucket-sandbox/"] and "-d" in argv
    assert argv[argv.index("--region") + 1] == "ap-southeast-5"
    assert "--config-file" in argv


def test_prefix_exists(tmp_path):
    c = controller(tmp_path)
    assert run(c.prefix_exists("oss://bucket-sandbox/data/"))
    assert not run(c.prefix_exists("oss://bucket-sandbox/data/team-alpah-black/"))


def test_check_prefix_finds_the_first_missing_folder_and_a_likely_typo(tmp_path):
    c = controller(tmp_path)
    check = run(c.check_prefix("oss://bucket-sandbox/data/team-alpah-black/incoming/20261008/"))
    assert not check.exists
    assert check.missing == "team-alpah-black"
    assert check.ancestor == "oss://bucket-sandbox/data/"
    assert check.similar == ["team-alpha-black"]


def test_check_prefix_existing_deep_path(tmp_path):
    c = controller(tmp_path)
    assert run(c.check_prefix("oss://bucket-sandbox/data/team-alpha-black/incoming/")).exists


def test_destination_warnings_for_a_typo_and_for_a_fine_destination(tmp_path):
    c = controller(tmp_path)
    warnings = run(c.destination_warnings("oss://bucket-sandbox/data/team-alpah-black/incoming/"))
    assert "does not exist yet" in warnings[0]
    assert "team-alpha-black" in warnings[1] and "typo" in warnings[1]
    assert run(c.destination_warnings("oss://bucket-sandbox/data/team-alpha-black/incoming/")) == []
    # a full key checks its parent folder, not the key itself
    assert run(c.destination_warnings("oss://bucket-sandbox/data/team-alpha-black/incoming/file.bin")) == []


def test_destination_in_a_brand_new_folder_has_no_typo_hint(tmp_path):
    c = controller(tmp_path)
    warnings = run(c.destination_warnings("oss://bucket-sandbox/data/totally-new-folder/"))
    assert len(warnings) == 1 and "does not exist yet" in warnings[0]


def test_failures_are_classified_into_readable_errors(tmp_path):
    async def denied(argv, env):
        return 1, "", "Http Status Code: 403. Error Code: AccessDenied."

    c = make_controller(tmp_path, exec_fn=denied)
    with pytest.raises(JobError, match="Access denied"):
        run(c.list_directory("oss://bucket-sandbox/"))


def test_missing_ossutil_and_bad_addresses_are_job_errors(tmp_path):
    async def missing(argv, env):
        raise FileNotFoundError("nope")

    c = make_controller(tmp_path, exec_fn=missing)
    with pytest.raises(JobError, match="Cannot run ossutil"):
        run(c.list_directory("oss://bucket-sandbox/"))
    with pytest.raises(JobError, match="Not a valid oss://"):
        run(c.list_directory("bucket-sandbox/data/"))


def test_folder_usage_accepts_exit_code_1_when_totals_are_present(tmp_path):
    c = controller(tmp_path, du_code=1)
    assert run(c.folder_usage("oss://bucket-sandbox/data/")) == (60902, 14521495894902)


def test_folder_usage_without_totals_raises(tmp_path):
    async def broken(argv, env):
        return 1, "", "Error Code: AccessDenied"

    c = make_controller(tmp_path, exec_fn=broken)
    with pytest.raises(JobError, match="Access denied"):
        run(c.folder_usage("oss://bucket-sandbox/data/"))


def test_stat_object_parses_details_and_wraps_errors(tmp_path):
    c = make_controller(tmp_path, stat_text=BIG_STAT)
    stat = run(c.stat_object("oss://bucket-sandbox/data/zz-notes.txt"))
    assert stat["Content-Length"] == "1073741824" and stat["X-Oss-Hash-Crc64ecma"] == "3534425600523290380"
    broken = make_controller(tmp_path, stat_text=None)
    with pytest.raises(JobError, match="stat not available"):
        run(broken.stat_object("oss://bucket-sandbox/data/zz-notes.txt"))
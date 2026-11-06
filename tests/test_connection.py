import asyncio

import pytest

from ossutil_tui.connection import build_env, check_connection, classify_failure

SECRET = "S3cretValue-123456"


def test_build_env_drops_other_oss_vars_and_sets_ours():
    base = {"PATH": "x", "OSS_REGION": "cn-hangzhou", "oss_endpoint": "e", "KEEP": "1"}
    env = build_env("KEYID123", SECRET, base)
    assert env["PATH"] == "x" and env["KEEP"] == "1"
    assert "OSS_REGION" not in env and "oss_endpoint" not in env
    assert env["OSS_ACCESS_KEY_ID"] == "KEYID123"
    assert env["OSS_ACCESS_KEY_SECRET"] == SECRET


@pytest.mark.parametrize(
    "text, kind",
    [
        ("Http Status Code: 403. Error Code: InvalidAccessKeyId.", "key"),
        ("Error Code: SignatureDoesNotMatch", "key"),
        ("Http Status Code: 404. Error Code: NoSuchBucket.", "bucket"),
        ("Http Status Code: 403. Error Code: AccessDenied. Message: The bucket you access does not belong to you.", "access"),
        ("The bucket you are attempting to access must be addressed using the specified endpoint", "region"),
        ("Error: region must be set in sign version 4.", "region"),
        ("dial tcp: lookup x.oss-oss-ap-southeast-5.aliyuncs.com: no such host", "network"),
        ("net/http: request canceled (Client.Timeout exceeded)", "network"),
    ],
)
def test_classify_failure(text, kind):
    assert classify_failure(text)[0] == kind


def test_classify_unknown_uses_last_line():
    kind, message = classify_failure("first\nsomething odd happened\n")
    assert kind == "unknown" and message == "something odd happened"
    assert classify_failure("")[1].startswith("ossutil failed")


def run(coro):
    return asyncio.run(coro)


def make_call(exec_fn, **overrides):
    args = dict(
        exe="ossutil.exe", config_file="empty.cfg", region="ap-southeast-5", bucket="bucket-sandbox",
        access_key_id="KEYID123", secret=SECRET, exec_fn=exec_fn,
    )
    args.update(overrides)
    return check_connection(**args)


def test_success_and_secret_never_in_argv():
    seen = {}

    async def fake_exec(argv, env):
        seen["argv"], seen["env"] = argv, env
        return 0, "Object Number is: 0", ""

    result = run(make_call(fake_exec))
    assert result.ok and result.kind == "ok"
    argv = seen["argv"]
    assert argv[:3] == ["ossutil.exe", "ls", "oss://bucket-sandbox"]
    assert "--limited-num" in argv and argv[argv.index("--region") + 1] == "ap-southeast-5"
    assert argv[argv.index("--config-file") + 1] == "empty.cfg"
    assert not any(SECRET in part or "KEYID123" in part for part in argv)
    assert seen["env"]["OSS_ACCESS_KEY_SECRET"] == SECRET


def test_failure_is_classified():
    async def fake_exec(argv, env):
        return 1, "", "Error Code: InvalidAccessKeyId"

    result = run(make_call(fake_exec))
    assert not result.ok and result.kind == "key"


def test_cannot_run_exe():
    async def fake_exec(argv, env):
        raise FileNotFoundError("nope")

    result = run(make_call(fake_exec))
    assert result.kind == "exe" and "Cannot run ossutil" in result.message


def test_timeout_is_reported_as_network():
    async def fake_exec(argv, env):
        await asyncio.sleep(5)

    result = run(make_call(fake_exec, timeout=0.05))
    assert result.kind == "network" and "Timed out" in result.message
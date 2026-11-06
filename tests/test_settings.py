from ossutil_tui.settings import Settings


def make(tmp_path, **overrides):
    exe = tmp_path / "ossutil.exe"
    exe.write_text("x")
    cfg = tmp_path / "cfg"
    cfg.write_text("x")
    base = dict(
        exe=str(exe),
        config_file=str(cfg),
        region="oss-ap-southeast-5",
        checkpoint_dir=str(tmp_path / "ck"),
        state_file=str(tmp_path / "q.json"),
        log_dir=str(tmp_path / "logs"),
    )
    base.update(overrides)
    return Settings(**base)


def test_valid_settings_have_no_problems(tmp_path):
    assert make(tmp_path).problems() == []


def test_reports_each_problem(tmp_path):
    problems = make(
        tmp_path, exe=str(tmp_path / "missing.exe"), config_file="nope", region="", checkpoint_dir=" ", part_size=1
    ).problems()
    text = " | ".join(problems)
    assert "executable not found" in text
    assert "config file not found" in text
    assert "region is empty" in text
    assert "checkpoint dir" in text
    assert "part size" in text


def test_region_that_does_not_look_valid(tmp_path):
    assert any("region does not look valid" in p for p in make(tmp_path, region="Jakarta").problems())
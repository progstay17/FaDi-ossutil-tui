import json

import pytest

from ossutil_tui.state import StateCorruptError, load_state, save_state


def test_roundtrip(tmp_path):
    path = tmp_path / "queue.json"
    data = {"jobs": [{"id": "job-1", "name": "FaDi_test_1gb.bin", "status": "UPLOADING"}]}
    save_state(path, data)
    assert load_state(path, default=None) == data


def test_missing_file_returns_default(tmp_path):
    assert load_state(tmp_path / "none.json", default={"jobs": []}) == {"jobs": []}


def test_corrupt_file_raises_instead_of_silently_resetting(tmp_path):
    path = tmp_path / "queue.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateCorruptError):
        load_state(path, default={"jobs": []})


def test_no_temp_files_left_after_save(tmp_path):
    path = tmp_path / "queue.json"
    save_state(path, {"a": 1})
    save_state(path, {"a": 2})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["queue.json"]
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2}


def test_unicode_paths_and_values_survive(tmp_path):
    path = tmp_path / "queue.json"
    data = {"destination": "oss://example-bucket/data/test-demo/"}
    save_state(path, data)
    assert load_state(path, default=None) == data
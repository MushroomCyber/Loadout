import json

from kalitools.manager import _atomic_write_json, _atomic_write_text


def test_atomic_write_json_roundtrip(tmp_path):
    path = tmp_path / "sample.json"
    payload = {"a": 1, "b": [1, 2, 3]}
    _atomic_write_json(path, payload)
    assert json.loads(path.read_text()) == payload


def test_atomic_write_replaces_existing(tmp_path):
    path = tmp_path / "sample.json"
    path.write_text("{}")
    _atomic_write_json(path, {"x": 1})
    assert json.loads(path.read_text()) == {"x": 1}


def test_atomic_write_text(tmp_path):
    path = tmp_path / "sample.txt"
    _atomic_write_text(path, "hello\n")
    assert path.read_text() == "hello\n"

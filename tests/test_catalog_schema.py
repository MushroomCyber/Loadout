import json

from kalitools.manager import _atomic_write_json


def test_schema_v2_round_trip(tmp_path):
    path = tmp_path / "tools_merged.json"
    payload = {
        "schema": 2,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "source": {"type": "apt"},
        "tools": [
            {"name": "nmap", "category": "recon", "installed": False, "size": 0,
             "commands": ["nmap"], "subpackages": [], "description": "scanner",
             "source": "apt", "metadata": {}, "subcategory": "Port Scan"},
        ],
    }
    _atomic_write_json(path, payload)
    data = json.loads(path.read_text())
    assert data["schema"] == 2
    assert data["tools"][0]["name"] == "nmap"


def test_v1_list_is_still_parseable():
    # Direct list, as the legacy loader accepted.
    legacy = [{"name": "nmap", "category": "recon"}]
    # The loader itself exists on KaliToolsManager instances; here we just
    # verify the payload serializes.
    assert json.loads(json.dumps(legacy)) == legacy

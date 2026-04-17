import json

from kalitools import config as config_mod
from kalitools.model import Tool


def test_export_import_round_trip(tmp_path):
    tools = [
        Tool(name="nmap", installed=True, category="recon", commands=["nmap"]),
        Tool(name="hydra", installed=False, category="password"),
    ]
    mgr = config_mod.ConfigManager(tools)
    path = tmp_path / "export.json"
    assert mgr.export_tools_list(str(path))
    data = json.loads(path.read_text())
    assert data["total_tools"] == 1
    assert data["tools"][0]["name"] == "nmap"

    round_trip = mgr.import_tools_list(str(path))
    assert round_trip == ["nmap"]


def test_import_with_installer_callback(tmp_path):
    tools = [Tool(name="nmap", installed=False)]
    mgr = config_mod.ConfigManager(tools)
    src = {"tools": [{"name": "nmap"}, {"name": "unknown-pkg"}]}
    path = tmp_path / "import.json"
    path.write_text(json.dumps(src))

    installed = []

    def fake_installer(pkg: str) -> bool:
        installed.append(pkg)
        return True

    names = mgr.import_tools_list(str(path), installer=fake_installer, assume_yes=True)
    assert names == ["nmap", "unknown-pkg"]
    assert installed == ["nmap"]  # unknown filtered out

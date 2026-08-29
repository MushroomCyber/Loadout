"""The commands that make this more than a package-manager wrapper."""

from __future__ import annotations

import json

import pytest

from loadout.ui.cli import main


@pytest.fixture
def installed_machine(catalog, monkeypatch):
    """A machine with nmap and ffuf installed via apt, from loadout's view."""
    from loadout.providers.apt import AptProvider
    from loadout.providers.base import ProviderStatus

    monkeypatch.setattr(AptProvider, "list_installed", lambda self: {"nmap", "ffuf"})
    monkeypatch.setattr(
        "loadout.providers.available_providers",
        lambda: {"apt": ProviderStatus(name="apt", available=True)},
    )
    monkeypatch.setattr(
        "loadout.catalog.open_catalog", lambda explicit=None: catalog
    )
    return catalog


class TestExport:
    def test_json_lists_installed_only(self, installed_machine, capsys):
        assert main(["export", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert {t["id"] for t in payload["tools"]} == {"nmap", "ffuf"}
        assert payload["count"] == 2

    def test_script_is_a_single_apt_transaction(self, installed_machine, capsys):
        assert main(["export", "--format", "script"]) == 0
        script = capsys.readouterr().out
        assert script.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in script
        assert "DEBIAN_FRONTEND=noninteractive" in script
        assert script.count("apt-get install") == 1, "should batch, not loop"
        assert "nmap" in script and "ffuf" in script

    def test_dockerfile_is_plausible(self, installed_machine, capsys):
        assert main(["export", "--format", "docker"]) == 0
        dockerfile = capsys.readouterr().out
        assert dockerfile.startswith("# Generated")
        assert "FROM kalilinux/kali-rolling" in dockerfile
        assert "--no-install-recommends" in dockerfile
        assert "rm -rf /var/lib/apt/lists/*" in dockerfile

    def test_ansible_has_a_task_list(self, installed_machine, capsys):
        assert main(["export", "--format", "ansible"]) == 0
        playbook = capsys.readouterr().out
        assert "ansible.builtin.apt:" in playbook
        assert "become: true" in playbook

    def test_loadout_format_round_trips(self, installed_machine, capsys, tmp_path):
        import yaml

        assert main(["export", "--format", "loadout"]) == 0
        manifest = yaml.safe_load(capsys.readouterr().out)
        assert sorted(manifest["tools"]) == ["ffuf", "nmap"]

    def test_writes_to_a_file(self, installed_machine, tmp_path):
        target = tmp_path / "install.sh"
        assert main(["export", "--format", "script", "-o", str(target)]) == 0
        assert target.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


class TestReport:
    def test_json_report_carries_an_integrity_digest(self, installed_machine, capsys):
        from loadout.state import get_state_db

        get_state_db().set_installed("nmap", True, provider="apt", version="7.94")
        get_state_db().record("run", "nmap", detail="-sV target")

        assert main(["report", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["integrity"]["algorithm"] == "sha256"
        assert len(payload["integrity"]["digest"]) == 64
        assert payload["host"]["hostname"]

    def test_records_the_version_that_was_used(self, installed_machine, capsys):
        from loadout.state import get_state_db

        db = get_state_db()
        db.set_installed("nmap", True, provider="apt", version="7.94")
        db.record("install", "nmap")
        main(["report", "--format", "json"])
        payload = json.loads(capsys.readouterr().out)
        entry = next(t for t in payload["tools"] if t["tool"] == "nmap")
        assert entry["version"] == "7.94"
        assert entry["provider"] == "apt"

    def test_defaults_to_what_was_used_not_everything_installed(
        self, installed_machine, capsys
    ):
        """A report listing every base-system package buries the four tools
        that matter under hundreds of rows."""
        from loadout.state import get_state_db

        get_state_db().record("run", "nmap")

        assert main(["report", "--format", "json"]) == 0
        used = json.loads(capsys.readouterr().out)
        assert [t["tool"] for t in used["tools"]] == ["nmap"]
        assert used["scope"] == "used in window"

        assert main(["report", "--format", "json", "--all-installed"]) == 0
        every = json.loads(capsys.readouterr().out)
        assert {t["tool"] for t in every["tools"]} == {"nmap", "ffuf"}
        assert every["scope"] == "all installed"

    def test_markdown_is_a_table(self, installed_machine, capsys):
        from loadout.state import get_state_db

        get_state_db().record("install", "nmap")
        assert main(["report", "--format", "markdown"]) == 0
        text = capsys.readouterr().out
        assert "| Tool | Version |" in text
        assert "sha256:" in text

    @pytest.mark.parametrize(
        ("since", "unit"), [("30d", "days"), ("12h", "hours")]
    )
    def test_relative_windows_parse(self, since, unit):
        from loadout.ui.cli import _parse_since

        parsed = _parse_since(since)
        assert parsed and parsed.endswith("+00:00")

    def test_absolute_dates_pass_through(self):
        from loadout.ui.cli import _parse_since

        assert _parse_since("2026-01-01") == "2026-01-01"
        assert _parse_since(None) is None


class TestAudit:
    def test_reports_clean_machine(self, installed_machine, capsys):
        from loadout.state import get_state_db

        for tool_id in ("nmap", "ffuf"):
            get_state_db().set_installed(tool_id, True, provider="apt", version="1.0")
        assert main(["--json", "audit"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["installed"] == 2

    def test_flags_missing_provenance(self, installed_machine, capsys):
        assert main(["--json", "audit"]) == 0
        payload = json.loads(capsys.readouterr().out)
        issues = {f["issue"] for f in payload["findings"]}
        assert "no recorded version" in issues

    def test_flags_unchecksummed_github_methods(self, tmp_path, monkeypatch, capsys):
        from loadout.catalog.store import CatalogStore, build_catalog
        from loadout.model import InstallMethod, Tool
        from loadout.providers.base import ProviderStatus

        risky = Tool(
            id="risky",
            binaries=("risky",),
            install=(InstallMethod(provider="github", spec={"repo": "a/b"}),),
        )
        path = tmp_path / "c.db"
        build_catalog(path, [risky])
        store = CatalogStore(path)

        monkeypatch.setattr("loadout.catalog.open_catalog", lambda explicit=None: store)
        monkeypatch.setattr(
            "loadout.providers.available_providers",
            lambda: {"github": ProviderStatus(name="github", available=True)},
        )
        monkeypatch.setattr(
            "loadout.providers.github.GithubReleaseProvider.list_installed",
            lambda self: {"risky"},
        )
        assert main(["--json", "audit"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert any("publishes no checksum" in f["issue"] for f in payload["findings"])
        store.close()


class TestSyncWorkflow:
    def test_sync_reports_in_sync(self, installed_machine, capsys, tmp_path, monkeypatch):
        from loadout import loadouts

        manifest = loadouts.Loadout(slug="mine", tools=("nmap", "ffuf"))
        manifest.write(tmp_path / "loadout.yaml")
        monkeypatch.chdir(tmp_path)

        assert main(["--json", "sync"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["in_sync"] is True

    def test_sync_plans_the_gap(self, installed_machine, capsys, tmp_path, monkeypatch):
        from loadout import loadouts

        manifest = loadouts.Loadout(slug="mine", tools=("nmap", "ffuf", "masscan"))
        manifest.write(tmp_path / "loadout.yaml")
        monkeypatch.chdir(tmp_path)

        main(["--json", "sync", "--dry-run", "--yes"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["actions"][0]["tool"] == "masscan"

    def test_missing_manifest_explains_how_to_make_one(self, installed_machine, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert main(["sync"]) == 4
        assert "loadout save" in capsys.readouterr().out


class TestAlternatives:
    def test_curated_alternatives_are_used(self, installed_machine, capsys):
        assert main(["--json", "alt", "nmap"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert [t["id"] for t in payload["alternatives"]] == ["masscan"]
        assert payload["inferred"] is False

    def test_uncategorised_tools_do_not_dump_the_whole_catalog(
        self, tmp_path, monkeypatch, capsys
    ):
        """`alt` on an uncurated entry used to return every tool in `other`."""
        from loadout.catalog.store import CatalogStore, build_catalog
        from loadout.model import Tool
        from loadout.providers.base import ProviderStatus

        tools = [Tool(id=f"misc-{i}", categories=("other",)) for i in range(50)]
        path = tmp_path / "c.db"
        build_catalog(path, tools)
        store = CatalogStore(path)
        monkeypatch.setattr("loadout.catalog.open_catalog", lambda explicit=None: store)
        monkeypatch.setattr(
            "loadout.providers.available_providers",
            lambda: {"apt": ProviderStatus(name="apt", available=False)},
        )

        assert main(["--json", "alt", "misc-0"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["alternatives"] == []
        store.close()

    def test_inference_is_capped_and_flagged(self, installed_machine, capsys):
        assert main(["--json", "alt", "nuclei"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["alternatives"]) <= 8

    def test_deprecation_is_surfaced(self, tmp_path, monkeypatch, capsys):
        from loadout.catalog.store import CatalogStore, build_catalog
        from loadout.model import Tool
        from loadout.providers.base import ProviderStatus

        old = Tool(id="dirbuster", categories=("web",), deprecated_by="feroxbuster")
        new = Tool(id="feroxbuster", summary="Recursive content discovery",
                   categories=("web",))
        path = tmp_path / "c.db"
        build_catalog(path, [old, new])
        store = CatalogStore(path)
        monkeypatch.setattr("loadout.catalog.open_catalog", lambda explicit=None: store)
        monkeypatch.setattr(
            "loadout.providers.available_providers",
            lambda: {"apt": ProviderStatus(name="apt", available=False)},
        )

        assert main(["--json", "alt", "dirbuster"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["deprecated_by"] == "feroxbuster"
        store.close()

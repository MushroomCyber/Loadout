"""Loadout manifests, the XDG migration, and the CLI's contract."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from loadout import loadouts
from loadout.ui.cli import main


class TestManifests:
    def test_round_trips(self, tmp_path):
        manifest = loadouts.Loadout(
            slug="ad-ops", name="AD Ops", tools=("netexec", "impacket")
        )
        path = manifest.write(tmp_path / "ad-ops.yaml")
        reloaded = loadouts._load_file(path, "user")
        assert reloaded.slug == "ad-ops"
        assert reloaded.tools == ("netexec", "impacket")

    def test_legacy_packages_key_still_loads(self, tmp_path):
        """kalitools profiles used `packages:`; they must keep working."""
        path = tmp_path / "old.yaml"
        path.write_text("slug: old\npackages: [nmap, ffuf]\n", encoding="utf-8")
        loaded = loadouts._load_file(path, "user")
        assert loaded.tools == ("nmap", "ffuf")

    def test_duplicates_and_case_are_normalised(self):
        manifest = loadouts.Loadout(slug="x", tools=("NMAP", "nmap", " ffuf "))
        assert manifest.tools == ("nmap", "ffuf")

    def test_bundled_loadouts_all_parse(self):
        found = loadouts.load_all()
        assert "pentester-web" in found
        assert "dfir-responder" in found, "the widened scope needs a blue-team loadout"
        for manifest in found.values():
            assert manifest.tools, f"{manifest.slug} is empty"


class TestDiff:
    def test_reports_missing_present_and_unknown(self, catalog):
        manifest = loadouts.Loadout(slug="t", tools=("nmap", "ffuf", "nonexistent"))
        result = loadouts.diff(manifest, catalog=catalog, installed={"nmap"})
        assert result.present == ["nmap"]
        assert result.missing == ["ffuf"]
        assert result.unknown == ["nonexistent"]
        assert not result.in_sync

    def test_in_sync_when_everything_present(self, catalog):
        manifest = loadouts.Loadout(slug="t", tools=("nmap",))
        result = loadouts.diff(manifest, catalog=catalog, installed={"nmap"})
        assert result.in_sync

    def test_extra_lists_what_is_not_declared(self, catalog):
        manifest = loadouts.Loadout(slug="t", tools=("nmap",))
        result = loadouts.diff(manifest, catalog=catalog, installed={"nmap", "masscan"})
        assert result.extra == ["masscan"]

    def test_snapshot_round_trips_through_diff(self, catalog):
        snapshot = loadouts.from_installed("snap", ["nmap", "ffuf"])
        result = loadouts.diff(snapshot, catalog=catalog, installed={"nmap", "ffuf"})
        assert result.in_sync


class TestLegacyMigration:
    def test_detects_kalitools_files(self, tmp_path, monkeypatch):
        from loadout.paths import needs_migration

        home = tmp_path / "home"
        (home / ".kali_tools_settings.json").write_text('{"per_page": 40}', encoding="utf-8")
        assert needs_migration() is True

    def test_imports_without_deleting_anything(self, tmp_path):
        from loadout.paths import migrate_legacy_state

        home = tmp_path / "home"
        settings = home / ".kali_tools_settings.json"
        settings.write_text('{"per_page": 40}', encoding="utf-8")
        (home / ".kali_tools_cache.json").write_text(
            '{"nmap": true, "ffuf": false}', encoding="utf-8"
        )
        (home / ".kali_tools_local_repo.txt").write_text("/srv/mirror", encoding="utf-8")

        report = migrate_legacy_state()
        assert report.settings == {"per_page": 40}
        assert report.installed == ["nmap"]
        assert report.local_repo == "/srv/mirror"
        assert settings.exists(), "migration must copy, never delete"

    def test_runs_only_once(self, tmp_path):
        from loadout.paths import migrate_legacy_state, needs_migration

        (tmp_path / "home" / ".kali_tools_settings.json").write_text("{}", encoding="utf-8")
        migrate_legacy_state()
        assert needs_migration() is False
        assert migrate_legacy_state().ran is False

    def test_state_db_schema_v1_is_upgraded(self, tmp_path):
        """A kalitools state.db keeps its stars and history."""
        import sqlite3

        from loadout.state import StateDB

        path = tmp_path / "state.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE tool_state (
                name TEXT PRIMARY KEY, installed INTEGER DEFAULT 0,
                last_used TEXT, starred INTEGER DEFAULT 0, user_notes TEXT);
            CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, action TEXT,
                package TEXT, success INTEGER, detail TEXT);
            INSERT INTO tool_state VALUES ('nmap', 1, '2026-01-01', 1, 'my note');
            INSERT INTO history (ts, action, package, success, detail)
                VALUES ('2026-01-01', 'install', 'nmap', 1, '');
            """
        )
        conn.commit()
        conn.close()

        database = StateDB(path)
        state = database.get("nmap")
        assert state is not None
        assert state["starred"] == 1
        assert state["notes"] == "my note"
        assert database.history()[0]["tool_id"] == "nmap"


class TestStateSync:
    def test_records_provider_and_version(self, tmp_path):
        from loadout.state import StateDB

        database = StateDB(tmp_path / "s.db")
        database.set_installed("ffuf", True, provider="go", version="2.1.0")
        state = database.get("ffuf")
        assert state["provider"] == "go"
        assert state["version"] == "2.1.0"

    def test_later_writes_do_not_erase_provenance(self, tmp_path):
        from loadout.state import StateDB

        database = StateDB(tmp_path / "s.db")
        database.set_installed("ffuf", True, provider="go", version="2.1.0")
        database.set_installed("ffuf", True)
        state = database.get("ffuf")
        assert state["provider"] == "go"
        assert state["version"] == "2.1.0"

    def test_history_window_filtering(self, tmp_path):
        from loadout.state import StateDB

        database = StateDB(tmp_path / "s.db")
        database.record("install", "nmap")
        database.record("run", "ffuf")
        assert len(database.history(actions=["install"])) == 1
        assert len(database.history(tool_id="ffuf")) == 1


class TestCliContract:
    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "loadout", *argv],
            capture_output=True,
            text=True,
            timeout=90,
        )

    def test_help_exits_clean(self):
        result = self._run("--help")
        assert result.returncode == 0
        assert "loadout" in result.stdout
        assert "sync" in result.stdout
        assert "Traceback" not in result.stderr

    def test_version(self):
        result = self._run("--version")
        assert result.returncode == 0
        assert result.stdout.strip().startswith("loadout ")

    def test_no_traceback_on_unknown_tool(self, capsys):
        code = main(["show", "definitely-not-a-tool", "--json"])
        assert code == 4
        payload = json.loads(capsys.readouterr().out)
        assert "Unknown tool" in payload["error"]

    def test_json_search_is_parseable(self, capsys):
        assert main(["--json", "search", "nmap"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert isinstance(payload, list)

    def test_json_catalog_info_shape(self, capsys):
        assert main(["--json", "catalog", "info"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["tools"] > 0
        assert "categories" in payload

    def test_dry_run_json_emits_a_plan(self, capsys):
        main(["--json", "install", "nmap", "--dry-run", "--yes"])
        payload = json.loads(capsys.readouterr().out)
        assert "actions" in payload or "results" in payload

    def test_providers_json_names_the_platform(self, capsys):
        assert main(["--json", "providers"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["distro"]
        assert any(p["provider"] == "apt" for p in payload["providers"])

    def test_doctor_never_crashes(self, capsys):
        code = main(["--json", "doctor"])
        assert code in (0, 2)
        payload = json.loads(capsys.readouterr().out)
        assert all("severity" in check for check in payload)

    def test_errors_go_to_stdout_as_json_not_a_traceback(self, capsys):
        code = main(["--json", "loadout", "show", "no-such-loadout"])
        assert code == 4
        assert "Traceback" not in capsys.readouterr().err

    @pytest.mark.parametrize("command", ["list", "categories", "providers", "phase"])
    def test_read_only_commands_succeed(self, command, capsys):
        assert main(["--json", command]) == 0
        json.loads(capsys.readouterr().out)

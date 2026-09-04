"""Loadout manifests and the CLI's contract."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from loadout import loadouts
from loadout.ui.cli import main

NL = chr(10)


class TestManifests:
    def test_round_trips(self, tmp_path):
        manifest = loadouts.Loadout(
            slug="ad-ops", name="AD Ops", tools=("netexec", "impacket")
        )
        path = manifest.write(tmp_path / "ad-ops.yaml")
        reloaded = loadouts._load_file(path, "user")
        assert reloaded.slug == "ad-ops"
        assert reloaded.tools == ("netexec", "impacket")

    def test_missing_tools_key_is_an_empty_manifest(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("slug: empty\n", encoding="utf-8")
        loaded = loadouts._load_file(path, "user")
        assert loaded.tools == ()

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


def _git(repo, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def _self_update_checkout(tmp_path):
    """A local remote+clone pair standing in for Loadout's own git checkout,
    so this exercises the `self-update` command's plumbing without depending
    on -- or mutating -- the real repository this test suite runs from."""
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "--quiet", "--initial-branch=main")
    _git(remote, "config", "user.email", "test@example.com")
    _git(remote, "config", "user.name", "Test")
    (remote / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    _git(remote, "add", ".")
    _git(remote, "commit", "--quiet", "-m", "initial")

    local = tmp_path / "local"
    subprocess.run(
        ["git", "clone", "--quiet", str(remote), str(local)], check=True, capture_output=True
    )
    _git(local, "config", "user.email", "test@example.com")
    _git(local, "config", "user.name", "Test")
    return remote, local


class TestSelfUpdateCLI:
    def test_reports_no_git_checkout_cleanly(self, capsys, monkeypatch):
        from loadout import selfupdate

        monkeypatch.setattr(selfupdate, "find_repo_root", lambda: None)
        assert main(["self-update", "--check"]) == 1
        assert "Traceback" not in capsys.readouterr().err

    def test_check_reports_up_to_date_without_prompting(self, tmp_path, capsys, monkeypatch):
        from loadout import selfupdate

        _remote, local = _self_update_checkout(tmp_path)
        monkeypatch.setattr(selfupdate, "find_repo_root", lambda: local)

        assert main(["--json", "self-update", "--check"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["up_to_date"] is True

    def test_yes_pulls_a_fast_forward_update(self, tmp_path, capsys, monkeypatch):
        from loadout import selfupdate

        remote, local = _self_update_checkout(tmp_path)
        (remote / "code.py").write_text("x = 1\n", encoding="utf-8")
        _git(remote, "add", ".")
        _git(remote, "commit", "--quiet", "-m", "add code")
        monkeypatch.setattr(selfupdate, "find_repo_root", lambda: local)

        assert main(["--json", "self-update", "--yes"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["old_commit"] != payload["new_commit"]
        assert (local / "code.py").exists()

    def test_without_yes_and_no_tty_aborts_rather_than_hangs(
        self, tmp_path, capsys, monkeypatch
    ):
        from loadout import selfupdate

        remote, local = _self_update_checkout(tmp_path)
        (remote / "code.py").write_text("x = 1\n", encoding="utf-8")
        _git(remote, "add", ".")
        _git(remote, "commit", "--quiet", "-m", "add code")
        monkeypatch.setattr(selfupdate, "find_repo_root", lambda: local)

        assert main(["self-update"]) == 130
        assert "Traceback" not in capsys.readouterr().err


class TestRunningContent:
    """`loadout run seclists` is a category error, and it used to be reported
    as a catalog defect -- "no known binary ... add a `binaries:` field" --
    which is advice that would make the entry wrong."""

    @pytest.fixture
    def content_catalog(self, tmp_path, monkeypatch):
        from loadout.catalog.compile import build_catalog
        from loadout.catalog.store import CatalogStore
        from loadout.model import Tool

        path = tmp_path / "content.db"
        build_catalog(
            path,
            [
                Tool.from_dict(
                    {
                        "id": "seclists",
                        "kind": "content",
                        "summary": "Wordlists",
                        "paths": ["/usr/share/seclists"],
                    }
                )
            ],
            source="test",
        )
        store = CatalogStore(path)
        monkeypatch.setattr("loadout.catalog.open_catalog", lambda explicit=None: store)
        yield store
        store.close()

    def test_running_content_says_what_it_is_rather_than_blaming_the_catalog(
        self, content_catalog, capsys
    ):
        assert main(["run", "seclists"]) == 4
        output = capsys.readouterr().out + capsys.readouterr().err
        assert "nothing to run" in output

    def test_it_points_at_where_the_files_actually_are(self, content_catalog, capsys):
        main(["run", "seclists"])
        combined = capsys.readouterr()
        assert "/usr/share/seclists" in (combined.out + combined.err)


@pytest.fixture
def machine(catalog, monkeypatch, tmp_path):
    """A box with nmap installed, and a working directory of its own."""
    from loadout.providers.apt import AptProvider
    from loadout.providers.base import ProviderStatus

    monkeypatch.setattr(AptProvider, "list_installed", lambda self: {"nmap"})
    monkeypatch.setattr(
        "loadout.providers.available_providers",
        lambda: {"apt": ProviderStatus(name="apt", available=True)},
    )
    monkeypatch.setattr("loadout.catalog.open_catalog", lambda explicit=None: catalog)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestTheCatalogCommand:
    def test_info_reports_what_catalog_is_actually_in_use(self, machine, capsys):
        """Which catalog answered is the first question when a tool is
        missing, and the shipped one and a refreshed one can differ."""
        assert main(["catalog", "info", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["tools"] > 0
        assert payload["path"].endswith(".db")
        assert payload["categories"]

    def test_validate_accepts_a_good_source_tree(self, machine, capsys, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "x.yaml").write_text(
            NL.join(["id: x", "summary: a tool", "binaries: [x]", ""]), encoding="utf-8"
        )
        assert main(["catalog", "validate", "--source", str(source)]) == 0

    def test_validate_rejects_a_broken_entry_and_says_why(
        self, machine, capsys, tmp_path
    ):
        source = tmp_path / "src"
        source.mkdir()
        (source / "bad.yaml").write_text("id: Bad Name" + NL, encoding="utf-8")
        assert main(["catalog", "validate", "--source", str(source)]) != 0
        assert "must match" in capsys.readouterr().out

    def test_build_writes_a_catalog_that_can_be_opened(self, machine, tmp_path):
        from loadout.catalog.store import CatalogStore

        source = tmp_path / "src"
        source.mkdir()
        (source / "x.yaml").write_text(
            NL.join(["id: x", "summary: a tool", "binaries: [x]", ""]), encoding="utf-8"
        )
        target = tmp_path / "built.db"
        assert main(
            ["catalog", "build", "--source", str(source), "--output", str(target)]
        ) == 0
        with CatalogStore(target) as store:
            assert store.get("x") is not None


class TestTheLoadoutCommand:
    def test_list_names_the_bundled_kits(self, machine, capsys):
        assert main(["loadout", "list", "--json"]) == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows
        assert {"slug", "name", "tools", "source"} <= set(rows[0])

    def test_save_captures_what_is_installed(self, machine, capsys, tmp_path):
        target = tmp_path / "kit.yaml"
        assert main(["loadout", "save", "mykit", "--output", str(target)]) == 0
        assert "nmap" in target.read_text(encoding="utf-8")

    def test_diff_falls_back_to_the_project_manifest(self, machine, capsys):
        """The slug is declared optional, so omitting it has to mean
        something -- it used to mean "No loadout named None"."""
        (machine / "loadout.yaml").write_text(
            NL.join(["slug: kit", "tools:", "- nmap", "- ffuf", ""]), encoding="utf-8"
        )
        assert main(["loadout", "diff", "--json"]) in (0, 1)
        payload = json.loads(capsys.readouterr().out)
        assert "ffuf" in payload["missing"]
        assert "nmap" in payload["present"]

    def test_an_unknown_loadout_is_refused(self, machine, capsys):
        assert main(["loadout", "show", "not-a-real-kit"]) != 0


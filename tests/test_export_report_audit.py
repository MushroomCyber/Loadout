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


class TestTheReportNamesWhatArrivedUnchecked:
    """The question a challenged finding asks: could that binary have been
    something else? A state row cannot answer it -- it holds one row per tool,
    overwritten by the next install."""

    def _install(self, tool_id, detail):
        from loadout.state import get_state_db

        db = get_state_db()
        db.set_installed(tool_id, True, provider="github", version="1.0")
        db.record("install", tool_id, detail=detail)

    def test_a_bypassed_check_is_named_and_says_what_permitted_it(
        self, installed_machine, capsys
    ):
        self._install("nmap", "provider=github verify=none allow_unverified=yes")
        assert main(["report", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)

        assert payload["unverified"] == ["nmap"]
        entry = next(t for t in payload["tools"] if t["tool"] == "nmap")
        assert entry["verification"]["verified"] is False
        assert entry["verification"]["allow_unverified"] is True

    def test_a_passing_check_records_which_method_passed(
        self, installed_machine, capsys
    ):
        self._install("nmap", "provider=github verify=gpg")
        assert main(["report", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)

        assert payload["unverified"] == []
        entry = next(t for t in payload["tools"] if t["tool"] == "nmap")
        assert entry["verification"] == {
            "method": "gpg",
            "verified": True,
            "checkable": True,
            "allow_unverified": False,
            "source": "history",
        }

    def test_a_provider_with_no_check_of_ours_is_not_called_unverified(
        self, installed_machine, capsys
    ):
        """apt verifies its own package signatures. Listing it beside a real
        bypass would bury the one that matters."""
        self._install("nmap", "provider=apt verify=n/a")
        assert main(["report", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)

        assert payload["unverified"] == []
        entry = next(t for t in payload["tools"] if t["tool"] == "nmap")
        assert entry["verification"]["checkable"] is False
        assert entry["verification"]["method"] == ""

    def test_the_newest_install_is_what_the_report_states(
        self, installed_machine, capsys
    ):
        """Reinstalling with a checksum published clears the earlier bypass."""
        self._install("nmap", "provider=github verify=none allow_unverified=yes")
        self._install("nmap", "provider=github verify=checksum")

        assert main(["report", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["unverified"] == []

    def test_an_install_recorded_before_this_existed_falls_back_to_state(
        self, installed_machine, capsys
    ):
        from loadout.state import get_state_db

        db = get_state_db()
        db.set_installed(
            "nmap", True, provider="github", version="1.0",
            verification=("checksum", True),
        )
        db.record("install", "nmap", detail="provider=github elapsed=1.0s version=1.0")

        assert main(["report", "--format", "json"]) == 0
        entry = next(
            t
            for t in json.loads(capsys.readouterr().out)["tools"]
            if t["tool"] == "nmap"
        )
        assert entry["verification"]["source"] == "state"
        assert entry["verification"]["verified"] is True

    def test_nothing_recorded_at_all_claims_nothing(self, installed_machine, capsys):
        from loadout.state import get_state_db

        get_state_db().record("run", "nmap")
        assert main(["report", "--format", "json"]) == 0
        entry = next(
            t
            for t in json.loads(capsys.readouterr().out)["tools"]
            if t["tool"] == "nmap"
        )
        assert entry["verification"]["source"] == "unrecorded"
        assert entry["verification"]["allow_unverified"] is False

    def test_nothing_recorded_does_not_read_as_nothing_to_check(
        self, installed_machine, capsys
    ):
        """`n/a` says apt verified it itself. An older state file knows neither
        that nor the opposite, and must not borrow the reassuring one."""
        from loadout.ui.cli import _verify_cell

        unrecorded = {
            "method": "", "verified": False, "checkable": False,
            "allow_unverified": False, "source": "unrecorded",
        }
        assert _verify_cell(unrecorded) == "—"
        assert _verify_cell({**unrecorded, "source": "history"}) == "n/a"

    def test_the_text_report_calls_out_the_bypass_rather_than_hiding_it_in_a_column(
        self, installed_machine, capsys
    ):
        self._install("nmap", "provider=github verify=none allow_unverified=yes")
        assert main(["report"]) == 0
        text = capsys.readouterr().out
        assert "Installed without verification (1):" in text
        assert "--allow-unverified" in text

    def test_the_markdown_report_has_a_verified_column(self, installed_machine, capsys):
        self._install("nmap", "provider=github verify=checksum")
        assert main(["report", "--format", "markdown"]) == 0
        text = capsys.readouterr().out
        assert "| Verified |" in text
        assert "| checksum " in text
        assert "Installed without verification" not in text


class TestTheExecutorWritesTheAuditFact:
    def test_a_passing_check_names_its_method(self):
        from loadout.executor import verification_detail

        detail = verification_detail([("checksum", True)], allow_unverified=False)
        assert detail == "verify=checksum"

    def test_a_bypass_records_the_flag_that_allowed_it(self):
        from loadout.executor import verification_detail

        detail = verification_detail([("checksum", False)], allow_unverified=True)
        assert detail == "verify=none allow_unverified=yes"

    def test_no_check_of_ours_is_distinct_from_a_check_that_found_nothing(self):
        from loadout.executor import verification_detail

        assert verification_detail([], allow_unverified=True) == "verify=n/a"
        assert verification_detail([], allow_unverified=False) == "verify=n/a"

    def test_the_flag_is_not_claimed_when_it_changed_nothing(self):
        """Passing --allow-unverified and then verifying anyway is not a bypass;
        recording it as one would put clean installs in the report's list."""
        from loadout.executor import verification_detail

        detail = verification_detail([("gpg", True)], allow_unverified=True)
        assert detail == "verify=gpg"

    def test_the_fact_reaches_the_history_row(self, catalog, tmp_path):
        from loadout.executor import ExecContext, Executor
        from loadout.model import InstallMethod
        from loadout.planner import PlannedAction
        from loadout.state import StateDB

        state = StateDB(tmp_path / "state.db")
        executor = Executor(state=state, allow_unverified=True)
        action = PlannedAction(
            tool=catalog.get("nmap"),
            action="install",
            provider="github",
            method=InstallMethod(provider="github", spec={"repo": "x/y"}),
        )
        context = ExecContext(emit=lambda event: None, allow_unverified=True)
        context.verified("checksum", False)

        executor._record(action, 1.0, context)

        row = state.history(tool_id="nmap")[0]
        assert "allow_unverified=yes" in row["detail"]
        assert "provider=github" in row["detail"]


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

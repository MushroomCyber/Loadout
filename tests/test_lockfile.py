"""`loadout.lock` -- recording what a loadout resolved to, and detecting drift.

A loadout names tool ids with no versions, so applying one twice six months
apart builds two different boxes. These cover the part that makes the
difference visible rather than assumed.
"""

from __future__ import annotations

import json

import pytest

from loadout.lockfile import (
    DRIFT_MISSING,
    DRIFT_PROVIDER,
    DRIFT_UNKNOWN,
    DRIFT_UNLOCKED,
    DRIFT_VERSION,
    Lock,
    LockEntry,
    capture,
    compare,
)


def state(**tools):
    """A `state.all_state()` shaped dict: {id: {installed, provider, version}}."""
    out = {}
    for tool_id, spec in tools.items():
        provider, _, version = spec.partition("@")
        out[tool_id] = {
            "tool_id": tool_id,
            "installed": 1,
            "provider": provider,
            "version": version,
            "verify_method": "checksum",
            "verify_ok": 1,
        }
    return out


class TestCapturing:
    def test_it_records_provider_and_version_per_tool(self):
        lock = capture("web", ["nmap", "ffuf"], state(nmap="apt@7.94", ffuf="go@2.1.0"))
        assert lock.entries["nmap"].provider == "apt"
        assert lock.entries["ffuf"].version == "2.1.0"
        assert lock.slug == "web"

    def test_a_tool_that_is_not_installed_is_not_locked(self):
        """Locking it would write a pin nothing verified."""
        lock = capture("web", ["nmap", "absent"], state(nmap="apt@7.94"))
        assert set(lock.entries) == {"nmap"}

    def test_a_tool_marked_uninstalled_is_not_locked(self):
        rows = state(nmap="apt@7.94")
        rows["nmap"]["installed"] = 0
        assert capture("web", ["nmap"], rows).entries == {}

    def test_ids_are_normalised_and_deduplicated(self):
        lock = capture("web", ["NMAP", "nmap", " nmap "], state(nmap="apt@7.94"))
        assert list(lock.entries) == ["nmap"]

    def test_it_records_how_the_download_was_checked(self):
        lock = capture("web", ["nmap"], state(nmap="apt@7.94"))
        assert lock.entries["nmap"].verify_method == "checksum"
        assert lock.entries["nmap"].verify_ok is True


class TestRoundTrip:
    def test_a_lock_survives_write_and_read(self, tmp_path):
        original = capture("web", ["nmap", "ffuf"], state(nmap="apt@7.94", ffuf="go@2.1"))
        path = original.write(tmp_path / "loadout.lock")
        reloaded = Lock.read(path)
        assert reloaded.slug == "web"
        assert reloaded.entries["ffuf"].version == "2.1"
        assert reloaded.entries["nmap"].provider == "apt"

    def test_tools_are_written_in_sorted_order(self, tmp_path):
        """This file is diffed in code review; key order must not churn."""
        lock = capture("web", ["zsteg", "amass", "nmap"],
                       state(zsteg="gem@1", amass="apt@2", nmap="apt@3"))
        path = lock.write(tmp_path / "loadout.lock")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert list(payload["tools"]) == ["amass", "nmap", "zsteg"]

    def test_the_file_ends_with_a_newline(self, tmp_path):
        path = capture("w", ["nmap"], state(nmap="apt@1")).write(tmp_path / "l.lock")
        assert path.read_text(encoding="utf-8").endswith("}\n")

    def test_a_failed_verification_is_not_recorded_as_verified(self, tmp_path):
        rows = state(nmap="apt@7.94")
        rows["nmap"]["verify_ok"] = 0
        payload = capture("w", ["nmap"], rows).to_dict()
        assert payload["tools"]["nmap"]["verified"] == ""

    def test_garbage_is_rejected_rather_than_half_read(self):
        with pytest.raises(ValueError):
            Lock.from_dict(["not", "an", "object"])
        with pytest.raises(ValueError):
            Lock.from_dict({"tools": ["nmap"]})


class TestComparing:
    def _lock(self):
        return capture("web", ["nmap", "ffuf"], state(nmap="apt@7.94", ffuf="go@2.1.0"))

    def test_an_identical_machine_reports_no_drift(self):
        rows = state(nmap="apt@7.94", ffuf="go@2.1.0")
        assert compare(self._lock(), rows) == []

    def test_a_different_version_is_named_with_both_sides(self):
        rows = state(nmap="apt@7.95", ffuf="go@2.1.0")
        drift = compare(self._lock(), rows)
        assert [d.kind for d in drift] == [DRIFT_VERSION]
        assert drift[0].expected == "7.94"
        assert drift[0].actual == "7.95"

    def test_a_tool_from_the_lock_that_is_gone_is_missing(self):
        rows = state(ffuf="go@2.1.0")
        drift = compare(self._lock(), rows)
        assert [(d.tool_id, d.kind) for d in drift] == [("nmap", DRIFT_MISSING)]

    def test_the_same_version_from_another_provider_still_drifts(self):
        """apt's build and a release archive's are not interchangeable."""
        rows = state(nmap="brew@7.94", ffuf="go@2.1.0")
        drift = compare(self._lock(), rows)
        assert [d.kind for d in drift] == [DRIFT_PROVIDER]
        assert drift[0].actual == "brew"

    def test_an_unrecorded_version_is_unknown_not_a_match(self):
        """"We do not know" must never render as "it agrees"."""
        rows = state(nmap="apt@", ffuf="go@2.1.0")
        drift = compare(self._lock(), rows)
        assert [d.kind for d in drift] == [DRIFT_UNKNOWN]

    def test_something_installed_that_the_lock_never_mentioned(self):
        rows = state(nmap="apt@7.94", ffuf="go@2.1.0", extra="apt@1.0")
        drift = compare(self._lock(), rows)
        assert [(d.tool_id, d.kind) for d in drift] == [("extra", DRIFT_UNLOCKED)]

    def test_the_caller_may_narrow_what_counts_as_installed(self):
        """`sync` compares against the loadout's tools; every other package on
        a Kali box would otherwise report as unlocked and bury the real drift."""
        rows = state(nmap="apt@7.94", ffuf="go@2.1.0", unrelated="apt@9")
        assert compare(self._lock(), rows, installed={"nmap", "ffuf"}) == []

    def test_drift_is_ordered_by_tool_id(self):
        lock = capture("w", ["a", "b", "c"], state(a="apt@1", b="apt@1", c="apt@1"))
        drift = compare(lock, state(a="apt@2", b="apt@2", c="apt@2"))
        assert [d.tool_id for d in drift] == ["a", "b", "c"]


class TestTheCommand:
    @pytest.fixture
    def machine(self, catalog, monkeypatch, tmp_path):
        from loadout.providers.apt import AptProvider
        from loadout.providers.base import ProviderStatus

        monkeypatch.setattr(AptProvider, "list_installed", lambda self: {"nmap"})
        monkeypatch.setattr(
            "loadout.providers.available_providers",
            lambda: {"apt": ProviderStatus(name="apt", available=True)},
        )
        monkeypatch.setattr("loadout.catalog.open_catalog", lambda explicit=None: catalog)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "loadout.yaml").write_text(
            "slug: kit\nname: Kit\ntools:\n- nmap\n", encoding="utf-8"
        )
        return tmp_path

    def test_lock_writes_a_file_from_the_project_manifest(self, machine, capsys):
        from loadout.state import get_state_db
        from loadout.ui.cli import main

        get_state_db().set_installed("nmap", True, provider="apt", version="7.94")
        assert main(["lock"]) == 0
        payload = json.loads((machine / "loadout.lock").read_text(encoding="utf-8"))
        assert payload["tools"]["nmap"]["version"] == "7.94"
        assert payload["slug"] == "kit"

    def test_check_passes_when_the_machine_matches(self, machine, capsys):
        from loadout.state import get_state_db
        from loadout.ui.cli import main

        get_state_db().set_installed("nmap", True, provider="apt", version="7.94")
        main(["lock"])
        capsys.readouterr()
        assert main(["lock", "--check"]) == 0

    def test_check_fails_and_names_the_drift(self, machine, capsys):
        from loadout.state import get_state_db
        from loadout.ui.cli import main

        db = get_state_db()
        db.set_installed("nmap", True, provider="apt", version="7.94")
        main(["lock"])
        capsys.readouterr()

        db.set_installed("nmap", True, provider="apt", version="7.95")
        assert main(["lock", "--check"]) == 1
        output = capsys.readouterr().out
        assert "nmap" in output
        assert "7.94" in output and "7.95" in output

    def test_check_without_a_lockfile_says_how_to_make_one(self, machine, capsys):
        from loadout.ui.cli import main

        assert main(["lock", "--check"]) == 4
        assert "loadout lock" in capsys.readouterr().out

    def test_locking_a_machine_with_nothing_installed_refuses(self, machine, capsys):
        """A lock records what a machine has, not what it should have."""
        from loadout.ui.cli import main

        assert main(["lock"]) == 4
        assert not (machine / "loadout.lock").exists()

    def test_json_mode_reports_drift_machine_readably(self, machine, capsys):
        from loadout.state import get_state_db
        from loadout.ui.cli import main

        db = get_state_db()
        db.set_installed("nmap", True, provider="apt", version="7.94")
        main(["lock"])
        capsys.readouterr()
        db.set_installed("nmap", True, provider="apt", version="7.95")

        assert main(["lock", "--check", "--json"]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["in_sync"] is False
        assert payload["drift"][0]["tool"] == "nmap"
        assert payload["drift"][0]["drift"] == DRIFT_VERSION

    def test_an_unknown_slug_is_refused(self, machine, capsys):
        from loadout.ui.cli import main

        assert main(["lock", "not-a-loadout"]) == 4


def test_a_lock_entry_without_verification_round_trips():
    entry = LockEntry(tool_id="x", provider="apt", version="1.0")
    assert "verified" not in entry.to_dict()
    assert LockEntry.from_dict("x", entry.to_dict()).verify_ok is False


class TestTheVersionAGithubInstallResolved:
    """Nothing on disk records which release tag a binary came from, so
    `installed_version()` could only answer "the binary exists" -- leaving the
    lock with nothing to pin for the provider where pinning matters most."""

    def test_the_resolved_tag_is_what_gets_recorded(self, catalog, tmp_path):
        from loadout.executor import ExecContext, Executor
        from loadout.model import InstallMethod
        from loadout.planner import PlannedAction
        from loadout.state import StateDB

        state = StateDB(tmp_path / "state.db")
        executor = Executor(state=state)
        action = PlannedAction(
            tool=catalog.get("ffuf"),
            action="install",
            provider="github",
            method=InstallMethod(provider="github", spec={"repo": "ffuf/ffuf"}),
        )
        context = ExecContext(emit=lambda event: None)
        context.installed_version = "v2.1.0"

        executor._record(action, 1.0, context)

        assert state.all_state()["ffuf"]["version"] == "v2.1.0"

    def test_it_beats_the_providers_own_guess(self, catalog, tmp_path, monkeypatch):
        """The provider would answer "" for any binary on PATH, which would
        overwrite a real tag with nothing."""
        from loadout.executor import ExecContext, Executor
        from loadout.model import InstallMethod
        from loadout.planner import PlannedAction
        from loadout.providers.github import GithubReleaseProvider
        from loadout.state import StateDB

        monkeypatch.setattr(
            GithubReleaseProvider, "installed_version", lambda self, tool, method: ""
        )
        state = StateDB(tmp_path / "state.db")
        action = PlannedAction(
            tool=catalog.get("ffuf"),
            action="install",
            provider="github",
            method=InstallMethod(provider="github", spec={"repo": "ffuf/ffuf"}),
        )
        context = ExecContext(emit=lambda event: None)
        context.installed_version = "v2.1.0"

        Executor(state=state)._record(action, 1.0, context)
        assert state.all_state()["ffuf"]["version"] == "v2.1.0"

    def test_a_provider_that_does_know_is_still_used(self, catalog, tmp_path):
        """apt reports a real version; nothing here should displace it."""
        from loadout.executor import ExecContext, Executor
        from loadout.model import InstallMethod
        from loadout.planner import PlannedAction
        from loadout.state import StateDB

        state = StateDB(tmp_path / "state.db")
        action = PlannedAction(
            tool=catalog.get("nmap"),
            action="install",
            provider="apt",
            method=InstallMethod(provider="apt", spec={"package": "nmap"}),
        )
        context = ExecContext(emit=lambda event: None)  # provider left to answer

        Executor(state=state)._record(action, 1.0, context)
        assert "nmap" in state.all_state()


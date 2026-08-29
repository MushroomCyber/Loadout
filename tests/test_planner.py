"""Planning is pure, so the whole resolution layer is testable without a box.

These assert on *planned argv*, which is the point of separating planning from
execution: the previous release could not test its install path at all because
constructing the manager touched the network, the filesystem and dpkg.
"""

from __future__ import annotations

import pytest

from loadout.errors import NoViableProvider, ToolNotFound
from loadout.planner import ACTION_INSTALL, ACTION_REMOVE, Planner
from loadout.providers.base import ProviderStatus


def statuses(*available: str) -> dict[str, ProviderStatus]:
    from loadout.providers import all_providers

    return {
        name: ProviderStatus(name=name, available=name in available)
        for name in all_providers()
    }


class TestProviderSelection:
    def test_prefers_apt_on_debian_when_everything_is_available(
        self, catalog, all_available
    ):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        provider, method = planner.choose_method(catalog.get("ffuf"))
        assert provider == "apt"
        assert method.spec["package"] == "ffuf"

    def test_falls_back_to_go_when_apt_is_absent(self, catalog):
        planner = Planner(catalog, distro="kali", statuses=statuses("go", "github"))
        provider, _ = planner.choose_method(catalog.get("ffuf"))
        assert provider == "go"

    def test_falls_back_to_github_when_only_that_works(self, catalog):
        planner = Planner(catalog, distro="macos", statuses=statuses("github"))
        provider, _ = planner.choose_method(catalog.get("ffuf"))
        assert provider == "github"

    def test_distro_restriction_is_honoured(self, catalog):
        """ffuf's apt route declares distros: [kali]; on macOS it must not win."""
        planner = Planner(catalog, distro="macos", statuses=statuses("apt", "go"))
        provider, _ = planner.choose_method(catalog.get("ffuf"))
        assert provider == "go"

    def test_catalog_priority_beats_provider_default(self, catalog, all_available):
        """nuclei pins go=20 over apt=60 even though apt's default is lower."""
        planner = Planner(catalog, distro="kali", statuses=all_available)
        provider, _ = planner.choose_method(catalog.get("nuclei"))
        assert provider == "go"

    def test_user_preference_beats_everything(self, catalog, all_available):
        planner = Planner(
            catalog, distro="kali", statuses=all_available, preferred=["github"]
        )
        provider, _ = planner.choose_method(catalog.get("ffuf"))
        assert provider == "github"

    def test_selection_is_deterministic(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        picks = {planner.choose_method(catalog.get("ffuf"))[0] for _ in range(20)}
        assert len(picks) == 1

    def test_no_viable_provider_names_what_was_tried(self, catalog):
        planner = Planner(catalog, distro="arch", statuses=statuses())
        with pytest.raises(NoViableProvider) as excinfo:
            planner.choose_method(catalog.get("masscan"))
        assert "apt" in excinfo.value.tried


class TestPlanBuilding:
    def test_unknown_tool_is_skipped_not_fatal(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        plan = planner.plan(["nmap", "does-not-exist"])
        assert plan.tool_ids == ["nmap"]
        assert plan.skipped[0].tool_id == "does-not-exist"
        assert "not in catalog" in plan.skipped[0].reason

    def test_resolve_suggests_near_misses(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        with pytest.raises(ToolNotFound) as excinfo:
            planner.resolve("nma")
        assert "nmap" in excinfo.value.suggestions

    def test_duplicates_are_collapsed(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        plan = planner.plan(["nmap", "nmap", "NMAP"])
        assert plan.tool_ids == ["nmap"]

    def test_already_installed_is_skipped(self, catalog, all_available):
        planner = Planner(
            catalog,
            distro="kali",
            statuses=all_available,
            installed={"apt": {"nmap"}},
        )
        plan = planner.plan(["nmap", "masscan"])
        assert plan.tool_ids == ["masscan"]
        assert plan.skipped[0].reason == "already installed"

    def test_reinstall_overrides_the_skip(self, catalog, all_available):
        planner = Planner(
            catalog, distro="kali", statuses=all_available, installed={"apt": {"nmap"}}
        )
        plan = planner.plan(["nmap"], skip_installed=False)
        assert plan.tool_ids == ["nmap"]

    def test_removing_something_absent_is_skipped(self, catalog, all_available):
        planner = Planner(
            catalog, distro="kali", statuses=all_available, installed={"apt": {"nmap"}}
        )
        plan = planner.plan(["masscan"], action=ACTION_REMOVE)
        assert not plan.actions
        assert plan.skipped[0].reason == "not installed"

    def test_provider_override_that_the_catalog_lacks(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        plan = planner.plan(["masscan"], provider_override="cargo")
        assert not plan.actions
        assert "no cargo install method" in plan.skipped[0].reason

    def test_needs_root_is_reported(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        assert planner.plan(["nmap"], provider_override="apt").needs_root is True
        assert planner.plan(["ffuf"], provider_override="go").needs_root is False

    def test_plan_serialises_for_json_output(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        payload = planner.plan(["nmap", "nope"]).to_dict()
        assert payload["actions"][0]["tool"] == "nmap"
        assert payload["actions"][0]["provider"] == "apt"
        assert payload["skipped"][0]["tool"] == "nope"
        assert isinstance(payload["needs_root"], bool)


class TestPlannedCommands:
    def test_apt_argv_shape(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        plan = planner.plan(["nmap"], provider_override="apt")
        argv = plan.actions[0].steps[0].argv
        assert argv[0] == "apt-get"
        assert argv[1] == "install"
        assert argv[-1] == "nmap"
        assert "-y" in argv

    def test_go_argv_shape(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        plan = planner.plan(["ffuf"], provider_override="go")
        assert plan.actions[0].steps[0].argv == [
            "go",
            "install",
            "github.com/ffuf/ffuf/v2@latest",
        ]

    def test_removal_uses_remove_not_purge_by_default(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        plan = planner.plan(["nmap"], action=ACTION_REMOVE, provider_override="apt")
        assert "remove" in plan.actions[0].steps[0].argv
        assert "purge" not in plan.actions[0].steps[0].argv

    def test_github_plan_is_inspectable_without_running(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        plan = planner.plan(["ffuf"], provider_override="github", action=ACTION_INSTALL)
        rendered = plan.actions[0].render()[0]
        assert "ffuf/ffuf" in rendered
        assert "verify" in rendered

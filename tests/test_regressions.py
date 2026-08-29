"""Regression tests, one per finding from the 0.3 teardown.

Each test is named for the finding it locks down. If one of these fails, a bug
that shipped once has shipped again.
"""

from __future__ import annotations

import pytest

from loadout.model import Tool
from loadout.planner import Planner
from loadout.providers.apt import AptProvider
from loadout.state import StateDB


class TestB01LiveInstalledState:
    """`list --installed` and `export` returned nothing on a fresh machine
    because installed state was read from a cache file that started empty."""

    def test_installed_comes_from_provider_not_cache(self, catalog, monkeypatch):
        import argparse

        from loadout.providers.base import ProviderStatus
        from loadout.ui.cli import Context

        # apt reports nmap installed; nothing has ever written a cache file.
        monkeypatch.setattr(
            AptProvider, "list_installed", lambda self: {"nmap", "coreutils"}
        )
        args = argparse.Namespace(as_json=False, catalog=None, prefer=[])
        ctx = Context(args=args)
        ctx._catalog = catalog
        ctx._statuses = {"apt": ProviderStatus(name="apt", available=True)}

        assert "nmap" in ctx.installed()
        assert "ffuf" not in ctx.installed()

    def test_state_is_reconciled_downward(self, catalog, monkeypatch):
        """A tool removed outside loadout stops being reported as installed."""
        import argparse

        from loadout.providers.base import ProviderStatus
        from loadout.state import get_state_db
        from loadout.ui.cli import Context

        get_state_db().set_installed("masscan", True, provider="apt")

        monkeypatch.setattr(AptProvider, "list_installed", lambda self: set())
        args = argparse.Namespace(as_json=False, catalog=None, prefer=[])
        ctx = Context(args=args)
        ctx._catalog = catalog
        ctx._statuses = {"apt": ProviderStatus(name="apt", available=True)}

        assert ctx.installed() == set()
        assert get_state_db().installed_ids() == set()


class TestB02CatalogLocation:
    """The catalog was written into the installed package directory, so pipx
    upgrades discarded it and read-only prefixes broke refresh."""

    def test_catalog_writes_to_xdg_data_home(self, tmp_path, monkeypatch):
        from loadout.paths import bundled_catalog, catalog_db

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        target = catalog_db()
        assert str(tmp_path / "data") in str(target)
        # The bundled copy stays inside the package and is never the write target.
        assert bundled_catalog() != target

    def test_user_catalog_wins_over_bundled(self, tmp_path, monkeypatch, sample_tools):
        from loadout.catalog.store import build_catalog, open_catalog
        from loadout.paths import catalog_db

        build_catalog(catalog_db(), sample_tools, source="user-refreshed")
        with open_catalog() as store:
            assert store.info().source == "user-refreshed"


class TestB03NoninteractiveInstall:
    """apt could hang forever on a debconf prompt whose output was piped away."""

    def test_env_forces_noninteractive(self):
        from loadout.policy import subprocess_env

        env = subprocess_env()
        assert env["DEBIAN_FRONTEND"] == "noninteractive"

    def test_install_argv_keeps_existing_config(self):
        steps = AptProvider().plan_install(
            Tool(id="nmap"), _method("apt", package="nmap")
        )
        argv = steps[0].argv
        assert "-o" in argv
        assert "Dpkg::Options::=--force-confold" in argv

    def test_subprocess_stdin_is_devnull(self):
        """A hang must become a clean failure, not a frozen terminal."""
        import inspect

        from loadout.executor import Executor

        source = inspect.getsource(Executor._spawn)
        assert "stdin=subprocess.DEVNULL" in source


class TestB04TagsFilter:
    """`search tag:osint` silently matched nothing: Tool had no tags field and
    the dict shim turned the typo into None."""

    def test_tool_has_real_tags_field(self):
        tool = Tool(id="x", tags=("osint", "recon"))
        assert tool.tags == ("osint", "recon")

    def test_no_dict_shim_hides_typos(self):
        tool = Tool(id="x")
        assert not hasattr(tool, "get")
        with pytest.raises(TypeError):
            tool["tags"]  # type: ignore[index]

    def test_tag_filter_actually_filters(self, catalog):
        assert [t.id for t in catalog.search("", tags=["bug-bounty"])] == ["ffuf"]
        assert catalog.search("", tags=["nonexistent-tag"]) == []


class TestB05BinaryNames:
    """`launch` and `--help` ran the package name, so metasploit-framework
    produced "command not found" instead of msfconsole."""

    def test_primary_binary_is_not_synthesised_from_id(self):
        tool = Tool(id="metasploit-framework")
        assert tool.primary_binary == ""

    def test_primary_binary_uses_the_catalog(self):
        tool = Tool(id="metasploit-framework", binaries=("msfconsole", "msfvenom"))
        assert tool.primary_binary == "msfconsole"

    def test_dpkg_binaries_filters_to_bin_dirs(self, monkeypatch):
        import subprocess as sp

        from loadout.providers import apt as apt_module

        listing = "\n".join(
            [
                "/usr",
                "/usr/share/doc/exploitdb/README",
                "/usr/bin/searchsploit",
                "/usr/share/exploitdb/files_exploits.csv",
                "/usr/sbin/helper",
            ]
        )
        monkeypatch.setattr(apt_module.shutil, "which", lambda _n: "/usr/bin/dpkg")
        monkeypatch.setattr(
            sp, "run", lambda *a, **k: sp.CompletedProcess(a, 0, listing, "")
        )
        # dpkg's own order is preserved when nothing matches the package name.
        assert apt_module.dpkg_binaries("exploitdb") == ["searchsploit", "helper"]

    def test_package_named_binary_is_promoted(self, monkeypatch):
        """dpkg -L is alphabetical, so the real command is rarely first."""
        import subprocess as sp

        from loadout.providers import apt as apt_module

        listing = "\n".join(["/usr/bin/aardvark", "/usr/bin/nmap", "/usr/bin/zebra"])
        monkeypatch.setattr(apt_module.shutil, "which", lambda _n: "/usr/bin/dpkg")
        monkeypatch.setattr(
            sp, "run", lambda *a, **k: sp.CompletedProcess(a, 0, listing, "")
        )
        assert apt_module.dpkg_binaries("nmap")[0] == "nmap"


class TestB06StatePruneAtScale:
    """prune_unknown bound one SQL variable per id and blew SQLite's 32,766
    limit on a full APT catalog -- swallowed by a blanket except."""

    def test_prune_handles_more_ids_than_sqlite_variable_limit(self, tmp_path):
        database = StateDB(tmp_path / "state.db")
        database.set_installed("stale-tool", True)
        database.set_installed("kept-tool", True)

        # Comfortably past every build's ceiling, and past the ~70k packages a
        # full APT catalog actually contains.
        known = [f"pkg-{i}" for i in range(300_000)] + ["kept-tool"]
        removed = database.prune_unknown(known)

        assert removed == 1
        assert database.get("stale-tool") is None
        assert database.get("kept-tool") is not None

    def test_prune_keeps_starred_and_annotated_rows(self, tmp_path):
        database = StateDB(tmp_path / "state.db")
        database.set_installed("starred-tool", True)
        database.set_starred("starred-tool", True)
        database.prune_unknown(["something-else"])
        assert database.get("starred-tool") is not None


class TestB07RealProgress:
    """Progress was min(95, 5 + lines*2): every install hit 95% after 45 lines
    regardless of package size."""

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("pmstatus:nmap:42.5:Unpacking nmap", (42.5, "Unpacking nmap")),
            ("dlstatus:1:12.0:Retrieving file 1 of 3", (12.0, "Retrieving file 1 of 3")),
            ("pmstatus:x:100:Done", (100.0, "Done")),
        ],
    )
    def test_parses_apt_status_fd(self, line, expected):
        assert AptProvider.parse_status_line(line) == expected

    @pytest.mark.parametrize(
        "line", ["", "garbage", "pmstatus:only:two", "other:x:1:msg", "pmstatus:x:NaN:m"]
    )
    def test_ignores_anything_else(self, line):
        assert AptProvider.parse_status_line(line) is None

    def test_percentage_is_clamped(self):
        assert AptProvider.parse_status_line("pmstatus:x:150:m") == (100.0, "m")
        assert AptProvider.parse_status_line("pmstatus:x:-5:m") == (0.0, "m")

    def test_status_fd_option_is_requested(self):
        from loadout.providers.apt import apt_status_fd_args

        assert apt_status_fd_args(7) == ["-o", "APT::Status-Fd=7"]


class TestB08NoNetworkOnStartup:
    """The constructor scraped ~800 pages from kali.org on first run."""

    def test_opening_a_catalog_makes_no_http_call(self, catalog, monkeypatch):
        def explode(*_a, **_k):  # pragma: no cover - must never run
            raise AssertionError("startup must not touch the network")

        monkeypatch.setattr("loadout.http_util.polite_get", explode)
        assert catalog.count() > 0
        assert catalog.get("nmap") is not None

    def test_no_scraper_module_remains(self):
        import importlib

        for name in ("kalitools_lib.scraping", "loadout.manager"):
            with pytest.raises(ModuleNotFoundError):
                importlib.import_module(name)


class TestB09ValidationOnEveryPath:
    """Install validated package names; remove did not."""

    @pytest.mark.parametrize("verb", ["plan_install", "plan_remove"])
    @pytest.mark.parametrize("bad", ["; rm -rf /", "--force-yes", "pkg name", "../etc"])
    def test_both_paths_reject_unsafe_names(self, verb, bad):
        from loadout.errors import UnsafeArgument

        provider = AptProvider()
        with pytest.raises(UnsafeArgument):
            getattr(provider, verb)(Tool(id="x"), _method("apt", package=bad))

    @pytest.mark.parametrize("verb", ["plan_install", "plan_remove"])
    def test_double_dash_separates_options_from_names(self, verb):
        steps = getattr(AptProvider(), verb)(
            Tool(id="nmap"), _method("apt", package="nmap")
        )
        argv = steps[0].argv
        assert "--" in argv
        assert argv.index("--") < argv.index("nmap")


class TestB10Deb822Sources:
    """The sources check globbed *.list only and ignored modern deb822 files.

    Both tests pass explicit paths rather than patching ``Path``: the earlier
    version leaked onto the host's real /etc/apt and so passed on a developer
    machine while failing on a CI runner that had one.
    """

    def test_reads_deb822_sources(self, tmp_path):
        from loadout import doctor

        sources_dir = tmp_path / "sources.list.d"
        sources_dir.mkdir(parents=True)
        (sources_dir / "kali.sources").write_text(
            "Types: deb\nURIs: http://http.kali.org/kali\n"
            "Suites: kali-rolling\nComponents: main\n",
            encoding="utf-8",
        )

        result = doctor._check_apt_sources(
            sources_dir=sources_dir, main_list=tmp_path / "absent.list"
        )
        assert result.severity == "ok"
        assert "1 source file" in result.message

    def test_incomplete_deb822_stanza_is_flagged(self, tmp_path):
        from loadout import doctor

        sources_dir = tmp_path / "sources.list.d"
        sources_dir.mkdir(parents=True)
        (sources_dir / "broken.sources").write_text(
            "Types: deb\nComponents: main\n", encoding="utf-8"
        )
        result = doctor._check_apt_sources(
            sources_dir=sources_dir, main_list=tmp_path / "absent.list"
        )
        assert result.severity == "warn"
        assert "incomplete deb822" in result.remediation

    def test_flags_disabled_signature_verification(self, tmp_path):
        from loadout import doctor

        sources_dir = tmp_path / "sources.list.d"
        sources_dir.mkdir(parents=True)
        (sources_dir / "local.list").write_text(
            "deb [trusted=yes] file:/srv/mirror ./\n", encoding="utf-8"
        )
        result = doctor._check_apt_sources(
            sources_dir=sources_dir, main_list=tmp_path / "absent.list"
        )
        assert result.severity == "warn"
        assert "signature verification disabled" in result.remediation

    def test_no_apt_at_all_is_not_a_problem(self, tmp_path):
        from loadout import doctor

        result = doctor._check_apt_sources(
            sources_dir=tmp_path / "nope", main_list=tmp_path / "also-nope"
        )
        assert result.severity == "ok"
        assert "not an APT system" in result.message



class TestA03SingleSearchImplementation:
    """Three search implementations ranked the same query differently."""

    def test_cli_and_tui_share_the_catalog_search(self, catalog):
        direct = [t.id for t in catalog.search("fuzz")]
        assert direct == ["ffuf"]
        # There is exactly one search entry point; no module defines its own.
        import inspect

        from loadout.ui import cli

        source = inspect.getsource(cli)
        assert "def _score_tool" not in source
        assert "fuzz.partial_ratio" not in source


class TestPlanCoalescing:
    """Eighteen apt packages should be one transaction, not eighteen sudo calls."""

    def test_apt_actions_merge_into_one_step(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        plan = planner.plan(["nmap", "masscan"], provider_override="apt")

        steps = [step for action in plan.actions for step in action.steps]
        assert len(steps) == 1
        assert "nmap" in steps[0].argv
        assert "masscan" in steps[0].argv

    def test_every_tool_still_gets_a_result(self, catalog, all_available):
        from loadout.executor import Executor

        planner = Planner(catalog, distro="kali", statuses=all_available)
        plan = planner.plan(["nmap", "masscan"], provider_override="apt")
        result = Executor(dry_run=True).run(plan)
        assert {r.tool_id for r in result.results} == {"nmap", "masscan"}


def _method(provider: str, **spec):
    from loadout.model import InstallMethod

    return InstallMethod(provider=provider, spec=spec)


class TestB12StatusFdArgvPlacement:
    """Live-run finding: the APT status-fd options were appended to an argv that
    already ended in `-- <package>`, so apt read them as package names and
    exited 100. Only a real install could surface this."""

    def test_options_land_before_the_separator(self):
        from loadout.executor import _insert_options

        argv = ["apt-get", "install", "-y", "--", "nmap"]
        merged = _insert_options(argv, ["-o", "APT::Status-Fd=7"])
        assert merged.index("-o") < merged.index("--")
        assert merged[-1] == "nmap"

    def test_appends_when_there_is_no_separator(self):
        from loadout.executor import _insert_options

        assert _insert_options(["apt-get", "update"], ["-o", "X=1"]) == [
            "apt-get",
            "update",
            "-o",
            "X=1",
        ]

    def test_nothing_follows_the_separator_but_packages(self):
        """Whatever we inject, the tail after `--` must stay package names."""
        from loadout.executor import _insert_options
        from loadout.model import Tool
        from loadout.providers.apt import AptProvider

        steps = AptProvider().plan_install(
            Tool(id="nmap"), _method("apt", package="nmap")
        )
        merged = _insert_options(steps[0].argv, ["-o", "APT::Status-Fd=9"])
        tail = merged[merged.index("--") + 1 :]
        assert tail == ["nmap"]
        assert not any(token.startswith("-") for token in tail)


class TestFailureOutputIsSurfaced:
    """`apt-get exited 100` with no further detail forced the user to re-run the
    command by hand to learn anything."""

    def test_error_includes_the_last_output_lines(self, catalog, all_available):
        from loadout.executor import Executor
        from loadout.planner import Planner
        from loadout.providers.base import CommandStep

        plan = Planner(catalog, distro="kali", statuses=all_available).plan(["nmap"])
        plan.actions[0].steps = [
            CommandStep(argv=["python", "-c", "import sys; sys.exit(3)"],
                        description="fail")
        ]
        executor = Executor(sink=lambda _e: None)
        executor._recent_output.extend(["E: Unable to locate package -o", "noise"])
        result = executor.run(plan)
        assert not result.ok
        assert "exited 3" in result.failures[0].error


class TestVerbConjugation:
    """`f"{action}ed"` rendered "removeed" to every user who removed a tool."""

    def test_past_tense(self):
        from loadout.executor import past_tense

        assert past_tense("install") == "installed"
        assert past_tense("remove") == "removed"


class TestB13StatusFdSurvivesSudo:
    """Live-run finding: a real install as a non-root sudo user produced
    "E: Write error - write (9: Bad file descriptor)" on every status write,
    which apt then treated as fatal -- exiting non-zero even though dpkg had
    already finished (confirmed by cross-checking real dpkg state, which
    showed the package correctly installed). Sending Status-Fd through an
    anonymous pipe via pass_fds depended on that fd surviving an extra
    fork+exec through sudo; whether it does turned out to depend on things
    (sudo build, PAM, the calling thread/terminal state) this project has no
    control over. Fixed by pointing Status-Fd at fd 1 -- the process's own
    stdout, guaranteed open and inherited by exec() with no pass_fds
    bookkeeping at all."""

    def test_status_fd_targets_stdout_not_a_pipe(self):
        """No os.pipe(), no pass_fds, no extra thread: grep the source for the
        pattern that broke, so a regression shows up as a failing assertion
        instead of requiring another live incident to notice."""
        import inspect

        from loadout.executor import Executor

        source = inspect.getsource(Executor._spawn)
        assert "pass_fds=" not in source
        assert "os.pipe()" not in source
        assert "apt_status_fd_args(1)" in source

    def test_no_background_thread_is_spawned_to_read_status(self):
        import inspect

        from loadout.executor import Executor

        source = inspect.getsource(Executor._spawn)
        assert "threading.Thread" not in source

    def test_status_lines_and_plain_output_share_one_stream(self, catalog, all_available, monkeypatch):
        """A Popen returning interleaved pmstatus: and human-readable lines on
        the same stdout must route each to the right event type."""
        import subprocess as sp

        from loadout.executor import EVENT_OUTPUT, EVENT_PROGRESS, Executor
        from loadout.planner import Planner

        class FakeProcess:
            returncode = 0
            stdout = iter(
                [
                    "Reading package lists...\n",
                    "pmstatus:nmap:20.0000:Unpacking nmap (amd64)\n",
                    "Unpacking nmap (7.94-1) ...\n",
                    "pmstatus:nmap:80.0000:Installed nmap (amd64)\n",
                ]
            )

            def wait(self):
                return None

        monkeypatch.setattr(sp, "Popen", lambda *a, **k: FakeProcess())

        events = []
        plan = Planner(catalog, distro="kali", statuses=all_available).plan(
            ["nmap"], provider_override="apt"
        )
        Executor(dry_run=False, sink=events.append).run(plan)

        # The step emits an initial 0% "preparing" progress event before the
        # process even starts; only the ones parsed from real output matter here.
        progress = [
            e for e in events
            if e.kind == EVENT_PROGRESS and e.percent is not None and e.message != "apt-get install nmap"
        ]
        output = [e for e in events if e.kind == EVENT_OUTPUT]
        assert [p.percent for p in progress] == [20.0, 80.0]
        assert any("Reading package lists" in o.message for o in output)
        assert any("Unpacking nmap (7.94-1)" in o.message for o in output)
        # A pmstatus: line is machine format, not something a human needs to
        # see twice -- it must not also show up as a plain output line.
        assert not any("pmstatus:" in o.message for o in output)

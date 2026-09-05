"""The APT enrichment engine.

This module generates the catalog every user ends up with, so it needs to be
trustworthy off a Debian host. The fixtures are real output captured from Kali
rolling (``apt-cache show`` and ``apt-cache depends --recurse``), so the parsers
are tested against the shapes they actually meet rather than an idealised
version of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.catalog import seed_apt
from loadout.model import InstallMethod, Tool

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def dumpavail() -> str:
    return (FIXTURES / "dumpavail-sample.txt").read_text(encoding="utf-8")


@pytest.fixture
def depends_output() -> str:
    return (FIXTURES / "depends-kali-tools-web.txt").read_text(encoding="utf-8")


class TestStanzaParsing:
    def test_reads_every_package_in_the_fixture(self, dumpavail, monkeypatch):
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(seed_apt, "_run", lambda *a, **k: dumpavail)
        entries = seed_apt._entries_via_dumpavail(None)
        names = {e["name"] for e in entries}
        assert "nmap" in names
        assert len(entries) >= 2

    def test_extracts_the_fields_the_catalog_needs(self, dumpavail, monkeypatch):
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(seed_apt, "_run", lambda *a, **k: dumpavail)
        entries = {e["name"]: e for e in seed_apt._entries_via_dumpavail(None)}

        nmap = entries["nmap"]
        assert nmap["summary"] == "The Network Mapper"
        assert nmap["homepage"] == "https://nmap.org/"
        assert nmap["size"] == 4823 * 1024, "Installed-Size is in KiB"
        assert nmap["version"].startswith("7.99")
        assert "network exploration" in nmap["description"]

    def test_summary_is_the_first_line_only(self, dumpavail, monkeypatch):
        """Description's first line is the summary; the rest is the body."""
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(seed_apt, "_run", lambda *a, **k: dumpavail)
        entries = {e["name"]: e for e in seed_apt._entries_via_dumpavail(None)}
        assert "\n" not in entries["nmap"]["summary"]
        assert entries["nmap"]["summary"] != entries["nmap"]["description"]

    def test_no_apt_cache_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: None)
        assert seed_apt._entries_via_dumpavail(None) is None

    def test_malformed_stanzas_are_skipped_not_fatal(self, monkeypatch):
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(
            seed_apt,
            "_run",
            lambda *a, **k: "Package: good\nDescription: fine\n\n"
            "this is not a stanza at all\n\n"
            "Version: 1.0\n\n",  # no Package: field
        )
        entries = seed_apt._entries_via_dumpavail(None)
        assert [e["name"] for e in entries] == ["good"]

    def test_later_versions_win(self, monkeypatch):
        """dumpavail lists every available version; keep the last seen."""
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(
            seed_apt,
            "_run",
            lambda *a, **k: "Package: tool\nVersion: 1.0\nDescription: old\n\n"
            "Package: tool\nVersion: 2.0\nDescription: new\n\n",
        )
        entries = seed_apt._entries_via_dumpavail(None)
        assert len(entries) == 1
        assert entries[0]["version"] == "2.0"

    def test_progress_callback_is_invoked(self, dumpavail, monkeypatch):
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(seed_apt, "_run", lambda *a, **k: dumpavail)
        seen: list[tuple[int, int]] = []
        seed_apt._entries_via_dumpavail(lambda done, total: seen.append((done, total)))
        assert seen, "progress should be reported for long-running parses"


class TestMetaPackageMembership:
    def test_parses_recurse_output(self, depends_output, monkeypatch):
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(seed_apt, "_run", lambda *a, **k: depends_output)

        membership = seed_apt.discover_meta_membership(["kali-tools-web"])
        assert membership, "the fixture contains real members"
        assert all(cat == "web" for cat, _tags in membership.values())

    def test_skips_indented_depends_lines(self, depends_output, monkeypatch):
        """`--recurse` prints both '  Depends: x' and bare 'x'; only bare names
        are members, and a 'Depends:' line must never become a package id."""
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(seed_apt, "_run", lambda *a, **k: depends_output)

        membership = seed_apt.discover_meta_membership(["kali-tools-web"])
        assert not any(":" in name or " " in name for name in membership)
        assert "Depends" not in membership

    def test_the_meta_package_is_not_its_own_member(self, depends_output, monkeypatch):
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(seed_apt, "_run", lambda *a, **k: depends_output)
        membership = seed_apt.discover_meta_membership(["kali-tools-web"])
        assert "kali-tools-web" not in membership
        assert not any(n.startswith(("kali-tools-", "kali-linux-")) for n in membership)

    def test_first_meta_wins(self, monkeypatch):
        """META_PACKAGES order is authoritative when a tool is in two metas."""
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(seed_apt, "_run", lambda *a, **k: "meta\nsharedtool\n")
        membership = seed_apt.discover_meta_membership(
            ["kali-tools-information-gathering", "kali-tools-web"]
        )
        assert membership["sharedtool"][0] == "recon"

    def test_unknown_meta_is_ignored(self, monkeypatch):
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: "/usr/bin/apt-cache")
        monkeypatch.setattr(seed_apt, "_run", lambda *a, **k: "whatever\n")
        assert seed_apt.discover_meta_membership(["not-a-kali-meta"]) == {}

    def test_no_apt_cache_is_not_an_error(self, monkeypatch):
        monkeypatch.setattr(seed_apt.shutil, "which", lambda _n: None)
        assert seed_apt.discover_meta_membership() == {}


class TestDebtags:
    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("security::forensics", "forensics"),
            ("network::scanner", "recon"),
            ("network::sniffer", "sniffing"),
            ("use::scanning", "recon"),
            ("protocol::http", "web"),
            ("devel::rev-engineering", "reverse"),
        ],
    )
    def test_known_tags_map_to_categories(self, tag, expected):
        assert seed_apt._debtag_category([tag]) == expected

    def test_unknown_tags_give_nothing(self):
        assert seed_apt._debtag_category(["made::up", "role::program"]) == ""

    def test_empty_tag_list(self):
        assert seed_apt._debtag_category([]) == ""


class TestBuildTools:
    def _patch(self, monkeypatch, entries, membership=None):
        monkeypatch.setattr(seed_apt, "_entries_via_python_apt", lambda _p: None)
        monkeypatch.setattr(seed_apt, "_entries_via_dumpavail", lambda _p: entries)
        monkeypatch.setattr(
            seed_apt, "discover_meta_membership", lambda *a, **k: membership or {}
        )
        monkeypatch.setattr(seed_apt, "dpkg_binaries", lambda _n, **k: [])

    def test_meta_membership_beats_debtags(self, monkeypatch):
        """Kali's own taxonomy is the strongest signal available."""
        self._patch(
            monkeypatch,
            [{"name": "tool", "summary": "s", "tags": ["security::forensics"], "size": 0}],
            membership={"tool": ("web", ("kali",))},
        )
        tools = {t.id: t for t in seed_apt.build_tools()}
        assert tools["tool"].category == "web"
        assert "kali" in tools["tool"].tags

    def test_debtags_used_when_no_membership(self, monkeypatch):
        self._patch(
            monkeypatch,
            [{"name": "tool", "summary": "s", "tags": ["security::forensics"], "size": 0}],
        )
        tools = {t.id: t for t in seed_apt.build_tools()}
        assert tools["tool"].category == "forensics"

    def test_only_security_filters_out_evidence_free_packages(self, monkeypatch):
        """No membership and no security debtag means it is not a security tool."""
        self._patch(
            monkeypatch,
            [
                {"name": "libfoo", "summary": "a library", "tags": [], "size": 0},
                {"name": "tool", "summary": "s", "tags": ["network::scanner"], "size": 0},
            ],
        )
        assert [t.id for t in seed_apt.build_tools(only_security=True)] == ["tool"]
        assert len(seed_apt.build_tools(only_security=False)) == 2

    def test_no_keyword_guessing(self, monkeypatch):
        """The substring guesser is gone: no evidence means 'other', not a guess.

        The old heuristic matched 'sql' anywhere in a description and filed the
        package under database, which is how 655 of 764 tools ended up wrong.
        """
        self._patch(
            monkeypatch,
            [{
                "name": "innocent",
                "summary": "Reads SQL dumps and web logs for forensics",
                "tags": [],
                "size": 0,
            }],
        )
        tools = seed_apt.build_tools(only_security=False)
        assert tools[0].category == "other"

    def test_apt_install_method_is_attached(self, monkeypatch):
        self._patch(monkeypatch, [{"name": "tool", "summary": "s", "tags": [], "size": 0}])
        tool = seed_apt.build_tools(only_security=False)[0]
        assert tool.providers == ("apt",)
        assert tool.install[0].spec["package"] == "tool"
        assert "kali" in tool.install[0].distros

    def test_plus_in_package_names_survives(self, monkeypatch):
        """953 Kali packages contain '+'; rejecting them aborted the whole build."""
        self._patch(
            monkeypatch,
            [{"name": "aflplusplus", "summary": "fuzzer", "tags": [], "size": 0},
             {"name": "g++", "summary": "compiler", "tags": [], "size": 0}],
        )
        ids = {t.id for t in seed_apt.build_tools(only_security=False)}
        assert "g++" in ids

    def test_no_metadata_source_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(seed_apt, "_entries_via_python_apt", lambda _p: None)
        monkeypatch.setattr(seed_apt, "_entries_via_dumpavail", lambda _p: None)
        assert seed_apt.build_tools() == []


class TestEnrich:
    """Enrichment fills gaps. Anything the entry already states must win --
    otherwise a CI job would quietly overwrite hand-curated text."""

    def _patch(self, monkeypatch, entries, membership=None):
        monkeypatch.setattr(seed_apt, "_entries_via_python_apt", lambda _p: None)
        monkeypatch.setattr(seed_apt, "_entries_via_dumpavail", lambda _p: entries)
        monkeypatch.setattr(
            seed_apt, "discover_meta_membership", lambda *a, **k: membership or {}
        )
        monkeypatch.setattr(seed_apt, "dpkg_binaries", lambda _n, **k: [])

    def test_fills_a_missing_summary(self, monkeypatch):
        self._patch(monkeypatch, [{
            "name": "nmap", "summary": "The Network Mapper",
            "homepage": "https://nmap.org/", "size": 4938752, "version": "7.99",
            "description": "long text", "tags": [],
        }])
        bare = Tool(id="nmap", install=(InstallMethod(provider="apt", spec={"package": "nmap"}),))
        [out] = seed_apt.enrich([bare])
        assert out.summary == "The Network Mapper"
        assert out.homepage == "https://nmap.org/"
        assert out.size == 4938752

    def test_never_overwrites_curated_text(self, monkeypatch):
        self._patch(monkeypatch, [{
            "name": "nmap", "summary": "The Network Mapper",
            "homepage": "https://example.invalid", "size": 1, "tags": [],
        }])
        curated = Tool(
            id="nmap",
            summary="Network discovery and service fingerprinting",
            homepage="https://nmap.org",
            install=(InstallMethod(provider="apt", spec={"package": "nmap"}),),
        )
        [out] = seed_apt.enrich([curated])
        assert out.summary == "Network discovery and service fingerprinting"
        assert out.homepage == "https://nmap.org"

    def test_upgrades_only_the_other_category(self, monkeypatch):
        self._patch(
            monkeypatch,
            [{"name": "tool", "summary": "s", "tags": [], "size": 0}],
            membership={"tool": ("forensics", ())},
        )
        bare = Tool(id="tool", categories=("other",),
                    install=(InstallMethod(provider="apt", spec={"package": "tool"}),))
        curated = Tool(id="tool", categories=("web",),
                       install=(InstallMethod(provider="apt", spec={"package": "tool"}),))
        assert seed_apt.enrich([bare])[0].category == "forensics"
        assert seed_apt.enrich([curated])[0].category == "web"

    def test_tools_apt_does_not_know_pass_through(self, monkeypatch):
        self._patch(monkeypatch, [])
        tool = Tool(id="hayabusa", summary="kept",
                    install=(InstallMethod(provider="github", spec={"repo": "a/b"}),))
        [out] = seed_apt.enrich([tool])
        assert out.summary == "kept"
        assert out.providers == ("github",)

    def test_resolves_binaries_when_asked(self, monkeypatch):
        self._patch(monkeypatch, [{"name": "exploitdb", "summary": "s", "tags": [], "size": 0}])
        monkeypatch.setattr(seed_apt, "dpkg_binaries", lambda _n, **k: ["searchsploit"])
        bare = Tool(id="exploitdb",
                    install=(InstallMethod(provider="apt", spec={"package": "exploitdb"}),))
        [out] = seed_apt.enrich([bare], resolve_binaries=True)
        assert out.binaries == ("searchsploit",)

    def test_enrichment_is_idempotent(self, monkeypatch):
        """Running the CI job twice must produce no second diff."""
        self._patch(monkeypatch, [{
            "name": "nmap", "summary": "The Network Mapper", "size": 10, "tags": [],
        }])
        bare = Tool(id="nmap", install=(InstallMethod(provider="apt", spec={"package": "nmap"}),))
        once = seed_apt.enrich([bare])
        twice = seed_apt.enrich(once)
        assert once[0].to_dict() == twice[0].to_dict()


class TestEnrichSourceTree:
    """enrich_source_tree writes hundreds of files in one run. A real run once
    lost one file outright with an ordinary (non-atomic) write -- these lock
    down the invariant that must never regress: enrichment never loses data."""

    def _write(self, root, category, tool_id, **fields):
        import yaml

        path = root / category / f"{tool_id}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"id": tool_id, "categories": [category], **fields}),
            encoding="utf-8",
        )
        return path

    def _patch_apt(self, monkeypatch, entries, membership=None):
        from loadout.catalog import seed_apt

        monkeypatch.setattr(seed_apt, "_entries_via_python_apt", lambda _p: None)
        monkeypatch.setattr(seed_apt, "_entries_via_dumpavail", lambda _p: entries)
        monkeypatch.setattr(
            seed_apt, "discover_meta_membership", lambda *a, **k: membership or {}
        )
        monkeypatch.setattr(seed_apt, "dpkg_binaries", lambda _n, **k: [])

    def test_entry_count_never_decreases(self, tmp_path, monkeypatch):
        from loadout.catalog import enrich_source_tree, load_source_tree

        for i in range(40):
            self._write(tmp_path, "other", f"tool-{i}")
        before = len(load_source_tree(tmp_path, strict=False).tools)

        self._patch_apt(
            monkeypatch,
            [{"name": f"tool-{i}", "summary": f"summary {i}", "tags": [], "size": 0}
             for i in range(40)],
        )
        enrich_source_tree(tmp_path, resolve_binaries=False)
        after = len(load_source_tree(tmp_path, strict=False).tools)
        assert after == before

    def test_every_original_file_still_exists(self, tmp_path, monkeypatch):
        from loadout.catalog import enrich_source_tree

        paths = [self._write(tmp_path, "other", f"tool-{i}") for i in range(60)]
        self._patch_apt(
            monkeypatch,
            [{"name": f"tool-{i}", "summary": f"s{i}", "tags": [], "size": 0}
             for i in range(60)],
        )
        enrich_source_tree(tmp_path, resolve_binaries=False)
        missing = [p for p in paths if not p.exists()]
        assert missing == []

    def test_curated_fields_survive_a_batch_run(self, tmp_path, monkeypatch):
        self._write(
            tmp_path, "web", "ffuf",
            summary="Fast web fuzzer for content and parameter discovery",
            homepage="https://github.com/ffuf/ffuf",
        )
        self._patch_apt(
            monkeypatch,
            [{"name": "ffuf", "summary": "apt says something else",
              "homepage": "https://example.invalid", "tags": [], "size": 0}],
        )
        from loadout.catalog import enrich_source_tree, load_source_tree

        enrich_source_tree(tmp_path, resolve_binaries=False)
        tool = load_source_tree(tmp_path, strict=False).tools[0]
        assert tool.summary == "Fast web fuzzer for content and parameter discovery"
        assert tool.homepage == "https://github.com/ffuf/ffuf"

    def test_a_write_failure_leaves_the_original_file_intact(self, tmp_path, monkeypatch):
        """Atomic replace: if the write step raises, the target is untouched."""
        from loadout.catalog import compile as compile_mod
        from loadout.model import Tool

        path = self._write(tmp_path / "sub", "other", "tool")
        original = path.read_text(encoding="utf-8")

        def boom(*_a, **_k):
            raise OSError("simulated replace failure")

        # Path.replace is the step that publishes the write; failing it must
        # leave the original file exactly as it was, not half-written.
        monkeypatch.setattr(compile_mod.Path, "replace", boom)
        with pytest.raises(OSError):
            compile_mod.dump_tool(Tool(id="tool", categories=("other",)), path)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == original

    def test_an_annotated_entry_is_never_regenerated(self, tmp_path):
        """`yaml.safe_dump` cannot round-trip comments, so regenerating an
        annotated file deletes the only place the catalog says *why* -- and
        the annotated entries are the signature ones."""
        from loadout.catalog import compile as compile_mod
        from loadout.model import Tool

        path = self._write(tmp_path, "other", "tool")
        path.write_text(
            "# Pinned from the real certificate, not from the docs.\n"
            + path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        original = path.read_text(encoding="utf-8")

        result = compile_mod.dump_tool(
            Tool(id="tool", summary="changed", categories=("other",)), path
        )

        assert result is None
        assert path.read_text(encoding="utf-8") == original

    def test_an_unannotated_entry_is_still_written(self, tmp_path):
        from loadout.catalog import compile as compile_mod
        from loadout.model import Tool

        path = self._write(tmp_path, "other", "tool")
        result = compile_mod.dump_tool(
            Tool(id="tool", summary="changed", categories=("other",)), path
        )
        assert result == path
        assert "changed" in path.read_text(encoding="utf-8")

    def test_enrichment_reports_what_it_declined_to_touch(self, tmp_path, monkeypatch):
        """Silently skipping would be indistinguishable from having nothing to
        do, and the weekly job's whole output is these counts."""
        path = self._write(tmp_path, "other", "tool")
        path.write_text("# why\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        self._patch_apt(
            monkeypatch,
            [{"name": "tool", "summary": "from apt", "tags": [], "size": 0}],
        )
        from loadout.catalog import enrich_source_tree

        stats = enrich_source_tree(tmp_path, resolve_binaries=False)

        assert stats["annotated"] == 1
        assert stats["changed"] == 0
        assert "from apt" not in path.read_text(encoding="utf-8")

    def test_idempotent_second_run_touches_nothing(self, tmp_path, monkeypatch):
        self._write(tmp_path, "other", "tool")
        self._patch_apt(
            monkeypatch, [{"name": "tool", "summary": "s", "tags": [], "size": 0}]
        )
        from loadout.catalog import enrich_source_tree

        first = enrich_source_tree(tmp_path, resolve_binaries=False)
        second = enrich_source_tree(tmp_path, resolve_binaries=False)
        assert first["changed"] == 1
        assert second["changed"] == 0

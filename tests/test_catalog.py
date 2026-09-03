"""Catalog store, schema validation and the compiler."""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.catalog.compile import load_source_tree
from loadout.catalog.schema import validate_entry
from loadout.catalog.store import CatalogStore, build_catalog
from loadout.errors import CatalogMissing
from loadout.model import Tool


class TestStore:
    def test_round_trips_every_field(self, tmp_path):
        original = Tool(
            id="ffuf",
            summary="Fast web fuzzer",
            description="Longer text",
            categories=("web", "fuzzing"),
            tags=("bug-bounty",),
            phases=("discovery",),
            binaries=("ffuf",),
            homepage="https://example.invalid",
            license="Apache-2.0",
            alternatives=("gobuster",),
            requires_root=False,
            verify="ffuf -V",
            size=1024,
            install=(),
        )
        path = tmp_path / "c.db"
        build_catalog(path, [original])
        with CatalogStore(path) as store:
            loaded = store.get("ffuf")
        assert loaded is not None
        assert loaded.to_dict() == original.to_dict()

    def test_lookup_is_case_insensitive(self, catalog):
        assert catalog.get("NMAP") is not None
        assert catalog.get("  nmap  ") is not None

    def test_missing_catalog_raises_with_a_fix(self, tmp_path):
        with pytest.raises(CatalogMissing) as excinfo:
            CatalogStore(tmp_path / "nope.db")
        assert "catalog update" in excinfo.value.remediation

    def test_get_many_preserves_request_order(self, catalog):
        got = catalog.get_many(["nuclei", "nmap", "missing", "ffuf"])
        assert [t.id for t in got] == ["nuclei", "nmap", "ffuf"]

    def test_get_many_chunks_past_the_variable_limit(self, catalog):
        """A 40k-id request must not raise 'too many SQL variables'."""
        request = [f"absent-{i}" for i in range(40_000)] + ["nmap"]
        assert [t.id for t in catalog.get_many(request)] == ["nmap"]

    def test_info_reports_build_metadata(self, catalog):
        info = catalog.info()
        assert info.tool_count == 4
        assert info.source == "test"
        assert info.generated_at


class TestSearch:
    def test_matches_on_summary_not_just_name(self, catalog):
        assert [t.id for t in catalog.search("fuzzer")] == ["ffuf"]

    def test_prefix_matching(self, catalog):
        assert "nmap" in [t.id for t in catalog.search("nma")]

    def test_empty_query_returns_everything_sorted(self, catalog):
        assert [t.id for t in catalog.search("")] == [
            "ffuf",
            "masscan",
            "nmap",
            "nuclei",
        ]

    def test_facet_filters_combine_as_and(self, catalog):
        assert [t.id for t in catalog.search("", categories=["web"], tags=["fuzzing"])] == [
            "ffuf"
        ]
        assert catalog.search("", categories=["recon"], tags=["fuzzing"]) == []

    def test_provider_facet(self, catalog):
        ids = {t.id for t in catalog.search("", providers=["go"])}
        assert ids == {"ffuf", "nuclei"}

    def test_limit_is_applied(self, catalog):
        assert len(catalog.search("", limit=2)) == 2

    @pytest.mark.parametrize("hostile", ['" OR 1=1 --', "AND OR NOT", "*", '""', "NEAR("])
    def test_fts_operators_in_user_input_are_literal(self, catalog, hostile):
        """A user typing FTS syntax must get zero results, not a crash."""
        assert isinstance(catalog.search(hostile), list)

    def test_suggest_finds_near_misses(self, catalog):
        assert "nmap" in catalog.suggest("nmp") or "nmap" in catalog.suggest("nma")


class TestSchemaValidation:
    def test_minimal_entry_is_valid(self):
        result = validate_entry({"id": "x", "summary": "y", "binaries": ["x"]})
        assert result.ok

    def test_missing_id_is_an_error(self):
        assert not validate_entry({"summary": "no id"}).ok

    def test_bad_id_characters_rejected(self):
        result = validate_entry({"id": "Bad Name"})
        assert not result.ok
        assert "must match" in result.errors[0]

    def test_unknown_category_names_the_valid_ones(self):
        result = validate_entry({"id": "x", "categories": ["nonsense"]})
        assert not result.ok
        assert "Valid:" in result.errors[0]

    def test_unknown_phase_rejected(self):
        assert not validate_entry({"id": "x", "phases": ["teatime"]}).ok

    def test_unknown_provider_rejected(self):
        result = validate_entry(
            {"id": "x", "install": [{"provider": "aptitude", "package": "x"}]}
        )
        assert not result.ok
        assert "unknown provider" in result.errors[0]

    def test_provider_required_keys_enforced(self):
        result = validate_entry({"id": "x", "install": [{"provider": "apt"}]})
        assert not result.ok
        assert "requires package" in result.errors[0]

    def test_all_errors_collected_at_once(self):
        """A contributor should see every problem in one CI run."""
        result = validate_entry(
            {"id": "x", "categories": ["nope"], "phases": ["nope"],
             "install": [{"provider": "nope"}]}
        )
        assert len(result.errors) >= 3

    def test_missing_binaries_warns_not_errors(self):
        result = validate_entry({"id": "x", "summary": "y"})
        assert result.ok
        assert any("binaries" in w for w in result.warnings)


class TestCompiler:
    def _write(self, root, name, text):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_a_tree(self, tmp_path):
        self._write(tmp_path, "web/ffuf.yaml", "id: ffuf\nsummary: fuzzer\nbinaries: [ffuf]\n")
        self._write(tmp_path, "recon/nmap.yaml", "id: nmap\nsummary: scanner\nbinaries: [nmap]\n")
        report = load_source_tree(tmp_path)
        assert {t.id for t in report.tools} == {"ffuf", "nmap"}
        assert report.ok

    def test_duplicate_ids_are_an_error(self, tmp_path):
        self._write(tmp_path, "a/x.yaml", "id: x\n")
        self._write(tmp_path, "b/x.yaml", "id: x\n")
        report = load_source_tree(tmp_path)
        assert any("duplicate id" in e for e in report.errors)

    def test_broken_yaml_names_the_file(self, tmp_path):
        self._write(tmp_path, "bad.yaml", "id: [unclosed\n")
        report = load_source_tree(tmp_path)
        assert any("bad.yaml" in e for e in report.errors)

    def test_underscore_prefixed_files_are_skipped(self, tmp_path):
        self._write(tmp_path, "_template.yaml", "id: template\n")
        self._write(tmp_path, "real.yaml", "id: real\n")
        report = load_source_tree(tmp_path)
        assert [t.id for t in report.tools] == ["real"]

    def test_a_file_may_hold_several_entries(self, tmp_path):
        self._write(tmp_path, "many.yaml", "- id: a\n- id: b\n")
        report = load_source_tree(tmp_path)
        assert {t.id for t in report.tools} == {"a", "b"}


class TestShippedCatalogSource:
    """The real catalog in this repository must always compile."""

    def test_repository_catalog_is_valid(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "catalog"
        if not root.is_dir():
            pytest.skip("catalog source tree not present")
        report = load_source_tree(root)
        assert report.errors == []
        assert len(report.tools) > 100

    def test_curated_entries_have_real_metadata(self):
        """Guards against the 0.3 failure mode: names with nothing attached."""
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "catalog"
        if not root.is_dir():
            pytest.skip("catalog source tree not present")
        report = load_source_tree(root)
        multi = [t for t in report.tools if len(t.providers) > 1]
        assert len(multi) >= 30, "curated core should offer several install routes"
        described = [t for t in report.tools if t.summary]
        assert len(described) >= 50


class TestNoChecksumFieldPointsAtNothing:
    """hayabusa and velociraptor both declared `checksums: '*checksums*.txt'`
    against releases that have never published such a file, confirmed live
    against the GitHub API -- which made every install fail outright with
    "checksum file is not in the release assets" rather than only skip
    verification. Pinned here so a future edit cannot silently reintroduce a
    checksums pattern that matches nothing.
    """

    def test_hayabusa_declares_no_checksums(self):
        from pathlib import Path

        import yaml

        entry = yaml.safe_load(
            (Path(__file__).resolve().parent.parent / "catalog" / "detection" / "hayabusa.yaml")
            .read_text(encoding="utf-8")
        )
        route = entry["install"][0]
        assert "checksums" not in route, route

    def test_velociraptor_declares_no_checksums(self):
        from pathlib import Path

        import yaml

        entry = yaml.safe_load(
            (
                Path(__file__).resolve().parent.parent
                / "catalog"
                / "incident-response"
                / "velociraptor.yaml"
            ).read_text(encoding="utf-8")
        )
        route = entry["install"][0]
        assert "checksums" not in route, route


class TestSignatureValidation:
    """A signature block is checked when the catalog is validated, so a typo
    fails review rather than an install on someone else's machine -- by which
    point the artifact has already been downloaded."""

    def _entry(self, signature):
        return {
            "id": "signed-tool",
            "summary": "A tool that publishes signatures",
            "install": [
                {
                    "provider": "github",
                    "repo": "owner/signed-tool",
                    "checksums": "*SHA256SUMS",
                    "signature": signature,
                }
            ],
        }

    def _errors(self, signature):
        from loadout.catalog.schema import validate_entry

        return validate_entry(self._entry(signature), origin="test.yaml").errors

    def test_a_good_block_validates(self):
        assert self._errors(
            {
                "type": "gpg",
                "asset": "*SHA256SUMS.asc",
                "public_key": "-----BEGIN PGP PUBLIC KEY BLOCK-----",
                "signs": "checksums",
            }
        ) == []

    def test_a_typo_in_the_type_is_caught_at_review_time(self):
        errors = " ".join(self._errors({"type": "pgp", "asset": "*.asc"}))
        assert "unknown type" in errors

    def test_a_gpg_block_with_no_key_is_caught_at_review_time(self):
        errors = " ".join(self._errors({"type": "gpg", "asset": "*.asc"}))
        assert "requires 'public_key'" in errors

    def test_a_signature_that_is_not_a_mapping_is_caught(self):
        errors = " ".join(self._errors("gpg"))
        assert "must be a mapping" in errors

    def test_entries_without_signatures_are_unaffected(self):
        from loadout.catalog.schema import validate_entry

        entry = self._entry(None)
        entry["install"][0].pop("signature")
        assert validate_entry(entry, origin="test.yaml").errors == []


# ---------------------------------------------------------------------------
# Install routes that name a real package
# ---------------------------------------------------------------------------


def _pipx_routes() -> list[tuple[str, str, dict]]:
    """(tool id, package, method) for every pipx route in the YAML source."""
    import yaml

    root = Path(__file__).resolve().parent.parent / "catalog"
    found = []
    for path in root.rglob("*.yaml"):
        entry = yaml.safe_load(path.read_text(encoding="utf-8"))
        for method in entry.get("install") or []:
            if method.get("provider") == "pipx":
                found.append((entry["id"], method["package"], method))
    return sorted(found)


def test_no_pipx_route_points_at_a_package_that_does_not_exist():
    """netexec had one. There is no `netexec` on PyPI -- upstream ships through
    apt and its own installer -- so the route was a package nobody published,
    and it only stayed hidden because apt is tried first on Kali.

    The list is what was verified against the live index; adding a route means
    checking it and adding it here, which is the point.
    """
    verified = {
        "adversarial-robustness-toolbox", "agentic-security", "androguard", "checkov",
        "counterfit", "fickling", "frida-tools", "garak", "holehe", "impacket",
        "ldapdomaindump", "maigret", "mitmproxy", "mobsf", "modelscan", "objection",
        "picklescan", "prowler", "pyrit", "scoutsuite", "sigma-cli", "socialscan",
        "sqlmap", "textattack", "volatility3",
    }
    unverified = sorted({pkg for _t, pkg, _m in _pipx_routes()} - verified)
    assert unverified == [], (
        f"unverified pipx packages: {unverified} -- confirm each exists on PyPI, "
        "is not a reserved-name placeholder, and installs a command"
    )


def test_no_pipx_route_points_at_a_reserved_name_placeholder():
    """`spiderfoot` on PyPI is a 2026 placeholder whose own summary reads
    "Reserved name placeholder. No functionality."; `theHarvester` is a single
    0.0.1 from 2019 against a project now on 4.x. Both had routes inferred from
    the tool id when the catalog was seeded. A security tool installing an empty
    package under a name someone else controls is the wrong failure to have.
    """
    known_placeholders = {"spiderfoot", "theHarvester"}
    packages = {pkg for _t, pkg, _m in _pipx_routes()}
    assert not (packages & known_placeholders)


def test_every_requires_python_specifier_parses():
    """A typo here would silently rule the route out on every machine."""
    from loadout.pyversion import validate_specifier

    for tool_id, _pkg, method in _pipx_routes():
        specifier = method.get("requires_python")
        if specifier:
            validate_specifier(specifier)  # raises InvalidSpecifier on a typo
            assert tool_id


# ---------------------------------------------------------------------------
# Recommendations that are still alive
# ---------------------------------------------------------------------------


def _all_entries() -> dict:
    import yaml

    root = Path(__file__).resolve().parent.parent / "catalog"
    return {
        entry["id"]: entry
        for entry in (
            yaml.safe_load(p.read_text(encoding="utf-8")) for p in root.rglob("*.yaml")
        )
    }


def test_no_tool_is_deprecated_in_favour_of_another_deprecated_tool():
    """rebuff and vigil-llm pointed at llm-guard, which Protect AI then
    archived, so the catalog was routing two dead tools to a third. A successor
    that is itself dead is worse than no successor, because `loadout audit`
    prints it as the thing to move to.
    """
    entries = _all_entries()
    chains = [
        f"{tool_id} -> {entry['deprecated_by']}"
        for tool_id, entry in entries.items()
        if entry.get("deprecated_by") and entries.get(entry["deprecated_by"], {}).get("deprecated_by")
    ]
    assert chains == [], f"deprecated tools pointing at deprecated tools: {chains}"


def test_a_deprecated_tool_is_never_named_by_a_bundled_loadout():
    """Applying a loadout is a recommendation to install every tool in it."""
    import yaml

    entries = _all_entries()
    root = Path(__file__).resolve().parent.parent / "loadout" / "data" / "loadouts"
    offenders = []
    for path in sorted(root.glob("*.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        for tool_id in manifest.get("tools") or []:
            if entries.get(tool_id, {}).get("deprecated_by"):
                offenders.append(f"{path.name}: {tool_id}")
    assert offenders == [], f"loadouts recommending superseded tools: {offenders}"


def test_every_tool_upstream_has_archived_says_so():
    """Confirmed against the GitHub API with tools/audit_upstream.py, which is
    how this list was produced. `archived` is set by the project's own owners,
    so it is a statement and not an inference -- but it is only visible to
    someone who goes looking, which is what the audit script is for.

    Re-run it when adding entries; a newly archived project belongs here.
    """
    known_archived = {
        "crackmapexec", "dumpsterdiver", "goldeneye", "google-nexus-tools",
        "havoc", "jboss-autopwn", "koadic", "libsmali-java", "llm-guard",
        "maryam", "mdk3", "powersploit", "pyrit", "rebuff", "sprayingtoolkit",
        "stegcracker", "veil", "vigil-llm", "webscarab",
    }
    entries = _all_entries()
    silent = [
        tool_id
        for tool_id in sorted(known_archived)
        if tool_id in entries
        and "No longer maintained" not in (entries[tool_id].get("description") or "")
    ]
    assert silent == [], f"archived upstream but the entry does not say so: {silent}"


# ---------------------------------------------------------------------------
# The hackingtool diff -- gem/pipx routes with binaries verified on Kali
# ---------------------------------------------------------------------------


def test_every_gem_route_records_the_binary_it_actually_installs():
    """`gem install haiti-hash` installs a binary named `haiti`, not
    `haiti-hash` -- confirmed by installing it and reading the gem's bin
    directory, the same discipline as the pipx binaries check below."""
    import yaml

    root = Path(__file__).resolve().parent.parent / "catalog"
    for path in root.rglob("*.yaml"):
        entry = yaml.safe_load(path.read_text(encoding="utf-8"))
        for method in entry.get("install") or []:
            if method["provider"] == "gem":
                assert entry.get("binaries"), f"{entry['id']} has a gem route but no binaries"


def test_no_entry_duplicates_a_tool_already_in_the_catalog_by_repo():
    """wifite2, social-engineer-toolkit and hping were all candidates from the
    hackingtool diff that turned out to already be catalogued under a
    different id -- `wifite`, `set` and `hping3` respectively, all confirmed by
    matching the upstream repository rather than the name. Two entries
    pointing at the same upstream project is confusing in search results and
    in `loadout show`.
    """
    import yaml

    root = Path(__file__).resolve().parent.parent / "catalog"
    by_repo: dict[str, list[str]] = {}
    for path in root.rglob("*.yaml"):
        entry = yaml.safe_load(path.read_text(encoding="utf-8"))
        repo = (entry.get("repo") or "").lower()
        if repo:
            by_repo.setdefault(repo, []).append(entry["id"])
    dupes = {repo: ids for repo, ids in by_repo.items() if len(ids) > 1}
    assert dupes == {}, f"multiple catalog entries for the same repository: {dupes}"


class TestNameRelevance:
    """Typing a tool's own name has to surface that tool first.

    Found by actually searching the real, built catalog rather than the
    fixture: `sqlmap` put `dsss` (a different tool whose blurb happens to
    quote "sqlmap") above sqlmap itself, and `nmap` put `nmapsi4` above nmap.
    bm25 is a pure term-frequency model over one merged text blob, so a short
    document that mentions the query term twice can outscore a long one
    whose id *is* the term but only says it once.
    """

    def test_an_exact_id_match_always_ranks_first(self, tmp_path):
        """The real case: nmapsi4's summary says "nmap" twice in a document
        shorter than nmap's own, which let raw term frequency put the decoy
        above the tool whose name was typed."""
        decoy = Tool(
            id="nmapsi4",
            summary="graphical interface to nmap, the nmap network scanner",
            description="",
        )
        real = Tool(
            id="nmap",
            summary="Network discovery and service/version fingerprinting",
            description=(
                "Nmap is a utility for network exploration or security auditing "
                "with a great many long-form options and flags described here."
            ),
        )
        path = tmp_path / "c.db"
        build_catalog(path, [decoy, real], source="test")
        with CatalogStore(path) as store:
            assert next(t.id for t in store.search("nmap")) == "nmap"

    def test_a_short_document_that_merely_mentions_the_term_does_not_win(self, tmp_path):
        """sqlmap vs. dsss, reproduced directly rather than trusted from
        memory: a short entry whose blurb name-drops the query term must not
        outrank the entry whose id it is."""
        mentions_it = Tool(
            id="dsss",
            summary="Minimal SQLi scanner, a reference next to sqlmap sqlmap",
            description="",
        )
        the_tool = Tool(
            id="sqlmap",
            summary="Automated SQL injection detection and exploitation",
            description="A full-featured tool with many switches and options.",
        )
        path = tmp_path / "c.db"
        build_catalog(path, [mentions_it, the_tool], source="test")
        with CatalogStore(path) as store:
            assert next(t.id for t in store.search("sqlmap")) == "sqlmap"

    def test_the_exact_match_override_is_case_and_whitespace_insensitive(self, tmp_path):
        tool = Tool(id="nmap", summary="Network discovery")
        decoy = Tool(id="nmap-extra", summary="nmap nmap nmap addon scripts")
        path = tmp_path / "c.db"
        build_catalog(path, [decoy, tool], source="test")
        with CatalogStore(path) as store:
            for query in ("NMAP", "  nmap  ", "Nmap"):
                assert next(t.id for t in store.search(query)) == "nmap", query

    def test_the_override_does_not_apply_when_nothing_matches_exactly(self, tmp_path):
        """`metasploit` (no tool has exactly that id) must still return
        results, ranked by relevance as before -- the override only fires on
        a real exact match, it does not suppress ordinary search."""
        a = Tool(id="metasploit-framework", summary="Exploit development framework")
        b = Tool(id="framework2", summary="Metasploit Framework 2")
        path = tmp_path / "c.db"
        build_catalog(path, [a, b], source="test")
        with CatalogStore(path) as store:
            ids = {t.id for t in store.search("metasploit")}
            assert ids == {"metasploit-framework", "framework2"}

    def test_the_fts_index_carries_a_title_column_separate_from_the_blob(self, tmp_path):
        """Pins the schema shape the ranking fix depends on: without a
        distinct, higher-weighted title column, id-match relevance has no
        signal to key off beyond raw term frequency in one merged field."""
        path = tmp_path / "c.db"
        build_catalog(path, [Tool(id="nmap", summary="Network discovery")], source="test")
        with CatalogStore(path) as store:
            cols = {
                row[1]
                for row in store._conn.execute("PRAGMA table_info(tools_fts)").fetchall()
            }
        assert "title" in cols
        assert "blob" in cols


def test_generic_system_utilities_are_not_tagged_into_an_unrelated_security_category():
    """`net-tools`, `tmux`, `screen` and `subversion` were tagged `wireless`
    with no plausible connection -- found by clicking the wireless facet in
    the real browser and seeing a terminal multiplexer at the top of a list
    of RF and Bluetooth tools. Category tags come from a bulk import and were
    never reviewed by hand; this pins the four confirmed wrong ones so they
    cannot silently come back."""
    import yaml

    root = Path(__file__).resolve().parent.parent / "catalog"
    known_bad = {
        "net-tools": "wireless",
        "tmux": "wireless",
        "screen": "wireless",
        "subversion": "wireless",
    }
    for tool_id, bad_category in known_bad.items():
        matches = list(root.rglob(f"{tool_id}.yaml"))
        assert matches, tool_id
        entry = yaml.safe_load(matches[0].read_text(encoding="utf-8"))
        assert bad_category not in (entry.get("categories") or []), (
            f"{tool_id} is tagged {bad_category!r} again"
        )

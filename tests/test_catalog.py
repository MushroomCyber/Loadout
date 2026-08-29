"""Catalog store, schema validation and the compiler."""

from __future__ import annotations

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

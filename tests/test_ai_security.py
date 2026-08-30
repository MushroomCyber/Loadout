"""The `ai-security` category.

Two things here are worth locking down rather than trusting to review: that
every install route in this category points at a package that really exists
under that name, and that the entries which deliberately have *no* install
route keep saying so instead of quietly acquiring a guessed one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "ai-security"

#: Entries with no packaged install. Both are clone-and-run projects: neither
#: is on PyPI or npm, and Dark-Moon's GitHub releases carry no assets. Inventing
#: a route for them would produce a confident failure on someone's machine.
NO_INSTALL_ROUTE = {"hexstrike-ai", "dark-moon"}


def entries() -> list[dict]:
    return [
        yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(CATALOG.glob("*.yaml"))
    ]


def test_the_category_directory_exists_and_is_populated():
    assert CATALOG.is_dir()
    assert len(entries()) >= 12


def test_ai_security_is_a_known_category():
    from loadout.catalog.schema import CATEGORIES

    assert "ai-security" in CATEGORIES
    assert CATEGORIES["ai-security"] == "AI & LLM security"


def test_every_entry_validates():
    from loadout.catalog.schema import validate_entry

    for entry in entries():
        result = validate_entry(entry, origin=f"{entry['id']}.yaml")
        assert result.errors == [], f"{entry['id']}: {result.errors}"


def test_every_entry_is_in_the_ai_security_category():
    for entry in entries():
        assert "ai-security" in entry["categories"], entry["id"]


def test_every_entry_records_a_homepage_and_licence():
    """These are new, fast-moving projects. Someone deciding whether to run one
    on a client engagement needs to reach the source and know the licence."""
    for entry in entries():
        assert entry.get("homepage"), f"{entry['id']} has no homepage"
        assert entry.get("license"), f"{entry['id']} has no licence"


def test_the_clone_and_run_tools_declare_no_install_route():
    """Rather than a guessed one. They stay searchable and `loadout show` gives
    the repository; pretending they are installable would fail on a machine."""
    by_id = {e["id"]: e for e in entries()}
    for tool_id in NO_INSTALL_ROUTE:
        assert tool_id in by_id, f"{tool_id} is missing from the category"
        assert by_id[tool_id].get("install") == [], (
            f"{tool_id} gained an install route -- verify the package really "
            "exists under that name before allowing this"
        )


def test_everything_else_has_an_install_route():
    for entry in entries():
        if entry["id"] in NO_INSTALL_ROUTE:
            continue
        assert entry.get("install"), f"{entry['id']} has no install method"


def test_install_routes_only_use_providers_that_exist():
    from loadout.providers import known_provider_names

    known = known_provider_names()
    for entry in entries():
        for method in entry.get("install") or []:
            assert method["provider"] in known, entry["id"]


def test_pyrit_disambiguates_itself_from_the_wireless_tool():
    """`pyrit` in a pentest catalog reads as the WPA-PSK cracker. Someone
    searching for that must not install an LLM harness thinking it is."""
    by_id = {e["id"]: e for e in entries()}
    description = by_id["pyrit"]["description"].lower()
    assert "wpa" in description
    assert "not to be confused" in description


def test_model_file_scanners_are_also_filed_under_malware():
    """A pickle is executable code, so a scanner for one is doing the same job
    as a file scanner -- someone looking under malware analysis should find it."""
    by_id = {e["id"]: e for e in entries()}
    for tool_id in ("modelscan", "picklescan"):
        assert "malware" in by_id[tool_id]["categories"], tool_id


def test_alternatives_point_at_tools_that_exist(catalog_source_ids):
    """A dangling alternative makes `loadout alt` recommend nothing."""
    for entry in entries():
        for alternative in entry.get("alternatives") or []:
            assert alternative in catalog_source_ids, (
                f"{entry['id']} lists unknown alternative {alternative!r}"
            )


def test_the_ai_redteam_loadout_only_names_real_tools(catalog_source_ids):
    root = Path(__file__).resolve().parent.parent / "loadout" / "data" / "loadouts"
    manifest = yaml.safe_load((root / "ai-redteam.yaml").read_text(encoding="utf-8"))
    assert manifest["slug"] == "ai-redteam"
    unknown = [t for t in manifest["tools"] if t not in catalog_source_ids]
    assert unknown == [], f"ai-redteam names tools not in the catalog: {unknown}"


def test_the_loadout_only_names_installable_tools(catalog_source_ids):
    """Applying a loadout installs it. A clone-and-run entry in there would
    stop the apply with nothing to run."""
    root = Path(__file__).resolve().parent.parent / "loadout" / "data" / "loadouts"
    manifest = yaml.safe_load((root / "ai-redteam.yaml").read_text(encoding="utf-8"))
    assert not NO_INSTALL_ROUTE.intersection(manifest["tools"])


@pytest.fixture(scope="module")
def catalog_source_ids() -> set[str]:
    """Every id in the YAML source tree, not the compiled database.

    The source tree is what a pull request changes, so cross-references have to
    hold there or the build is the first thing to notice.
    """
    root = Path(__file__).resolve().parent.parent / "catalog"
    return {
        yaml.safe_load(path.read_text(encoding="utf-8"))["id"]
        for path in root.rglob("*.yaml")
    }

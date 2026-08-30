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

#: Entries with no packaged install, and why each one has none. Verified on a
#: real box: `pipx install` refuses any package that declares no console
#: script, so a pipx route for a library is a confident failure on a user's
#: machine rather than a working install.
NO_INSTALL_ROUTE = {
    # Clone-and-run: not on PyPI or npm, and Dark-Moon's releases carry no
    # assets.
    "hexstrike-ai",
    "dark-moon",
    # Libraries with no console script -- pipx says "No apps associated".
    "llm-guard",
    "adversarial-robustness-toolbox",
    "giskard",
    "rebuff",
    # Never published to PyPI.
    "vigil-llm",
}


def entries() -> list[dict]:
    return [
        yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(CATALOG.glob("*.yaml"))
    ]


def test_the_category_directory_exists_and_is_populated():
    assert CATALOG.is_dir()
    assert len(entries()) >= 16


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


def test_the_unpackaged_tools_declare_no_install_route():
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


def test_every_pipx_route_names_a_package_that_installs_a_command():
    """pipx installs applications. A package declaring no console script is one
    pipx refuses outright -- two entries shipped with exactly that mistake
    before this test existed, so the rule is pinned rather than remembered.

    The list is what each wheel declares, checked against PyPI.
    """
    provides_a_command = {
        "garak",
        "textattack",
        "fickling",
        "picklescan",
        "pyrit",
        "counterfit",
        "modelscan",
        "agentic-security",
    }
    for entry in entries():
        for method in entry.get("install") or []:
            if method["provider"] == "pipx":
                assert method["package"] in provides_a_command, (
                    f"{entry['id']} declares a pipx route for "
                    f"{method['package']!r}, which must be known to install a "
                    "command -- verify with `pipx install` before adding it"
                )


def test_every_entry_with_a_pipx_route_records_its_binaries():
    """Without them `loadout verify` falls back to guessing the binary from the
    tool id, and pyrit installs pyrit_scan, not pyrit."""
    for entry in entries():
        routes = entry.get("install") or []
        if any(m["provider"] == "pipx" for m in routes):
            assert entry.get("binaries"), f"{entry['id']} has a pipx route but no binaries"


def test_superseded_tools_point_at_a_maintained_replacement():
    """`loadout audit` reads deprecated_by to tell someone what to move to."""
    by_id = {e["id"]: e for e in entries()}
    for tool_id in ("rebuff", "vigil-llm"):
        replacement = by_id[tool_id].get("deprecated_by")
        assert replacement, f"{tool_id} is unmaintained but names no replacement"
        assert replacement in by_id, f"{tool_id} points at unknown {replacement!r}"


def test_modelscan_declares_the_python_pin_that_makes_it_fail_on_kali():
    """Upstream pins requires_python >=3.10,<3.13 and Kali ships only 3.13.
    Declared, the planner refuses it with that sentence; undeclared, the user
    gets forty lines of pip output after the download has already happened."""
    by_id = {e["id"]: e for e in entries()}
    routes = by_id["modelscan"]["install"]
    pipx = [m for m in routes if m["provider"] == "pipx"]
    assert pipx, "modelscan lost its pipx route"
    assert pipx[0]["requires_python"] == ">=3.10,<3.13"

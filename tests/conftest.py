"""Shared fixtures.

Every test runs against a temporary XDG root, so a test run can never read or
write the developer's real catalog, state database or config.
"""

from __future__ import annotations

import pytest

from loadout.catalog.store import CatalogStore, build_catalog
from loadout.model import InstallMethod, Tool
from loadout.providers.base import ProviderStatus


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        target = tmp_path / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(target))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    from loadout import state

    state.reset_state_db()
    yield tmp_path
    state.reset_state_db()


@pytest.fixture(autouse=True)
def no_provider_cache():
    from loadout import providers

    providers.reset_detection_cache()
    yield
    providers.reset_detection_cache()


def make_tool(tool_id: str, **kwargs) -> Tool:
    methods = kwargs.pop("install", [{"provider": "apt", "package": tool_id}])
    return Tool(
        id=tool_id,
        summary=kwargs.pop("summary", f"{tool_id} summary"),
        categories=tuple(kwargs.pop("categories", ("recon",))),
        install=tuple(InstallMethod.from_dict(dict(m)) for m in methods),
        **kwargs,
    )


@pytest.fixture
def sample_tools() -> list[Tool]:
    return [
        make_tool(
            "nmap",
            summary="Network discovery and service fingerprinting",
            categories=["recon"],
            phases=("discovery",),
            binaries=("nmap",),
            tags=("port-scan",),
            install=[
                {"provider": "apt", "package": "nmap", "distros": ["kali", "debian"]},
                {"provider": "brew", "formula": "nmap"},
            ],
            alternatives=("masscan",),
        ),
        make_tool(
            "ffuf",
            summary="Fast web fuzzer for content discovery",
            categories=["web", "fuzzing"],
            phases=("discovery",),
            binaries=("ffuf",),
            tags=("fuzzing", "bug-bounty"),
            install=[
                {"provider": "apt", "package": "ffuf", "distros": ["kali"]},
                {"provider": "go", "module": "github.com/ffuf/ffuf/v2@latest"},
                {"provider": "github", "repo": "ffuf/ffuf", "checksums": "*checksums*.txt"},
            ],
        ),
        make_tool(
            "nuclei",
            summary="Template-driven vulnerability scanner",
            categories=["vuln-scan"],
            binaries=("nuclei",),
            install=[
                {"provider": "go", "module": "github.com/x/nuclei@latest", "priority": 20},
                {"provider": "apt", "package": "nuclei", "priority": 60},
            ],
        ),
        make_tool(
            "masscan",
            summary="Internet-scale port scanner",
            categories=["recon"],
            binaries=("masscan",),
            install=[{"provider": "apt", "package": "masscan"}],
        ),
    ]


@pytest.fixture
def catalog(tmp_path, sample_tools) -> CatalogStore:
    path = tmp_path / "catalog.db"
    build_catalog(path, sample_tools, source="test")
    store = CatalogStore(path)
    yield store
    store.close()


@pytest.fixture
def all_available() -> dict[str, ProviderStatus]:
    """Pretend every provider works, so planning is deterministic in CI."""
    from loadout.providers import all_providers

    return {
        name: ProviderStatus(name=name, available=True, version="test")
        for name in all_providers()
    }

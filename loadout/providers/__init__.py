"""Provider registry and machine detection.

``get_provider("apt")`` returns a singleton; ``available_providers()`` reports
what this machine can actually use. Detection is cached for the process because
it shells out to every toolchain and is called on most startup paths.
"""

from __future__ import annotations

import functools
import platform
from pathlib import Path

from .apt import AptProvider
from .base import CommandStep, Provider, ProviderStatus, PythonStep, Step
from .docker import DockerProvider
from .github import GithubReleaseProvider
from .lang import (
    BrewProvider,
    CargoProvider,
    GemProvider,
    GoProvider,
    NpmProvider,
    PipxProvider,
)

__all__ = [
    "CommandStep",
    "Provider",
    "ProviderStatus",
    "PythonStep",
    "Step",
    "all_providers",
    "available_providers",
    "detect_distro",
    "get_provider",
    "known_provider_names",
    "reset_detection_cache",
]

_REGISTRY: dict[str, Provider] = {}


def _build_registry() -> dict[str, Provider]:
    providers: list[Provider] = [
        AptProvider(),
        BrewProvider(),
        PipxProvider(),
        GoProvider(),
        CargoProvider(),
        GemProvider(),
        NpmProvider(),
        GithubReleaseProvider(),
        DockerProvider(),
    ]
    return {p.name: p for p in providers}


def all_providers() -> dict[str, Provider]:
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _build_registry()
    return _REGISTRY


def get_provider(name: str) -> Provider:
    try:
        return all_providers()[name.strip().lower()]
    except KeyError as exc:
        raise KeyError(f"unknown provider: {name!r}") from exc


def known_provider_names() -> set[str]:
    return set(all_providers())


@functools.lru_cache(maxsize=1)
def _detect_all() -> dict[str, ProviderStatus]:
    return {name: provider.detect() for name, provider in all_providers().items()}


def available_providers() -> dict[str, ProviderStatus]:
    """Every provider's detection result, cached for the process."""
    return dict(_detect_all())


def reset_detection_cache() -> None:
    """Drop cached detection -- used by tests and after a toolchain install."""
    _detect_all.cache_clear()


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def detect_distro() -> str:
    """Return a distro id: ``kali``, ``parrot``, ``debian``, ``arch``, ``macos``...

    Used to pick between install methods that declare ``distros:``. Falls back
    to the OS name so macOS and Windows still resolve to something usable.
    """
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"

    release = Path("/etc/os-release")
    if release.exists():
        try:
            fields: dict[str, str] = {}
            for line in release.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    fields[key.strip()] = value.strip().strip('"').strip("'")
            distro_id = fields.get("ID", "").lower()
            if distro_id:
                return distro_id
        except OSError:
            pass
    return system or "unknown"


@functools.lru_cache(maxsize=1)
def distro_family() -> str:
    """``debian``, ``arch``, ``rhel``, ``macos`` -- for coarse provider choices."""
    distro = detect_distro()
    debian_like = {"debian", "kali", "parrot", "ubuntu", "raspbian", "linuxmint", "pop"}
    arch_like = {"arch", "manjaro", "endeavouros", "blackarch"}
    rhel_like = {"fedora", "rhel", "centos", "rocky", "almalinux"}
    if distro in debian_like:
        return "debian"
    if distro in arch_like:
        return "arch"
    if distro in rhel_like:
        return "rhel"
    if distro == "macos":
        return "macos"
    return distro

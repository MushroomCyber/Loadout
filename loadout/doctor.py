"""``loadout doctor`` -- diagnose environment problems before they bite.

Each check is best-effort and never raises. Severity maps to the exit code, so
this is usable as a CI gate: ``ok``/``warn`` exit 0, ``fail`` exits 2.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("loadout.doctor")

SEVERITY_ORDER = {"ok": 0, "warn": 1, "fail": 2}


@dataclass
class CheckResult:
    name: str
    severity: str  # ok | warn | fail
    message: str
    remediation: str = ""


def _check_python() -> CheckResult:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        return CheckResult(
            "python", "fail",
            f"Python {major}.{minor} is below the 3.10 minimum.",
            "Install Python 3.10 or newer.",
        )
    return CheckResult("python", "ok", f"Python {major}.{minor}")


def _check_providers() -> CheckResult:
    from .providers import available_providers, detect_distro

    statuses = available_providers()
    usable = sorted(name for name, status in statuses.items() if status.available)
    if not usable:
        return CheckResult(
            "providers", "fail",
            "No package manager is usable, so nothing can be installed.",
            "Install at least one of: apt, brew, pipx, go, cargo, docker.",
        )
    if len(usable) == 1:
        return CheckResult(
            "providers", "warn",
            f"Only one installer available ({usable[0]}) on {detect_distro()}",
            "Tools that ship via other channels will be unreachable. "
            "Installing pipx and go widens coverage substantially.",
        )
    return CheckResult(
        "providers", "ok", f"{len(usable)} installers available: {', '.join(usable)}"
    )


def _check_catalog() -> CheckResult:
    try:
        from .catalog import open_catalog

        with open_catalog() as store:
            info = store.info()
            if info.tool_count == 0:
                return CheckResult(
                    "catalog", "fail", "Catalog is empty.", "Run `loadout catalog update`."
                )
            uncategorised = dict(store.facet_values("category")).get("other", 0)
            share = uncategorised / info.tool_count if info.tool_count else 0
            if share > 0.5:
                return CheckResult(
                    "catalog", "warn",
                    f"{info.tool_count} tools, but {share:.0%} are uncategorised",
                    "Run `loadout catalog update` on a Debian-based host to pull "
                    "categories and descriptions from APT metadata.",
                )
            return CheckResult(
                "catalog", "ok",
                f"{info.tool_count} tools (built {info.generated_at})",
            )
    except Exception as exc:
        return CheckResult(
            "catalog", "fail", f"Catalog unavailable: {exc}", "Run `loadout catalog update`."
        )


def _check_state_db() -> CheckResult:
    try:
        from .state import get_state_db

        database = get_state_db()
        installed = len(database.installed_ids())
        return CheckResult("state", "ok", f"{installed} tool(s) tracked at {database.path}")
    except Exception as exc:
        return CheckResult("state", "warn", f"State database not ready: {exc}")


def _check_privileges() -> CheckResult:
    from .policy import detect_privilege, has_cached_credentials

    privilege = detect_privilege()
    if privilege.is_root:
        return CheckResult("privileges", "ok", "running as root")
    if not privilege.sudo_path:
        return CheckResult(
            "privileges", "warn",
            "sudo is not on PATH; system-wide installs will fail.",
            "Install sudo, or rely on user-scoped providers (pipx, go, cargo, github).",
        )
    cached = has_cached_credentials(privilege)
    return CheckResult(
        "privileges", "ok",
        f"sudo available ({'credentials cached' if cached else 'will prompt'})",
    )


def _check_dpkg_lock() -> CheckResult:
    lock = Path("/var/lib/dpkg/lock-frontend")
    if not lock.exists():
        return CheckResult("dpkg-lock", "ok", "no dpkg lock present")
    if shutil.which("fuser") is None:
        return CheckResult("dpkg-lock", "ok", "lock file present, fuser unavailable to probe")
    try:
        fuser = shutil.which("fuser")
        if fuser is None:
            return CheckResult("dpkg-lock", "ok", "lock present, fuser unavailable")
        result = subprocess.run(  # noqa: S603 - resolved path, fixed argv
            [fuser, str(lock)], capture_output=True, text=True, timeout=3, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return CheckResult(
                "dpkg-lock", "warn",
                f"dpkg lock is held by PID {result.stdout.strip()}",
                "Wait for that apt process to finish; installs will fail until it does.",
            )
    except Exception as exc:
        logger.debug("fuser probe failed: %s", exc)
    return CheckResult("dpkg-lock", "ok", "dpkg lock is free")


def _check_apt_sources(
    sources_dir: Path | None = None,
    main_list: Path | None = None,
) -> CheckResult:
    """Understands both one-line and deb822 sources.

    Kali and Debian are moving to ``*.sources`` files with ``Types:``/``URIs:``
    stanzas. Globbing only ``*.list``, as the previous release did, quietly
    ignored the real configuration on a modern box and reported "clean".

    The paths are parameters so tests can point at a fixture directory. They
    used to be hardcoded, which meant a test could only fake them by patching
    ``Path`` itself -- and then still picked up the host's real
    ``/etc/apt/sources.list``, so the same test passed locally and failed on a
    CI runner that happened to have one.
    """
    sources_dir = sources_dir if sources_dir is not None else Path("/etc/apt/sources.list.d")
    main_list = main_list if main_list is not None else Path("/etc/apt/sources.list")

    if not sources_dir.exists() and not main_list.exists():
        return CheckResult("apt-sources", "ok", "not an APT system")

    suspicious: list[str] = []
    checked = 0

    one_line = [main_list]
    if sources_dir.is_dir():
        one_line += sorted(sources_dir.glob("*.list"))
    for path in one_line:
        if not path.exists():
            continue
        checked += 1
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.split()[0] not in ("deb", "deb-src"):
                    suspicious.append(f"{path.name}: {stripped[:50]}")
                elif "[trusted=yes]" in stripped:
                    suspicious.append(f"{path.name}: signature verification disabled")
        except OSError:
            continue

    if sources_dir.is_dir():
        for path in sorted(sources_dir.glob("*.sources")):
            checked += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for stanza in text.split("\n\n"):
                if not stanza.strip():
                    continue
                fields = {
                    line.split(":", 1)[0].strip().lower()
                    for line in stanza.splitlines()
                    if ":" in line and not line.startswith((" ", "\t", "#"))
                }
                if not {"types", "uris", "suites"} <= fields:
                    suspicious.append(f"{path.name}: incomplete deb822 stanza")
                if "trusted" in fields and "yes" in stanza.lower():
                    suspicious.append(f"{path.name}: signature verification disabled")

    if suspicious:
        return CheckResult(
            "apt-sources", "warn",
            f"{len(suspicious)} issue(s) across {checked} source file(s)",
            "; ".join(suspicious[:3]),
        )
    return CheckResult("apt-sources", "ok", f"{checked} source file(s) look sane")


def _check_disk_space() -> CheckResult:
    target = "/var" if Path("/var").exists() else str(Path.home())
    try:
        usage = shutil.disk_usage(target)
        free_gb = usage.free / 1e9
        if free_gb < 1:
            return CheckResult(
                "disk", "fail", f"only {free_gb:.1f} GB free on {target}",
                "Free space before installing anything.",
            )
        if free_gb < 5:
            return CheckResult(
                "disk", "warn", f"only {free_gb:.1f} GB free on {target}",
                "Consider `sudo apt-get clean`.",
            )
        return CheckResult("disk", "ok", f"{free_gb:.1f} GB free on {target}")
    except OSError as exc:
        return CheckResult("disk", "warn", f"could not stat {target}: {exc}")


def _check_path() -> CheckResult:
    """User-scoped providers are useless if their bin directory is not on PATH."""
    from .providers.github import user_bin_dir
    from .providers.lang import go_bin_dir

    path_entries = {Path(p).resolve() for p in os.environ.get("PATH", "").split(os.pathsep) if p}
    missing: list[str] = []
    for directory in (user_bin_dir(), go_bin_dir()):
        try:
            if directory.is_dir() and directory.resolve() not in path_entries:
                missing.append(str(directory))
        except OSError:
            continue
    if missing:
        return CheckResult(
            "path", "warn",
            f"{len(missing)} install directory not on PATH: {', '.join(missing)}",
            f"Add to your shell profile: export PATH=\"{missing[0]}:$PATH\"",
        )
    return CheckResult("path", "ok", "install directories are on PATH")


def _check_network() -> CheckResult:
    from . import env_flag

    if env_flag("LOADOUT_OFFLINE"):
        return CheckResult("network", "ok", "offline mode: no network needed")
    for host, port in (("api.github.com", 443), ("archive.kali.org", 443)):
        try:
            socket.create_connection((host, port), timeout=3).close()
            return CheckResult("network", "ok", f"{host} reachable")
        except OSError:
            continue
    return CheckResult(
        "network", "warn", "no reachable package source",
        "Check DNS and firewall, or run with --offline.",
    )


def _check_legacy_state() -> CheckResult:
    from .paths import LEGACY_FILES, needs_migration

    if not needs_migration():
        return CheckResult("migration", "ok", "no legacy kalitools state pending")
    found = [name for name in LEGACY_FILES.values() if (Path.home() / name).exists()]
    return CheckResult(
        "migration", "warn",
        f"{len(found)} kalitools file(s) not yet imported",
        "Run `loadout migrate` (it copies, never deletes).",
    )


CHECKS: list[Callable[[], CheckResult]] = [
    _check_python,
    _check_providers,
    _check_catalog,
    _check_state_db,
    _check_privileges,
    _check_path,
    _check_dpkg_lock,
    _check_apt_sources,
    _check_disk_space,
    _check_network,
    _check_legacy_state,
]


def run_all() -> list[CheckResult]:
    results: list[CheckResult] = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # pragma: no cover - a check must never abort the run
            results.append(CheckResult(check.__name__.strip("_"), "fail", f"check crashed: {exc}"))
    return results


def worst_severity(results: list[CheckResult]) -> str:
    if not results:
        return "ok"
    return max(results, key=lambda r: SEVERITY_ORDER.get(r.severity, 0)).severity

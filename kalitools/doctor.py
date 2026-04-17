"""`kalitools doctor` — diagnose common environment issues.

Each check returns a :class:`CheckResult` with a severity (``ok``, ``warn``,
``fail``). Checks are best-effort and never raise.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SEVERITY_ORDER = {"ok": 0, "warn": 1, "fail": 2}


@dataclass
class CheckResult:
    name: str
    severity: str  # "ok" | "warn" | "fail"
    message: str
    remediation: str | None = None


def _check_python() -> CheckResult:
    import sys

    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        return CheckResult(
            "python-version",
            "fail",
            f"Python {major}.{minor} is below the 3.10 minimum.",
            "Install Python 3.10+ (e.g. `apt install python3.11`).",
        )
    return CheckResult("python-version", "ok", f"Python {major}.{minor}")


def _check_sudo() -> CheckResult:
    if shutil.which("sudo") is None:
        return CheckResult(
            "sudo",
            "warn",
            "`sudo` is not on PATH; install/remove actions will fail.",
            "Install sudo or run kalitools as root.",
        )
    return CheckResult("sudo", "ok", "sudo present")


def _check_apt_tools() -> CheckResult:
    missing = [b for b in ("apt-get", "dpkg", "apt-cache") if shutil.which(b) is None]
    if missing:
        return CheckResult(
            "apt-binaries",
            "fail",
            f"Missing APT binaries: {', '.join(missing)}",
            "Run `apt install apt dpkg` on a Debian-based system.",
        )
    return CheckResult("apt-binaries", "ok", "apt-get, dpkg, apt-cache available")


def _check_dpkg_lock() -> CheckResult:
    lock_path = Path("/var/lib/dpkg/lock-frontend")
    if not lock_path.exists():
        return CheckResult("dpkg-lock", "ok", "no dpkg lock file")
    try:
        # fuser is cheap; if not present, skip.
        if shutil.which("fuser") is None:
            return CheckResult("dpkg-lock", "ok", "dpkg lock present, fuser unavailable to probe")
        res = subprocess.run(
            ["fuser", str(lock_path)],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0 and res.stdout.strip():
            return CheckResult(
                "dpkg-lock",
                "warn",
                f"dpkg lock held by: {res.stdout.strip()}",
                "Wait for the other apt process to finish, or investigate with `ps -fp <pid>`.",
            )
    except Exception:
        pass
    return CheckResult("dpkg-lock", "ok", "dpkg lock free")


def _check_sources_lists() -> CheckResult:
    sources_dir = Path("/etc/apt/sources.list.d")
    main = Path("/etc/apt/sources.list")
    bad: list[str] = []
    for p in [main, *sources_dir.glob("*.list")] if sources_dir.exists() else [main]:
        try:
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.split()[0] not in {"deb", "deb-src"}:
                    bad.append(f"{p}: {stripped[:60]}")
        except Exception:
            continue
    if bad:
        return CheckResult(
            "apt-sources",
            "warn",
            f"{len(bad)} suspicious line(s) in sources",
            "Review and fix: " + "; ".join(bad[:3]),
        )
    return CheckResult("apt-sources", "ok", "sources.list(.d) looks clean")


def _check_disk_space() -> CheckResult:
    try:
        st = os.statvfs("/var")
        free_bytes = st.f_bavail * st.f_frsize
        free_gb = free_bytes / 1e9
        if free_gb < 1:
            return CheckResult(
                "disk-space",
                "fail",
                f"Only {free_gb:.1f} GB free on /var",
                "Free disk space before installing packages.",
            )
        if free_gb < 5:
            return CheckResult(
                "disk-space",
                "warn",
                f"Only {free_gb:.1f} GB free on /var",
                "Consider `apt clean`.",
            )
        return CheckResult("disk-space", "ok", f"{free_gb:.1f} GB free on /var")
    except Exception as exc:
        return CheckResult("disk-space", "warn", f"Could not statvfs /var: {exc}")


def _check_network() -> CheckResult:
    if os.environ.get("KALITOOLS_OFFLINE"):
        return CheckResult("network", "ok", "offline mode: skipping network probe")
    try:
        socket.create_connection(("archive.kali.org", 443), timeout=3).close()
        return CheckResult("network", "ok", "archive.kali.org reachable")
    except Exception as exc:
        return CheckResult(
            "network",
            "warn",
            f"archive.kali.org unreachable: {exc}",
            "Check DNS / firewall, or run with --offline.",
        )


def _check_catalog() -> CheckResult:
    try:
        data_path = Path(__file__).parent / "data" / "tools_merged.json"
        if not data_path.exists():
            return CheckResult(
                "catalog",
                "warn",
                "Catalog file missing",
                "Run `kalitools catalog refresh`.",
            )
        payload = json.loads(data_path.read_text(encoding="utf-8"))
        tools = payload.get("tools", payload if isinstance(payload, list) else [])
        generated = payload.get("generated_at") if isinstance(payload, dict) else None
        return CheckResult(
            "catalog",
            "ok",
            f"{len(tools)} tools (generated_at={generated or 'unknown'})",
        )
    except Exception as exc:
        return CheckResult("catalog", "fail", f"Catalog unreadable: {exc}")


def _check_state_db() -> CheckResult:
    try:
        from .state import get_state_db

        db = get_state_db()
        with db._tx() as conn:  # pragma: no cover - private access ok for diag
            row = conn.execute("SELECT COUNT(*) AS n FROM tool_state").fetchone()
        return CheckResult("state-db", "ok", f"{row['n']} rows in tool_state")
    except Exception as exc:
        return CheckResult("state-db", "warn", f"state DB not ready: {exc}")


CHECKS: list[Callable[[], CheckResult]] = [
    _check_python,
    _check_apt_tools,
    _check_sudo,
    _check_dpkg_lock,
    _check_sources_lists,
    _check_disk_space,
    _check_network,
    _check_catalog,
    _check_state_db,
]


def run_all() -> list[CheckResult]:
    results: list[CheckResult] = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # pragma: no cover
            results.append(CheckResult(check.__name__, "fail", f"check crashed: {exc}"))
    return results


def worst_severity(results: list[CheckResult]) -> str:
    if not results:
        return "ok"
    return max(results, key=lambda r: SEVERITY_ORDER.get(r.severity, 0)).severity

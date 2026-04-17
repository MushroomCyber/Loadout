"""APT-based catalog builder for Kali Tools Manager.

This module treats APT — not kali.org HTML — as the authoritative source
for the tool catalog. Two parsers are provided:

* ``build_catalog_via_python_apt`` — uses the ``python3-apt`` system
  package when available. Fast, accurate, respects user pinning.
* ``build_catalog_via_dumpavail`` — subprocess fallback that parses
  ``apt-cache dumpavail`` output as RFC 822 stanzas via the stdlib
  ``email.parser`` module.

Both return a list of dicts with the same shape used by
[kalitools/model.Tool.from_dict](model.py).

Categorization prefers, in order:
1. Membership in a ``kali-tools-*`` meta-package (``apt-cache depends``).
2. Debtags (``Tag:`` field) with ``use::``, ``security::``, ``network::``.
3. Existing keyword hints from [kalitools/constants.CATEGORY_KEYWORD_HINTS](constants.py).
"""

from __future__ import annotations

import email.parser
import shutil
import subprocess
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from . import logger
from .constants import (
    CATEGORY_KEYWORD_HINTS,
    META_CATEGORY_SOURCES,
    get_subcategory_for,
)

# ---------------------------------------------------------------------------
# Debtags -> category slug
# ---------------------------------------------------------------------------

_DEBTAG_CATEGORY = {
    # security-domain debtags
    "security::forensics": "forensics",
    "security::cryptography": "crypto",
    "security::privacy": "crypto",
    "security::authentication": "password",
    "security::antivirus": "other",
    "security::ids": "sniffing",
    "security::firewall": "network",
    "security::log-analyzer": "forensics",
    # use-facet debtags
    "use::scanning": "recon",
    "use::checking": "vuln-scan",
    "use::analysing": "forensics",
    "use::monitor": "sniffing",
    # network-domain debtags
    "network::scanner": "recon",
    "network::sniffer": "sniffing",
    "network::server": "network",
    "network::client": "network",
    # protocol-domain debtags
    "protocol::http": "web",
    "protocol::https": "web",
}


def _debtag_to_category(tags: Sequence[str]) -> str | None:
    for tag in tags:
        for prefix, cat in _DEBTAG_CATEGORY.items():
            if tag.startswith(prefix):
                return cat
    return None


def _keyword_category(name: str, description: str) -> str | None:
    haystack = f"{name} {description}".lower()
    best: tuple[str, int] | None = None
    for cat, keywords in CATEGORY_KEYWORD_HINTS.items():
        hits = sum(1 for k in keywords if k in haystack)
        if hits and (best is None or hits > best[1]):
            best = (cat, hits)
    return best[0] if best else None


# ---------------------------------------------------------------------------
# Meta-package expansion via apt-cache depends
# ---------------------------------------------------------------------------

def discover_meta_membership(meta_names: Iterable[str]) -> dict[str, tuple[str, str]]:
    """For each ``kali-tools-*`` meta-package, return ``{pkg: (category, subcategory)}``.

    Uses ``apt-cache depends --recurse --no-recommends --no-suggests
    --no-conflicts --no-breaks --no-replaces --no-enhances`` which is
    fast and avoids resolver side-effects.
    """
    membership: dict[str, tuple[str, str]] = {}
    if not shutil.which("apt-cache"):
        return membership

    for meta in meta_names:
        cat_sub = META_CATEGORY_SOURCES.get(meta)
        if not cat_sub:
            continue
        category, subcategory = cat_sub
        try:
            result = subprocess.run(
                [
                    "apt-cache", "depends",
                    "--recurse",
                    "--no-recommends", "--no-suggests", "--no-conflicts",
                    "--no-breaks", "--no-replaces", "--no-enhances",
                    "--important",
                    meta,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug("apt-cache depends %s failed: %s", meta, exc)
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith(("Depends:", "|Depends:", "PreDepends:", "<")):
                continue
            # recurse output prints package names on their own lines
            if line.startswith(("Reverse", "  ", "\t")):
                continue
            if " " in line or ":" in line:
                continue
            # skip the meta itself and any kali-tools-* expansions
            if line == meta or line.startswith("kali-tools-"):
                continue
            # first writer wins; meta order in META_CATEGORY_SOURCES is authoritative
            membership.setdefault(line, (category, subcategory))
    return membership


# ---------------------------------------------------------------------------
# Catalog builders
# ---------------------------------------------------------------------------

def _post_process(
    entries: list[dict[str, Any]],
    membership: dict[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in entries:
        name = entry.get("name", "").strip()
        if not name:
            continue
        tags: list[str] = entry.get("_tags", []) or []
        description = entry.get("description", "") or ""
        category = ""
        subcategory = ""
        if name in membership:
            category, subcategory = membership[name]
        if not category:
            category = _debtag_to_category(tags) or ""
        if not category:
            category = _keyword_category(name, description) or "other"
        if not subcategory:
            subcategory = get_subcategory_for(name, category)
        entry["category"] = category
        entry["subcategory"] = subcategory or ""
        entry.pop("_tags", None)
        entry.setdefault("installed", False)
        entry.setdefault("commands", [entry["name"]])
        entry.setdefault("subpackages", [])
        entry.setdefault("source", "apt")
        out.append(entry)
    return out


def build_catalog_via_python_apt(
    *,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Build a catalog using the ``python3-apt`` system package.

    Returns ``None`` when ``python-apt`` is not importable; callers should
    fall back to :func:`build_catalog_via_dumpavail`.
    """
    try:
        import apt  # type: ignore
    except ImportError:
        return None

    try:
        cache = apt.Cache()
    except Exception as exc:  # pragma: no cover - apt init varies wildly
        logger.warning("python-apt cache init failed: %s", exc)
        return None

    total = len(cache)
    entries: list[dict[str, Any]] = []
    for i, pkg in enumerate(cache):
        if progress is not None and (i % 500 == 0 or i == total - 1):
            try:
                progress(i + 1, total)
            except Exception:
                pass
        try:
            candidate = pkg.candidate
            if candidate is None:
                continue
            name = pkg.name
            description = (candidate.summary or "").strip()
            installed_size = int(candidate.installed_size or 0)
            tags: list[str] = []
            try:
                record = candidate.record
                raw_tags = record.get("Tag") if record else ""
                if raw_tags:
                    tags = [t.strip() for t in raw_tags.replace("\n", "").split(",") if t.strip()]
            except Exception:
                tags = []
            entries.append({
                "name": name,
                "description": description,
                "size": installed_size,
                "commands": [name],
                "subpackages": [],
                "source": "apt",
                "metadata": {
                    "homepage": candidate.homepage or "",
                    "section": candidate.section or "",
                    "version": candidate.version or "",
                },
                "_tags": tags,
            })
        except Exception:
            continue

    membership = discover_meta_membership(META_CATEGORY_SOURCES.keys())
    return _post_process(entries, membership)


def build_catalog_via_dumpavail(
    *,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Build a catalog by parsing ``apt-cache dumpavail``.

    This runs even on systems without ``python3-apt`` as long as
    ``apt-cache`` is present. Only stanzas that look like real packages
    are emitted.
    """
    if not shutil.which("apt-cache"):
        return None

    try:
        proc = subprocess.run(
            ["apt-cache", "dumpavail"],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("apt-cache dumpavail failed: %s", exc)
        return None
    if proc.returncode != 0:
        return None

    raw = proc.stdout.decode("utf-8", errors="replace")

    # Split into stanzas
    stanzas = raw.split("\n\n")
    total = len(stanzas)
    parser = email.parser.HeaderParser()
    entries: list[dict[str, Any]] = []
    for i, stanza in enumerate(stanzas):
        if progress is not None and (i % 500 == 0 or i == total - 1):
            try:
                progress(i + 1, total)
            except Exception:
                pass
        stanza = stanza.strip()
        if not stanza:
            continue
        try:
            msg = parser.parsestr(stanza)
        except Exception:
            continue
        name = (msg.get("Package") or "").strip()
        if not name:
            continue
        description = (msg.get("Description-en") or msg.get("Description") or "").strip()
        # Keep only the summary (first line)
        description_summary = description.splitlines()[0] if description else ""
        raw_tags = msg.get("Tag") or ""
        tags = [t.strip() for t in raw_tags.replace("\n", "").split(",") if t.strip()]
        size_str = msg.get("Installed-Size") or "0"
        try:
            size_kb = int(size_str)
        except (TypeError, ValueError):
            size_kb = 0
        entries.append({
            "name": name,
            "description": description_summary,
            "size": size_kb * 1024,
            "commands": [name],
            "subpackages": [],
            "source": "apt",
            "metadata": {
                "homepage": msg.get("Homepage") or "",
                "section": msg.get("Section") or "",
                "version": msg.get("Version") or "",
            },
            "_tags": tags,
        })

    # De-duplicate (dumpavail prints every version)
    unique: dict[str, dict[str, Any]] = {}
    for e in entries:
        unique[e["name"]] = e
    membership = discover_meta_membership(META_CATEGORY_SOURCES.keys())
    return _post_process(list(unique.values()), membership)


def build_catalog(
    *,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Preferred entry point. Tries python-apt first, then dumpavail."""
    result = build_catalog_via_python_apt(progress=progress)
    if result:
        return result
    return build_catalog_via_dumpavail(progress=progress)


def filter_kali_tools(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only packages that look like Kali tools.

    Heuristic: member of a ``kali-tools-*`` meta-package OR has a
    security/network/use debtag OR its category was matched above by
    keyword hints (i.e. ``category`` is not ``other`` from the start).

    Callers can skip this if they want the full APT universe.
    """
    membership = discover_meta_membership(META_CATEGORY_SOURCES.keys())
    kept: list[dict[str, Any]] = []
    for e in entries:
        if e["name"] in membership:
            kept.append(e)
            continue
        if e.get("category") and e["category"] != "other":
            kept.append(e)
    return kept


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def installed_packages_via_dpkg() -> list[str]:
    """Return the list of currently-installed packages via ``dpkg-query``."""
    if not shutil.which("dpkg-query"):
        return []
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${binary:Package}\\t${db:Status-Status}\\n"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    out: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        name, status = parts
        if status.strip() == "installed":
            out.append(name.split(":")[0])
    return out


def commands_via_dpkg(pkg: str, *, max_commands: int = 12) -> list[str]:
    """Best-effort list of executable entry points installed by ``pkg``.

    Uses ``dpkg -L`` then filters paths under ``*/bin/*`` or ``*/sbin/*``.
    """
    if not shutil.which("dpkg"):
        return []
    try:
        proc = subprocess.run(
            ["dpkg", "-L", pkg],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    cmds: list[str] = []
    for line in proc.stdout.splitlines():
        p = line.strip()
        if not p:
            continue
        if ("/bin/" in p or "/sbin/" in p) and not p.endswith("/"):
            name = Path(p).name
            if name and name not in cmds:
                cmds.append(name)
        if len(cmds) >= max_commands:
            break
    return cmds

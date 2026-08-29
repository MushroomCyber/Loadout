"""Build catalog entries from APT metadata.

APT already knows the summary, homepage, section, version and installed size of
every package on the system. The previous release parsed all of that and then
persisted only the name, which is why the shipped catalog had descriptions for
29 of 764 tools.

Categorisation is now evidence-based and ordered by trust:

1. **Meta-package membership.** ``kali-tools-web``, ``kali-tools-forensics`` and
   friends *are* Kali's own taxonomy, curated by the Kali team.
2. **Debtags.** ``security::forensics``, ``network::scanner`` -- maintained
   metadata, not guesses.
3. **Nothing.** A tool with no evidence is ``other`` and stays there.

The keyword substring guesser is gone. It was what put 655 of 764 tools in
``other`` while confidently mis-filing the rest -- ``'sql' in haystack`` matched
any package whose description mentioned SQL at all.
"""

from __future__ import annotations

import email.parser
import logging
import shutil
import subprocess
from collections.abc import Callable, Iterable
from typing import Any

from ..model import InstallMethod, Tool
from ..providers.apt import dpkg_binaries

logger = logging.getLogger("loadout.catalog.seed_apt")

#: Kali meta-package -> (category, extra tags). Membership in one of these is
#: the single strongest categorisation signal available.
META_PACKAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "kali-tools-information-gathering": ("recon", ()),
    "kali-tools-vulnerability": ("vuln-scan", ()),
    "kali-tools-web": ("web", ()),
    "kali-tools-database": ("database", ()),
    "kali-tools-passwords": ("password", ()),
    "kali-tools-wireless": ("wireless", ()),
    "kali-tools-802-11": ("wireless", ("wifi",)),
    "kali-tools-bluetooth": ("wireless", ("bluetooth",)),
    "kali-tools-rfid": ("wireless", ("rfid",)),
    "kali-tools-sdr": ("wireless", ("sdr",)),
    "kali-tools-reverse-engineering": ("reverse", ()),
    "kali-tools-exploitation": ("exploitation", ()),
    "kali-tools-social-engineering": ("social", ()),
    "kali-tools-sniffing-spoofing": ("sniffing", ()),
    "kali-tools-post-exploitation": ("post-exploitation", ()),
    "kali-tools-forensics": ("forensics", ()),
    "kali-tools-reporting": ("reporting", ()),
    "kali-tools-crypto-stego": ("crypto", ()),
    "kali-tools-fuzzing": ("fuzzing", ()),
    "kali-tools-voip": ("network", ("voip",)),
    "kali-tools-hardware": ("hardware", ()),
    "kali-tools-windows-resources": ("post-exploitation", ("windows",)),
    "kali-tools-detect": ("detection", ()),
}

#: Debtag prefix -> category. Second-choice signal, still maintained metadata.
DEBTAG_CATEGORY: dict[str, str] = {
    "security::forensics": "forensics",
    "security::cryptography": "crypto",
    "security::privacy": "crypto",
    "security::authentication": "password",
    "security::ids": "monitoring",
    "security::firewall": "network",
    "security::log-analyzer": "detection",
    "use::scanning": "recon",
    "use::checking": "vuln-scan",
    "use::analysing": "forensics",
    "use::monitor": "monitoring",
    "network::scanner": "recon",
    "network::sniffer": "sniffing",
    "network::server": "network",
    "network::client": "network",
    "protocol::http": "web",
    "protocol::https": "web",
    "devel::debugger": "reverse",
    "devel::rev-engineering": "reverse",
}

ProgressFn = Callable[[int, int], None]


def _run(argv: list[str], *, timeout: int = 60) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("%s failed: %s", argv[0], exc)
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Meta-package membership
# ---------------------------------------------------------------------------


def discover_meta_membership(
    metas: Iterable[str] | None = None,
) -> dict[str, tuple[str, tuple[str, ...]]]:
    """``{package: (category, tags)}`` for every member of a Kali meta-package."""
    membership: dict[str, tuple[str, tuple[str, ...]]] = {}
    if not shutil.which("apt-cache"):
        return membership

    for meta in metas or META_PACKAGES:
        mapping = META_PACKAGES.get(meta)
        if not mapping:
            continue
        category, tags = mapping
        out = _run(
            [
                "apt-cache", "depends",
                "--recurse",
                "--no-recommends", "--no-suggests", "--no-conflicts",
                "--no-breaks", "--no-replaces", "--no-enhances",
                "--important",
                meta,
            ]
        )
        for line in out.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("Depends:", "|Depends:", "PreDepends:", "<")):
                continue
            if " " in stripped or ":" in stripped:
                continue
            if stripped == meta or stripped.startswith(("kali-tools-", "kali-linux-")):
                continue
            # First meta wins, so the mapping order above is authoritative.
            membership.setdefault(stripped, (category, tags))
    logger.info("meta-package membership: %d packages", len(membership))
    return membership


def _debtag_category(tags: list[str]) -> str:
    for tag in tags:
        for prefix, category in DEBTAG_CATEGORY.items():
            if tag.startswith(prefix):
                return category
    return ""


# ---------------------------------------------------------------------------
# Package enumeration
# ---------------------------------------------------------------------------


def _entries_via_python_apt(progress: ProgressFn | None) -> list[dict[str, Any]] | None:
    try:
        import apt  # type: ignore
    except ImportError:
        return None
    try:
        cache = apt.Cache()
    except Exception as exc:  # pragma: no cover - apt init varies by host
        logger.warning("python-apt cache init failed: %s", exc)
        return None

    total = len(cache)
    entries: list[dict[str, Any]] = []
    for index, package in enumerate(cache):
        if progress and index % 500 == 0:
            progress(index, total)
        try:
            candidate = package.candidate
            if candidate is None:
                continue
            record = candidate.record
            raw_tags = (record.get("Tag") if record else "") or ""
            entries.append(
                {
                    "name": package.name,
                    "summary": (candidate.summary or "").strip(),
                    "description": (candidate.description or "").strip(),
                    "homepage": candidate.homepage or "",
                    "section": candidate.section or "",
                    "version": candidate.version or "",
                    "size": int(candidate.installed_size or 0),
                    "tags": [t.strip() for t in raw_tags.replace("\n", "").split(",") if t.strip()],
                }
            )
        except Exception as exc:
            logger.debug("skipping %s: %s", getattr(package, "name", "?"), exc)
            continue
    return entries


def _entries_via_dumpavail(progress: ProgressFn | None) -> list[dict[str, Any]] | None:
    if not shutil.which("apt-cache"):
        return None
    raw = _run(["apt-cache", "dumpavail"], timeout=180)
    if not raw:
        return None

    stanzas = raw.split("\n\n")
    total = len(stanzas)
    parser = email.parser.HeaderParser()
    latest: dict[str, dict[str, Any]] = {}

    for index, stanza in enumerate(stanzas):
        if progress and index % 1000 == 0:
            progress(index, total)
        stanza = stanza.strip()
        if not stanza:
            continue
        try:
            message = parser.parsestr(stanza)
        except Exception as exc:
            logger.debug("unparseable stanza: %s", exc)
            continue
        name = (message.get("Package") or "").strip()
        if not name:
            continue
        description = (message.get("Description-en") or message.get("Description") or "").strip()
        lines = description.splitlines()
        summary = lines[0].strip() if lines else ""
        body = " ".join(line.strip() for line in lines[1:] if line.strip() != ".").strip()
        raw_tags = message.get("Tag") or ""
        size_text = (message.get("Installed-Size") or "0").strip()
        entry = {
            "name": name,
            "summary": summary,
            "description": body,
            "homepage": message.get("Homepage") or "",
            "section": message.get("Section") or "",
            "version": message.get("Version") or "",
            "size": int(size_text) * 1024 if size_text.isdigit() else 0,
            "tags": [t.strip() for t in raw_tags.replace("\n", "").split(",") if t.strip()],
        }
        # dumpavail prints every available version; keep the last seen.
        latest[name] = entry

    return list(latest.values())


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_tools(
    *,
    progress: ProgressFn | None = None,
    only_security: bool = True,
    resolve_binaries: bool = True,
) -> list[Tool]:
    """Produce catalog entries from this machine's APT metadata.

    With *only_security* the result is restricted to packages with real
    evidence of being security tooling -- meta-package membership or a
    security-relevant debtag. Without it you get the entire APT universe, which
    is occasionally what you want and never what you want by default.
    """
    entries = _entries_via_python_apt(progress)
    if entries is None:
        entries = _entries_via_dumpavail(progress)
    if entries is None:
        logger.warning("no APT metadata source available")
        return []

    membership = discover_meta_membership()
    tools: list[Tool] = []

    for entry in entries:
        name = entry["name"]
        category = ""
        tags: list[str] = []

        if name in membership:
            category, meta_tags = membership[name]
            tags.extend(meta_tags)
            tags.append("kali")
        if not category:
            category = _debtag_category(entry.get("tags") or [])

        if only_security and not category:
            continue

        binaries: tuple[str, ...] = ()
        if resolve_binaries:
            binaries = tuple(dpkg_binaries(name))

        tools.append(
            Tool(
                id=name,
                summary=entry.get("summary", "")[:200],
                description=entry.get("description", "")[:1000],
                categories=(category or "other",),
                tags=tuple(tags),
                binaries=binaries,
                homepage=entry.get("homepage", ""),
                size=int(entry.get("size") or 0),
                version=entry.get("version", ""),
                install=(
                    InstallMethod(
                        provider="apt",
                        spec={"package": name},
                        distros=("kali", "debian", "parrot", "ubuntu"),
                    ),
                ),
                metadata={"section": entry.get("section", "")},
            )
        )

    logger.info("built %d tools from APT metadata", len(tools))
    return tools


def enrich(tools: list[Tool], *, resolve_binaries: bool = True) -> list[Tool]:
    """Fill gaps in existing entries from local APT metadata.

    Used to upgrade a catalog seeded elsewhere: anything the entry already
    states wins, APT only supplies what is missing.
    """
    entries = _entries_via_python_apt(None) or _entries_via_dumpavail(None) or []
    by_name = {entry["name"]: entry for entry in entries}
    membership = discover_meta_membership()

    enriched: list[Tool] = []
    for tool in tools:
        package = ""
        for method in tool.install:
            if method.provider == "apt":
                package = str(method.spec.get("package", ""))
                break
        entry = by_name.get(package or tool.id)
        if entry is None:
            enriched.append(tool)
            continue

        changes: dict[str, Any] = {}
        if not tool.summary and entry.get("summary"):
            changes["summary"] = entry["summary"][:200]
        if not tool.description and entry.get("description"):
            changes["description"] = entry["description"][:1000]
        if not tool.homepage and entry.get("homepage"):
            changes["homepage"] = entry["homepage"]
        if not tool.size and entry.get("size"):
            changes["size"] = int(entry["size"])
        if not tool.version and entry.get("version"):
            changes["version"] = entry["version"]

        name = package or tool.id
        if tool.category == "other":
            if name in membership:
                category, meta_tags = membership[name]
                changes["categories"] = (category,)
                if meta_tags:
                    changes["tags"] = tuple(dict.fromkeys([*tool.tags, *meta_tags]))
            else:
                guessed = _debtag_category(entry.get("tags") or [])
                if guessed:
                    changes["categories"] = (guessed,)

        if resolve_binaries and not tool.binaries:
            found = dpkg_binaries(name)
            if found:
                changes["binaries"] = tuple(found)

        enriched.append(tool.with_(**changes) if changes else tool)
    return enriched

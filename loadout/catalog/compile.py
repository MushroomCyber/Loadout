"""Compile the YAML catalog source tree into a queryable SQLite catalog.

The catalog is *data*, kept in ``catalog/`` as one file per tool so it can be
reviewed by pull request, and compiled by CI into an artifact published with
each release. That is what turns a personal scraper into something a community
can maintain -- and it is why the app version and the catalog version are
allowed to move independently.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import CatalogError
from ..model import Tool
from .schema import validate_entry
from .store import build_catalog

logger = logging.getLogger("loadout.catalog.compile")


@dataclass
class CompileReport:
    tools: list[Tool] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files_read: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def iter_entry_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.yaml")):
        if path.name.startswith("_"):
            continue
        yield path
    for path in sorted(root.rglob("*.yml")):
        if path.name.startswith("_"):
            continue
        yield path


def load_source_tree(root: Path, *, strict: bool = True) -> CompileReport:
    """Read and validate every entry under *root*.

    Collects all problems rather than stopping at the first, so a contributor
    sees everything wrong with their branch in a single CI run.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise CatalogError(
            "PyYAML is required to compile the catalog",
        ) from exc

    root = Path(root)
    if not root.is_dir():
        raise CatalogError(f"catalog source directory not found: {root}")

    report = CompileReport()
    seen: dict[str, Path] = {}

    for path in iter_entry_files(root):
        report.files_read += 1
        relative = path.relative_to(root).as_posix()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            report.errors.append(f"{relative}: unreadable YAML ({exc})")
            continue
        if raw is None:
            report.warnings.append(f"{relative}: empty file (skipped)")
            continue

        entries = raw if isinstance(raw, list) else [raw]
        for index, entry in enumerate(entries):
            origin = relative if len(entries) == 1 else f"{relative}[{index}]"
            result = validate_entry(entry, origin=origin)
            report.warnings.extend(result.warnings)
            if not result.ok or result.tool is None:
                report.errors.extend(result.errors)
                continue

            tool = result.tool
            if tool.id in seen:
                report.errors.append(
                    f"{origin}: duplicate id {tool.id!r} (already defined in "
                    f"{seen[tool.id].relative_to(root).as_posix()})"
                )
                continue
            seen[tool.id] = path
            report.tools.append(tool)

    if strict and report.errors:
        logger.error("%d catalog error(s)", len(report.errors))
    return report


def compile_tree(
    source: Path,
    destination: Path,
    *,
    revision: str = "",
    strict: bool = True,
) -> CompileReport:
    """Validate *source* and, if clean, write a catalog to *destination*."""
    report = load_source_tree(source, strict=strict)
    if strict and report.errors:
        return report
    if not report.tools:
        report.errors.append("no valid tool entries found")
        return report

    build_catalog(
        destination,
        report.tools,
        source=f"yaml:{Path(source).name}",
        revision=revision,
    )
    return report


def enrich_source_tree(
    root: Path,
    *,
    only_security: bool = True,
    resolve_binaries: bool = True,
    add_new: bool = False,
) -> dict[str, int]:
    """Fill gaps in the YAML tree from local APT metadata, in place.

    Writes back into ``catalog/`` rather than straight to a compiled database,
    so the enrichment lands as a reviewable diff and the YAML stays the single
    source of truth. Curated values always win -- APT only supplies what an
    entry does not already state -- so this is safe to run on a schedule.

    Returns counts for the CI job to report.
    """
    from .seed_apt import build_tools, enrich

    root = Path(root)
    report = load_source_tree(root, strict=False)
    before = {t.id: t.to_dict() for t in report.tools}

    enriched = enrich(report.tools, resolve_binaries=resolve_binaries)
    by_id = {t.id: t for t in enriched}

    added = 0
    if add_new:
        for tool in build_tools(only_security=only_security, resolve_binaries=resolve_binaries):
            if tool.id not in by_id:
                by_id[tool.id] = tool
                added += 1

    # Remember where each entry already lives so enrichment never relocates a
    # file that a contributor placed deliberately.
    import yaml

    existing_paths: dict[str, Path] = {}
    for path in iter_entry_files(root):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("cannot map %s to an id: %s", path, exc)
            continue
        if isinstance(data, dict) and data.get("id"):
            existing_paths[str(data["id"]).strip().lower()] = path

    changed = 0
    annotated = 0
    for tool_id, tool in sorted(by_id.items()):
        payload = tool.to_dict()
        if before.get(tool_id) == payload:
            continue
        destination = existing_paths.get(tool_id) or (
            root / tool.category / f"{tool_id}.yaml"
        )
        if dump_tool(tool, destination) is None:
            annotated += 1
            continue
        changed += 1

    return {
        "entries": len(by_id),
        "changed": changed,
        "added": added,
        "annotated": annotated,
        "described": sum(1 for t in by_id.values() if t.summary),
        "categorised": sum(1 for t in by_id.values() if t.category != "other"),
    }


#: A `#` comment in an entry is the only place a catalog file can say *why*
#: -- why this signature identity is a pattern, why this key was trusted, why
#: a route is ordered the way it is. `yaml.safe_dump` cannot round-trip
#: comments, so any generated rewrite of such a file deletes that reasoning
#: silently, and the weekly enrichment job rewrites an entry whenever upstream
#: metadata moves (a package's `size:` changes with every new version). The
#: annotated entries are exactly the security-critical ones.
_COMMENT_LINE_RE = re.compile(r"^\s*#", re.M)


def is_annotated(path: Path) -> bool:
    """Does this entry carry comments a regenerated file would destroy?"""
    try:
        return bool(_COMMENT_LINE_RE.search(path.read_text(encoding="utf-8")))
    except OSError:
        return False


def dump_tool(tool: Tool, destination: Path) -> Path | None:
    """Write one tool back out as YAML. Used by the seeding importers.

    Returns ``None`` without writing when the destination carries comments:
    losing a reviewed explanation is worse than missing one round of automated
    enrichment, and the caller reports what it skipped so a person can apply
    the change by hand.

    Writes to a sibling temp file then ``os.replace``s it over the target.
    ``enrich_source_tree`` calls this hundreds of times in a batch; a plain
    ``write_text`` was found to lose a file entirely under real load (712
    writes across a WSL9P-mounted filesystem in ~13s dropped one file
    outright, with the destination directory still present -- an ordinary
    non-atomic write racing a concurrent reader or an interrupted flush is
    consistent with that). Atomic replace makes the failure mode "keep the old
    file" instead of "lose it".
    """
    import os
    import tempfile

    import yaml

    if is_annotated(destination):
        logger.info("%s carries comments; not regenerating it", destination)
        return None

    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = tool.to_dict()
    # Stable, human-friendly field order; contributors read these files.
    order = [
        "id", "summary", "description", "categories", "tags", "phases",
        "binaries", "homepage", "repo", "license", "requires_root", "verify",
        "alternatives", "size", "install", "metadata",
    ]
    ordered = {key: payload[key] for key in order if key in payload}
    ordered.update({k: v for k, v in payload.items() if k not in ordered})
    text = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=100)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass  # not fatal -- some network/9P mounts do not support fsync
        tmp_path.replace(destination)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return destination

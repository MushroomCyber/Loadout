"""Compile the YAML catalog source tree into a queryable SQLite catalog.

The catalog is *data*, kept in ``catalog/`` as one file per tool so it can be
reviewed by pull request, and compiled by CI into an artifact published with
each release. That is what turns a personal scraper into something a community
can maintain -- and it is why the app version and the catalog version are
allowed to move independently.
"""

from __future__ import annotations

import logging
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


def dump_tool(tool: Tool, destination: Path) -> Path:
    """Write one tool back out as YAML. Used by the seeding importers."""
    import yaml

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
    destination.write_text(
        yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return destination

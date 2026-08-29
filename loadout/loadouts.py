"""Loadouts: named tool sets, and the manifest that makes them reproducible.

A loadout is the kit you take on a job. The previous release called these
"profiles" and could only apply them; the useful operation is *converging* a
machine to one, which is what :func:`diff` and ``loadout sync`` do.

Three sources, in ascending precedence:

* **bundled** -- ship with the release (``data/loadouts/*.yaml``)
* **user**    -- ``$XDG_CONFIG_HOME/loadout/loadouts/*.yaml``
* **project** -- ``loadout.yaml`` in the working tree, committed to the
  engagement repo so a teammate gets the same box.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("loadout.loadouts")

PROJECT_MANIFEST = "loadout.yaml"


@dataclass
class Loadout:
    slug: str
    name: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    #: Optional per-tool provider pin, e.g. ``{"nuclei": "go"}``.
    providers: dict[str, str] = field(default_factory=dict)
    source: str = "bundled"
    path: Path | None = None

    def __post_init__(self) -> None:
        self.slug = self.slug.strip().lower()
        self.name = self.name or self.slug
        self.tools = tuple(dict.fromkeys(t.strip().lower() for t in self.tools if t.strip()))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
        }
        if self.tags:
            payload["tags"] = list(self.tags)
        if self.providers:
            payload["providers"] = dict(self.providers)
        payload["source"] = self.source
        return payload

    def write(self, path: Path) -> Path:
        import yaml

        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        payload.pop("source", None)
        path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )
        return path


@dataclass
class LoadoutDiff:
    """What would change if this machine converged to the loadout."""

    missing: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return not self.missing and not self.unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "in_sync": self.in_sync,
            "missing": self.missing,
            "present": self.present,
            "unknown": self.unknown,
            "extra": self.extra,
        }


def bundled_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "loadouts"


def user_dir() -> Path:
    from .paths import user_loadouts_dir

    return user_loadouts_dir()


def _load_file(path: Path, source: str) -> Loadout | None:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("loadout %s unreadable: %s", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("loadout %s: expected a mapping", path)
        return None

    tools = data.get("tools") or []
    if not isinstance(tools, list):
        logger.warning("loadout %s: 'tools' must be a list", path)
        return None

    providers = data.get("providers") or {}
    if not isinstance(providers, dict):
        providers = {}

    return Loadout(
        slug=str(data.get("slug") or path.stem),
        name=str(data.get("name") or "").strip(),
        description=str(data.get("description") or "").strip(),
        tags=tuple(str(t) for t in data.get("tags") or []),
        tools=tuple(str(t) for t in tools),
        providers={str(k): str(v) for k, v in providers.items()},
        source=source,
        path=path,
    )


def load_all(*, project_root: Path | None = None) -> dict[str, Loadout]:
    found: dict[str, Loadout] = {}
    for directory, source in ((bundled_dir(), "bundled"), (user_dir(), "user")):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            loaded = _load_file(path, source)
            if loaded:
                found[loaded.slug] = loaded

    manifest = (project_root or Path.cwd()) / PROJECT_MANIFEST
    if manifest.is_file():
        loaded = _load_file(manifest, "project")
        if loaded:
            found[loaded.slug] = loaded
    return found


def get(slug: str, *, project_root: Path | None = None) -> Loadout | None:
    return load_all(project_root=project_root).get(slug.strip().lower())


def listing(*, project_root: Path | None = None) -> list[Loadout]:
    return sorted(load_all(project_root=project_root).values(), key=lambda p: p.slug)


def project_manifest(root: Path | None = None) -> Loadout | None:
    """The ``loadout.yaml`` in the working tree, if there is one."""
    manifest = (root or Path.cwd()) / PROJECT_MANIFEST
    if not manifest.is_file():
        return None
    return _load_file(manifest, "project")


def diff(
    target: Loadout,
    *,
    catalog,
    installed: set[str],
) -> LoadoutDiff:
    """Compare a loadout against the machine's current state."""
    result = LoadoutDiff()
    known = set(catalog.ids())
    for tool_id in target.tools:
        if tool_id not in known:
            result.unknown.append(tool_id)
        elif tool_id in installed:
            result.present.append(tool_id)
        else:
            result.missing.append(tool_id)
    result.extra = sorted(installed - set(target.tools))
    return result


def from_installed(
    slug: str,
    installed: list[str],
    *,
    name: str = "",
    description: str = "",
    providers: dict[str, str] | None = None,
) -> Loadout:
    """Snapshot the current machine as a loadout -- the input to ``sync``."""
    return Loadout(
        slug=slug,
        name=name or slug.replace("-", " ").title(),
        description=description or "Captured from an existing machine",
        tools=tuple(sorted(installed)),
        providers=providers or {},
        source="user",
    )

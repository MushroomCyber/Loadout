"""Curated Kali tool profiles.

A *profile* is a named collection of tool packages plus metadata
(description, tags). Profiles ship as JSON under
[kalitools/data/profiles/](data/profiles/) and can also be provided
directly by users via ``~/.config/kalitools/profiles/*.json``.

The CLI exposes ``kalitools profile {list,show,apply}``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import logger


@dataclass
class Profile:
    slug: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    source: str = "bundled"

    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "packages": list(self.packages),
            "source": self.source,
        }


def bundled_profiles_dir() -> Path:
    return Path(__file__).parent / "data" / "profiles"


def user_profiles_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "kalitools" / "profiles"


def _load_profile_file(path: Path, source: str) -> Profile | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("profile %s unreadable: %s", path, exc)
        return None
    slug = (data.get("slug") or path.stem).strip().lower()
    if not slug:
        return None
    packages = [str(p).strip() for p in data.get("packages") or [] if str(p).strip()]
    return Profile(
        slug=slug,
        name=str(data.get("name") or slug).strip(),
        description=str(data.get("description") or "").strip(),
        tags=[str(t) for t in data.get("tags") or []],
        packages=packages,
        source=source,
    )


def load_profiles() -> dict[str, Profile]:
    """Load bundled + user profiles, with user profiles overriding by slug."""
    profiles: dict[str, Profile] = {}
    for d, src in ((bundled_profiles_dir(), "bundled"), (user_profiles_dir(), "user")):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            prof = _load_profile_file(p, src)
            if prof:
                profiles[prof.slug] = prof
    return profiles


def get_profile(slug: str) -> Profile | None:
    return load_profiles().get(slug.strip().lower())


def list_profiles() -> list[Profile]:
    return sorted(load_profiles().values(), key=lambda p: p.slug)


def resolve_packages(profile: Profile, *, known_packages: Iterable[str]) -> list[str]:
    """Filter a profile's package list to entries that exist in the catalog."""
    known = set(known_packages)
    return [p for p in profile.packages if p in known]

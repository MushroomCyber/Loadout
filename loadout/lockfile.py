"""``loadout.lock`` -- what a loadout actually resolved to, on a real machine.

A loadout is a list of tool ids with no versions, so applying one in March and
again in September builds two different boxes. That is fine for "give me a web
kit" and useless for the claim this project exists to support: that a finding
can be re-examined months later against the tooling that produced it.

The lockfile closes that gap by recording what the ids *became* -- provider,
version, and how the download was checked -- so a later run can say whether
this machine still matches, and name every place it does not.

Deliberately a record and a comparison, not an enforcement mechanism. Pinning
a version at install time needs each provider to express one, and only ``go``
does today; a lockfile that silently failed to hold half its pins would be
worse than one that reports drift honestly. :func:`compare` is the honest
half, and it is the half a disputed finding actually needs.

The format is JSON rather than YAML: this file is written by a machine, read
by a machine, and diffed by humans in a code review. Sorted keys and a
trailing newline keep that diff to the lines that really changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Beside ``loadout.yaml``, committed with it.
LOCK_NAME = "loadout.lock"

#: Bumped only for a change that an older reader would get wrong.
LOCK_VERSION = 1

#: A tool in the lock that is not installed here at all.
DRIFT_MISSING = "missing"

#: Installed, but not the version the lock recorded.
DRIFT_VERSION = "version"

#: Installed via a different provider than the lock recorded. Worth reporting
#: separately: the same tool from apt and from a release archive can differ in
#: patch level, build flags and bundled data.
DRIFT_PROVIDER = "provider"

#: Installed here and absent from the lock -- not a failure to reproduce, but
#: it is on the box and did not come from the manifest.
DRIFT_UNLOCKED = "unlocked"

#: Version was never recorded, so nothing can be compared. Distinct from a
#: match: "we do not know" must never render as "it agrees".
DRIFT_UNKNOWN = "unknown"


@dataclass(frozen=True)
class LockEntry:
    tool_id: str
    provider: str = ""
    version: str = ""
    verify_method: str = ""
    verify_ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"provider": self.provider, "version": self.version}
        if self.verify_method:
            payload["verified"] = self.verify_method if self.verify_ok else ""
        return payload

    @classmethod
    def from_dict(cls, tool_id: str, data: dict[str, Any]) -> LockEntry:
        verified = str(data.get("verified") or "")
        return cls(
            tool_id=tool_id,
            provider=str(data.get("provider") or ""),
            version=str(data.get("version") or ""),
            verify_method=verified,
            verify_ok=bool(verified),
        )


@dataclass(frozen=True)
class Drift:
    tool_id: str
    kind: str
    expected: str = ""
    actual: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool_id,
            "drift": self.kind,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass
class Lock:
    slug: str = ""
    generated_at: str = ""
    entries: dict[str, LockEntry] = field(default_factory=dict)
    version: int = LOCK_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "lock_version": self.version,
            "slug": self.slug,
            "generated_at": self.generated_at,
            "tools": {
                tool_id: self.entries[tool_id].to_dict()
                for tool_id in sorted(self.entries)
            },
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        return path

    @classmethod
    def from_dict(cls, data: Any) -> Lock:
        if not isinstance(data, dict):
            raise ValueError("lockfile must be a JSON object")
        tools = data.get("tools")
        if tools is not None and not isinstance(tools, dict):
            raise ValueError("lockfile 'tools' must be an object")
        entries = {
            str(tool_id): LockEntry.from_dict(str(tool_id), value)
            for tool_id, value in (tools or {}).items()
            if isinstance(value, dict)
        }
        return cls(
            slug=str(data.get("slug") or ""),
            generated_at=str(data.get("generated_at") or ""),
            entries=entries,
            version=int(data.get("lock_version") or LOCK_VERSION),
        )

    @classmethod
    def read(cls, path: Path) -> Lock:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def lock_path(root: Path | None = None) -> Path:
    from pathlib import Path as _Path

    return (root or _Path.cwd()) / LOCK_NAME


def capture(slug: str, tool_ids: list[str], state: dict[str, dict[str, Any]]) -> Lock:
    """Build a lock from what this machine currently reports.

    Only installed tools are recorded. Locking a tool that is not here would
    write a pin nothing verified, which is exactly the sort of unearned claim
    the rest of this project refuses to make.
    """
    from datetime import datetime, timezone

    entries: dict[str, LockEntry] = {}
    for tool_id in sorted(dict.fromkeys(t.strip().lower() for t in tool_ids if t.strip())):
        row = state.get(tool_id)
        if not row or not row.get("installed"):
            continue
        entries[tool_id] = LockEntry(
            tool_id=tool_id,
            provider=str(row.get("provider") or ""),
            version=str(row.get("version") or ""),
            verify_method=str(row.get("verify_method") or ""),
            verify_ok=bool(row.get("verify_ok")),
        )
    return Lock(
        slug=slug,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        entries=entries,
    )


def compare(
    lock: Lock,
    state: dict[str, dict[str, Any]],
    *,
    installed: set[str] | None = None,
) -> list[Drift]:
    """Every way this machine differs from the lock, in tool-id order.

    An empty list is the only thing that means "reproduced". A tool whose
    version was never recorded reports :data:`DRIFT_UNKNOWN` rather than
    passing quietly -- the whole point is to distinguish a match from an
    absence of evidence.
    """
    here = installed if installed is not None else {
        tool_id for tool_id, row in state.items() if row.get("installed")
    }
    drifts: list[Drift] = []

    for tool_id in sorted(lock.entries):
        entry = lock.entries[tool_id]
        row = state.get(tool_id) or {}
        if tool_id not in here:
            drifts.append(Drift(tool_id, DRIFT_MISSING, expected=entry.version))
            continue

        provider = str(row.get("provider") or "")
        if entry.provider and provider and provider != entry.provider:
            drifts.append(
                Drift(tool_id, DRIFT_PROVIDER, expected=entry.provider, actual=provider)
            )
            continue

        version = str(row.get("version") or "")
        if not entry.version or not version:
            drifts.append(
                Drift(tool_id, DRIFT_UNKNOWN, expected=entry.version, actual=version)
            )
        elif version != entry.version:
            drifts.append(
                Drift(tool_id, DRIFT_VERSION, expected=entry.version, actual=version)
            )

    for tool_id in sorted(here - set(lock.entries)):
        drifts.append(Drift(tool_id, DRIFT_UNLOCKED, actual=str(
            (state.get(tool_id) or {}).get("version") or ""
        )))
    return drifts

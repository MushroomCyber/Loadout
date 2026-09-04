"""Core data model.

A :class:`Tool` is provider-agnostic: it names a piece of software and lists
*every* way it can be installed. Which of those routes is viable on the
current machine is decided later by the provider layer, never here.

Note the deliberate absence of dict-style ``__getitem__`` / ``get`` shims. The
previous model pretended to be a mapping, so ``tool.get("tags")`` on a field
that did not exist returned ``None`` instead of failing -- which is exactly how
the ``search tag:`` filter shipped broken. Attributes only, checked by mypy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

#: Debian policy package names, also used to gate anything reaching an argv.
PACKAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+.\-]*$")

#: Catalog identifiers: what a user types. Stable across distros and providers.
#: Must accept '+' -- 953 package names on Kali contain it (afl++, bonnie++,
#: g++, libstdc++6). Rejecting it aborted the whole APT catalog build.
TOOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._+\-]*$")

#: An entry that installs an executable. The default, and what every field
#: named after running something assumes.
KIND_TOOL = "tool"

#: An entry that installs data: wordlists, payloads, templates, rulesets.
#: Roughly half a working toolkit is not executable, and treating it as a
#: degenerate tool made every binary-shaped question about it answer wrongly --
#: `loadout verify` reported it missing, because it looked for a command that
#: was never supposed to exist.
KIND_CONTENT = "content"

KINDS = (KIND_TOOL, KIND_CONTENT)


class ModelError(ValueError):
    """Raised when catalog data violates the schema."""


@dataclass(frozen=True, slots=True)
class InstallMethod:
    """One route to getting a tool onto a machine.

    ``provider`` names a backend (``apt``, ``brew``, ``go``, ...); ``spec``
    carries the provider-specific payload (package name, module path, repo
    slug, image reference). Providers validate their own spec keys.
    """

    provider: str
    spec: dict[str, Any] = field(default_factory=dict)
    #: Restrict this route to particular distro/OS ids, empty means "anywhere".
    distros: tuple[str, ...] = ()
    #: Lower sorts first when several routes are viable.
    priority: int = 50

    def __post_init__(self) -> None:
        if not self.provider:
            raise ModelError("install method requires a provider")

    def applies_to(self, distro_id: str) -> bool:
        if not self.distros:
            return True
        return distro_id.lower() in {d.lower() for d in self.distros}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"provider": self.provider, **self.spec}
        if self.distros:
            out["distros"] = list(self.distros)
        if self.priority != 50:
            out["priority"] = self.priority
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstallMethod:
        payload = dict(data)
        provider = str(payload.pop("provider", "")).strip().lower()
        distros = tuple(str(d).strip().lower() for d in payload.pop("distros", []) or [])
        priority = int(payload.pop("priority", 50))
        return cls(provider=provider, spec=payload, distros=distros, priority=priority)


@dataclass(slots=True)
class Tool:
    """A catalog entry."""

    id: str
    summary: str = ""
    description: str = ""
    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    #: Kill-chain / PTES stages this tool belongs to.
    phases: tuple[str, ...] = ()
    #: Executables the tool installs. First entry is the primary command --
    #: this is what `loadout run` and `--help` use, and it is NEVER synthesised
    #: from the package name.
    binaries: tuple[str, ...] = ()
    homepage: str = ""
    repo: str = ""
    license: str = ""
    install: tuple[InstallMethod, ...] = ()
    alternatives: tuple[str, ...] = ()
    requires_root: bool = False
    #: Command proving the install actually works, e.g. ``ffuf -V``.
    verify: str = ""
    #: ``tool`` or ``content`` -- see :data:`KIND_CONTENT`.
    kind: str = KIND_TOOL
    #: Where a ``content`` entry's data lands, e.g. ``/usr/share/seclists``.
    #: This is to content what ``binaries`` is to a tool: the thing whose
    #: presence proves the install did something.
    paths: tuple[str, ...] = ()
    size: int = 0
    version: str = ""
    deprecated_by: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip().lower()
        if not self.id:
            raise ModelError("tool requires an id")
        if not TOOL_ID_RE.match(self.id):
            raise ModelError(f"invalid tool id: {self.id!r}")
        self.categories = _norm_tuple(self.categories)
        self.tags = _norm_tuple(self.tags)
        self.phases = _norm_tuple(self.phases)
        self.binaries = _norm_tuple(self.binaries, lower=False)
        self.alternatives = _norm_tuple(self.alternatives)
        self.paths = _norm_tuple(self.paths, lower=False)
        self.kind = (self.kind or KIND_TOOL).strip().lower()
        if self.kind not in KINDS:
            raise ModelError(f"{self.id}: unknown kind {self.kind!r}, expected one of {KINDS}")
        self.summary = (self.summary or "").strip()
        self.description = (self.description or "").strip()
        self.size = max(0, int(self.size or 0))

    # -- derived -----------------------------------------------------------

    @property
    def category(self) -> str:
        """Primary category, for single-column displays."""
        return self.categories[0] if self.categories else "other"

    @property
    def is_content(self) -> bool:
        """True for a wordlist, payload set or template pack.

        Callers that are about to run, resolve or PATH-check something should
        ask this first: for a content entry the honest answer to "where is its
        command" is that it does not have one and never will.
        """
        return self.kind == KIND_CONTENT

    @property
    def primary_binary(self) -> str:
        """The command a user actually runs. Empty when the catalog doesn't know.

        Returning ``""`` rather than guessing the package name is the point:
        callers must handle "we don't know the binary" instead of shelling out
        to something that does not exist.
        """
        return self.binaries[0] if self.binaries else ""

    @property
    def providers(self) -> tuple[str, ...]:
        seen: list[str] = []
        for method in self.install:
            if method.provider not in seen:
                seen.append(method.provider)
        return tuple(seen)

    def methods_for(self, provider: str) -> tuple[InstallMethod, ...]:
        return tuple(m for m in self.install if m.provider == provider)

    def search_blob(self) -> str:
        """Text handed to the FTS index."""
        return " ".join(
            [
                self.id,
                self.summary,
                self.description,
                " ".join(self.categories),
                " ".join(self.tags),
                " ".join(self.phases),
                " ".join(self.binaries),
                " ".join(self.paths),
                " ".join(self.alternatives),
            ]
        ).strip()

    def title_blob(self) -> str:
        """The identity-bearing text: id and summary, hyphens spaced out too.

        A search FTS-ranks purely on term frequency across whatever it is
        given, so typing the exact name of a well-known tool has to compete
        with every longer description that happens to mention it in passing.
        Indexed as its own column and weighted above the full blob, this is
        what makes typing 'sqlmap' put sqlmap itself above another entry
        whose description merely discusses sqlmap.
        """
        return f"{self.id} {self.id.replace('-', ' ')} {self.summary}".strip()

    def with_(self, **changes: Any) -> Tool:
        return replace(self, **changes)

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id}
        if self.summary:
            out["summary"] = self.summary
        if self.description:
            out["description"] = self.description
        if self.kind != KIND_TOOL:
            out["kind"] = self.kind
        for name in ("categories", "tags", "phases", "binaries", "paths", "alternatives"):
            value = getattr(self, name)
            if value:
                out[name] = list(value)
        for name in ("homepage", "repo", "license", "verify", "version", "deprecated_by"):
            value = getattr(self, name)
            if value:
                out[name] = value
        if self.requires_root:
            out["requires_root"] = True
        if self.size:
            out["size"] = self.size
        if self.install:
            out["install"] = [m.to_dict() for m in self.install]
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tool:
        if not isinstance(data, dict):
            raise ModelError(f"tool entry must be a mapping, got {type(data).__name__}")
        install_raw = data.get("install") or []
        if not isinstance(install_raw, list):
            raise ModelError(f"{data.get('id')!r}: 'install' must be a list")
        return cls(
            id=data.get("id") or data.get("name") or "",
            summary=data.get("summary", "") or "",
            description=data.get("description", "") or "",
            categories=tuple(data.get("categories") or []),
            tags=tuple(data.get("tags") or []),
            phases=tuple(data.get("phases") or []),
            binaries=tuple(data.get("binaries") or []),
            kind=str(data.get("kind") or KIND_TOOL),
            paths=tuple(data.get("paths") or []),
            homepage=data.get("homepage", "") or "",
            repo=data.get("repo", "") or "",
            license=data.get("license", "") or "",
            install=tuple(InstallMethod.from_dict(m) for m in install_raw),
            alternatives=tuple(data.get("alternatives") or []),
            requires_root=bool(data.get("requires_root", False)),
            verify=data.get("verify", "") or "",
            size=int(data.get("size") or 0),
            version=data.get("version", "") or "",
            deprecated_by=data.get("deprecated_by", "") or "",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class ToolStatus:
    """Live, per-machine state for a tool. Never stored in the catalog."""

    tool_id: str
    installed: bool = False
    installed_version: str = ""
    installed_via: str = ""
    starred: bool = False
    held: bool = False
    last_used: str = ""


def _norm_tuple(values: Any, *, lower: bool = True) -> tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if lower:
            text = text.lower()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)

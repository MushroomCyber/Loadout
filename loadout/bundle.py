"""Offline bundles: build a kit on a connected box, install it on one that is not.

Incident response happens on isolated segments. Client sites block egress.
Classified work has no route out at all. The usual answer is a USB stick of
``.deb`` files assembled by hand, with no record of what is on it and no way to
tell whether it arrived intact.

A bundle is a tar archive holding two things: a ``manifest.json`` describing
what it contains, and a ``payload/`` tree of the actual artifacts. Building one
needs network; installing from one needs none.

Trust runs the other way round from the rest of this program. Everywhere else
Loadout is the thing fetching from the network and deciding what to trust. Here
the bundle arrives from outside -- across a network, on removable media, from
whoever handed it over -- and is untrusted input on a machine chosen precisely
because it is isolated. So:

* every payload file is checksummed into the manifest at build time and
  re-checksummed before use, because a bundle that skipped this would be a
  clean way around the checksum and signature verification everywhere else;
* member paths are validated before extraction -- no absolute paths, no ``..``,
  no symlinks or device nodes -- since a tar can otherwise write anywhere the
  extracting user can;
* the build platform is recorded, because a bundle of amd64 debs is not a kit
  on an arm64 machine and finding that out mid-engagement is too late.

The format is versioned. :data:`FORMAT_VERSION` changes when a bundle written
by a newer Loadout would be misread by an older one.
"""

from __future__ import annotations

import json
import logging
import platform
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import BundleError
from .policy import file_digest

logger = logging.getLogger("loadout.bundle")

FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
PAYLOAD_DIR = "payload"

#: Control files a package manager leaves behind in a download directory.
#: apt creates `lock` and `partial/` in whatever archives directory it is given;
#: neither is an artifact, and shipping them puts a zero-byte lock file on the
#: isolated machine and inflates the manifest with entries nobody can install.
PAYLOAD_EXCLUDE = frozenset({"lock", "partial"})

#: Providers that can put something in a bundle. The rest need a build
#: toolchain or a package index on the target, which an isolated machine is
#: unlikely to have -- they are reported as skipped rather than silently
#: dropped, so a bundle never quietly contains less than it claims.
#: npm is deliberately absent: it needs `node`, and the only npm reachable on
#: the development box is the Windows one via WSL interop, so the offline path
#: could not be tested end to end. An untested bundle path is worse than an
#: honest refusal -- it fails on the isolated machine instead of at build time.
BUNDLEABLE = ("apt", "github", "pipx", "gem")


@dataclass
class BundledFile:
    path: str
    sha256: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundledFile:
        return cls(
            path=str(data.get("path", "")),
            sha256=str(data.get("sha256", "")),
            size=int(data.get("size") or 0),
        )


@dataclass
class BundledTool:
    tool_id: str
    provider: str
    files: list[BundledFile] = field(default_factory=list)
    spec: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool_id,
            "provider": self.provider,
            "files": [f.to_dict() for f in self.files],
            "spec": self.spec,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundledTool:
        return cls(
            tool_id=str(data.get("tool", "")),
            provider=str(data.get("provider", "")),
            files=[BundledFile.from_dict(f) for f in data.get("files") or []],
            spec=dict(data.get("spec") or {}),
        )


@dataclass
class SkippedTool:
    tool_id: str
    provider: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool_id, "provider": self.provider, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkippedTool:
        return cls(
            tool_id=str(data.get("tool", "")),
            provider=str(data.get("provider", "")),
            reason=str(data.get("reason", "")),
        )


@dataclass
class Manifest:
    """What a bundle claims to contain, and where it was built."""

    format: int = FORMAT_VERSION
    created_at: str = ""
    distro: str = ""
    arch: str = ""
    loadout_version: str = ""
    tools: list[BundledTool] = field(default_factory=list)
    skipped: list[SkippedTool] = field(default_factory=list)

    @property
    def files(self) -> list[BundledFile]:
        return [f for tool in self.tools for f in tool.files]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "created_at": self.created_at,
            "distro": self.distro,
            "arch": self.arch,
            "loadout_version": self.loadout_version,
            "tools": [t.to_dict() for t in self.tools],
            "skipped": [s.to_dict() for s in self.skipped],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Manifest:
        if not isinstance(data, dict):
            raise BundleError("manifest.json is not an object")
        try:
            format_version = int(data.get("format") or 0)
        except (TypeError, ValueError) as exc:
            raise BundleError(f"manifest has a non-numeric format: {exc}") from exc
        if format_version > FORMAT_VERSION:
            raise BundleError(
                f"bundle format {format_version} is newer than this Loadout "
                f"understands (supports up to {FORMAT_VERSION}). Upgrade Loadout "
                "on this machine, or rebuild the bundle with an older one."
            )
        return cls(
            format=format_version,
            created_at=str(data.get("created_at", "")),
            distro=str(data.get("distro", "")),
            arch=str(data.get("arch", "")),
            loadout_version=str(data.get("loadout_version", "")),
            tools=[BundledTool.from_dict(t) for t in data.get("tools") or []],
            skipped=[SkippedTool.from_dict(s) for s in data.get("skipped") or []],
        )


def current_platform() -> tuple[str, str]:
    """The (distro, arch) pair a bundle is built for."""
    from .providers import detect_distro

    return detect_distro(), _dpkg_arch()


def _dpkg_arch() -> str:
    """Debian's name for this architecture, since the payload is usually debs.

    ``platform.machine()`` says ``x86_64`` where dpkg says ``amd64``; comparing
    the wrong pair would either reject good bundles or accept useless ones.
    """
    from .providers.base import Provider

    reported = Provider._run_text(["dpkg", "--print-architecture"]).strip()
    if reported:
        return reported
    machine = platform.machine().lower()
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_manifest(archive: Path) -> Manifest:
    """Pull the manifest out of a bundle without extracting the payload."""
    if not archive.is_file():
        raise BundleError(f"{archive}: no such bundle")
    try:
        with tarfile.open(archive, "r:*") as tar:
            member = _find_manifest(tar)
            handle = tar.extractfile(member)
            if handle is None:
                raise BundleError(f"{archive}: manifest.json is not a regular file")
            raw = handle.read().decode("utf-8")
    except tarfile.TarError as exc:
        raise BundleError(f"{archive}: not a readable tar archive ({exc})") from exc
    try:
        return Manifest.from_dict(json.loads(raw))
    except ValueError as exc:
        raise BundleError(f"{archive}: manifest.json is not valid JSON ({exc})") from exc


def _find_manifest(tar: tarfile.TarFile) -> tarfile.TarInfo:
    for member in tar.getmembers():
        if PurePosixPath(member.name).name == MANIFEST_NAME and member.isfile():
            return member
    raise BundleError("no manifest.json in this archive -- is it a Loadout bundle?")


def safe_member(name: str) -> bool:
    """Reject anything that would write outside the extraction directory.

    A tar can name ``../../etc/cron.d/x`` or ``/etc/shadow`` and most extractors
    will happily oblige. The bundle arrives from outside, so this runs on every
    member before anything touches the filesystem.
    """
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute():
        return False
    if any(part == ".." for part in path.parts):
        return False
    # A Windows drive letter survives PurePosixPath as a plain segment.
    return ":" not in path.parts[0]


def extract(archive: Path, into: Path) -> Manifest:
    """Extract a bundle, verify every payload file, and return its manifest.

    Verification happens *after* extraction but *before* the caller is handed
    the manifest, so there is no window in which an unverified file is offered
    as installable.
    """
    manifest = read_manifest(archive)
    into.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(archive, "r:*") as tar:
            members = []
            for member in tar.getmembers():
                if not safe_member(member.name):
                    raise BundleError(
                        f"{archive.name} contains an unsafe path: {member.name!r}. "
                        "Refusing to extract."
                    )
                if member.issym() or member.islnk():
                    raise BundleError(
                        f"{archive.name} contains a link ({member.name!r}). "
                        "Refusing to extract."
                    )
                if not (member.isfile() or member.isdir()):
                    raise BundleError(
                        f"{archive.name} contains a special file ({member.name!r}). "
                        "Refusing to extract."
                    )
                members.append(member)
            _extract_all(tar, into, members)
    except tarfile.TarError as exc:
        raise BundleError(f"{archive}: could not extract ({exc})") from exc

    verify_payload(manifest, into)
    return manifest


def _extract_all(tar: tarfile.TarFile, into: Path, members: list[tarfile.TarInfo]) -> None:
    # Python 3.12 added a filter argument and warns without one; older versions
    # do not accept it. Every member is already validated above either way.
    try:
        tar.extractall(into, members=members, filter="data")  # type: ignore[call-arg]
    except TypeError:  # pragma: no cover - Python < 3.12
        tar.extractall(into, members=members)  # noqa: S202 - members validated


def verify_payload(manifest: Manifest, root: Path) -> None:
    """Re-checksum every file the manifest claims, against the manifest.

    Without this a bundle would be a way around every other verification in
    the program: the artifacts inside were checksummed and signature-checked
    when they were fetched, and that means nothing if the tar can be edited
    afterwards.
    """
    problems: list[str] = []
    for entry in manifest.files:
        if not safe_member(entry.path):
            problems.append(f"{entry.path}: unsafe path in manifest")
            continue
        target = root / entry.path
        if not target.is_file():
            problems.append(f"{entry.path}: listed in the manifest but not in the bundle")
            continue
        if not entry.sha256:
            problems.append(f"{entry.path}: no checksum recorded")
            continue
        actual = file_digest(target, "sha256")
        if actual.lower() != entry.sha256.lower():
            problems.append(
                f"{entry.path}: sha256 mismatch "
                f"(expected {entry.sha256[:16]}..., got {actual[:16]}...)"
            )
    if problems:
        raise BundleError(
            "bundle failed verification:\n  " + "\n  ".join(problems[:10])
        )


def platform_warnings(manifest: Manifest) -> list[str]:
    """Mismatches between where a bundle was built and where it is being used.

    Returned rather than raised: a distro mismatch is often fine (Kali debs on
    Debian), an architecture mismatch essentially never is, and the caller is
    better placed than this module to decide how loudly to say so.
    """
    distro, arch = current_platform()
    warnings: list[str] = []
    if manifest.arch and arch and manifest.arch != arch:
        warnings.append(
            f"built for {manifest.arch}, this machine is {arch} -- "
            "the packages inside will not install"
        )
    if manifest.distro and distro and manifest.distro != distro:
        warnings.append(
            f"built on {manifest.distro}, this machine is {distro} -- "
            "usually fine between Debian derivatives, check if it is not one"
        )
    return warnings


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def build_manifest(
    tools: list[BundledTool], skipped: list[SkippedTool], root: Path
) -> Manifest:
    """Checksum everything that was fetched and describe it."""
    from . import __version__

    distro, arch = current_platform()
    for tool in tools:
        for entry in tool.files:
            target = root / entry.path
            entry.sha256 = file_digest(target, "sha256")
            entry.size = target.stat().st_size
    return Manifest(
        format=FORMAT_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        distro=distro,
        arch=arch,
        loadout_version=__version__,
        tools=tools,
        skipped=skipped,
    )


def write(archive: Path, manifest: Manifest, root: Path) -> None:
    """Write the bundle: manifest first, then the payload tree.

    Manifest first so a reader can learn what it is holding without streaming
    the whole archive.
    """
    archive.parent.mkdir(parents=True, exist_ok=True)
    compress = archive.name.endswith((".gz", ".tgz"))
    manifest_path = root / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    started = time.monotonic()
    # Two branches rather than a computed mode string: tarfile.open is
    # overloaded on the mode, and a variable defeats the type checker.
    if compress:
        with tarfile.open(archive, "w:gz") as tar:
            _add_payload(tar, manifest_path, root)
    else:
        with tarfile.open(archive, "w") as tar:
            _add_payload(tar, manifest_path, root)
    logger.debug("wrote %s in %.1fs", archive, time.monotonic() - started)


def _add_payload(tar: tarfile.TarFile, manifest_path: Path, root: Path) -> None:
    tar.add(manifest_path, arcname=MANIFEST_NAME)
    payload = root / PAYLOAD_DIR
    if payload.is_dir():
        for path in sorted(payload.rglob("*")):
            # Same exclusions as collect(): anything in the tar that is not in
            # the manifest would be an unchecksummed file on the far side.
            if path.is_file() and not PAYLOAD_EXCLUDE.intersection(
                path.relative_to(payload).parts
            ):
                tar.add(path, arcname=str(path.relative_to(root).as_posix()))


def relative_payload_path(provider: str, tool_id: str, filename: str) -> str:
    """Where one artifact lives inside a bundle."""
    return f"{PAYLOAD_DIR}/{provider}/{tool_id}/{filename}"


# ---------------------------------------------------------------------------
# Choosing what to bundle
# ---------------------------------------------------------------------------


def choose_bundleable_method(planner: Any, tool: Any) -> tuple[str, Any]:
    """Pick an install route that can actually travel.

    The normal resolver picks by catalog priority, which is right for a live
    install and wrong here: a tool with both a `go` and an `apt` route would
    resolve to go, and go cannot be bundled. Prefer a bundleable provider when
    one is viable, and fall back to the normal choice so the caller still gets
    a real provider name to explain the skip with.
    """
    viable = planner.viable_methods(tool)
    if not viable:
        from .errors import NoViableProvider

        raise NoViableProvider(tool.id, tried=[m.provider for m in tool.install])
    for name, method in viable:
        if name in BUNDLEABLE:
            return name, method
    return viable[0]


def plan_fetch(ctx: Any, tool_ids: list[str], root: Path) -> tuple[Any, list[SkippedTool]]:
    """Build the plan that downloads everything into *root*/payload.

    Returns the plan plus the tools that cannot travel, each with the reason --
    a bundle that quietly held less than it claimed would be discovered on the
    isolated machine, which is the worst place to discover it.
    """
    from .errors import LoadoutError
    from .planner import ACTION_FETCH, Plan, PlannedAction
    from .providers import get_provider

    planner = ctx.planner()
    plan = Plan()
    skipped: list[SkippedTool] = []
    seen: set[str] = set()

    for raw_id in tool_ids:
        tool_id = raw_id.strip().lower()
        if not tool_id or tool_id in seen:
            continue
        seen.add(tool_id)

        tool = ctx.catalog.get(tool_id)
        if tool is None:
            skipped.append(SkippedTool(tool_id, "", "not in the catalog"))
            continue

        try:
            name, method = choose_bundleable_method(planner, tool)
        except LoadoutError as exc:
            skipped.append(SkippedTool(tool_id, "", exc.message))
            continue

        if name not in BUNDLEABLE:
            skipped.append(
                SkippedTool(
                    tool_id,
                    name,
                    f"{name} needs a toolchain or package index on the target",
                )
            )
            continue

        dest = root / PAYLOAD_DIR / name / tool_id
        try:
            steps = get_provider(name).plan_fetch(tool, method, dest)
        except LoadoutError as exc:
            skipped.append(SkippedTool(tool_id, name, exc.message))
            continue

        plan.actions.append(
            PlannedAction(
                tool=tool,
                action=ACTION_FETCH,
                provider=name,
                method=method,
                steps=steps,
            )
        )
    return plan, skipped


def collect(root: Path, plan: Any) -> list[BundledTool]:
    """Record what actually landed on disk after the fetch plan ran.

    Reads the filesystem rather than trusting the plan: apt pulls in a whole
    dependency closure whose filenames nobody knew in advance, and a provider
    that fetched nothing must not appear in the manifest as though it had.
    """
    bundled: list[BundledTool] = []
    for action in plan.actions:
        dest = root / PAYLOAD_DIR / action.provider / action.tool.id
        if not dest.is_dir():
            continue
        files = [
            BundledFile(
                path=str(path.relative_to(root).as_posix()), sha256="", size=0
            )
            for path in sorted(dest.rglob("*"))
            if path.is_file() and not _is_control_file(path, dest)
        ]
        if not files:
            continue
        bundled.append(
            BundledTool(
                tool_id=action.tool.id,
                provider=action.provider,
                files=files,
                spec=dict(action.method.spec),
            )
        )
    return bundled


def _is_control_file(path: Path, dest: Path) -> bool:
    """Package-manager bookkeeping that must not travel in a bundle."""
    relative = path.relative_to(dest)
    return bool(PAYLOAD_EXCLUDE.intersection(relative.parts))


def plan_install(ctx: Any, manifest: Manifest, root: Path, tool_ids: list[str]) -> Any:
    """Build the plan that installs from an already-extracted bundle.

    This is the half that runs on the isolated machine, so it consults the
    bundle for what to install and never the network for anything.
    """
    from .errors import LoadoutError
    from .model import InstallMethod
    from .planner import ACTION_INSTALL, Plan, PlannedAction
    from .providers import get_provider

    wanted = {t.strip().lower() for t in tool_ids if t.strip()}
    plan = Plan()
    for entry in manifest.tools:
        if wanted and entry.tool_id not in wanted:
            continue
        tool = ctx.catalog.get(entry.tool_id)
        if tool is None:
            plan.skipped.append(
                _planner_skip(entry.tool_id, "in the bundle but not in this catalog")
            )
            continue
        files = [root / f.path for f in entry.files]
        method = InstallMethod(provider=entry.provider, spec=dict(entry.spec))
        try:
            steps = get_provider(entry.provider).plan_install_local(tool, method, files)
        except LoadoutError as exc:
            plan.skipped.append(_planner_skip(entry.tool_id, exc.message))
            continue
        plan.actions.append(
            PlannedAction(
                tool=tool,
                action=ACTION_INSTALL,
                provider=entry.provider,
                method=method,
                steps=steps,
            )
        )

    missing = wanted - {e.tool_id for e in manifest.tools}
    for tool_id in sorted(missing):
        plan.skipped.append(_planner_skip(tool_id, "not in this bundle"))
    return plan


def _planner_skip(tool_id: str, reason: str) -> Any:
    from .planner import SkippedTool as PlannerSkipped

    return PlannerSkipped(tool_id, reason)

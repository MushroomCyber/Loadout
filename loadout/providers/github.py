"""GitHub release provider -- fetch, verify, extract, install.

Many of the best tools ship only as a release archive. Fetching and executing
those is the highest-risk thing this program does, so verification is not
optional: the artifact is checksummed against the release's own checksum file,
and an entry with no publishable checksum fails unless the user explicitly
passes ``--allow-unverified``.

A checksum alone only proves the download was not corrupted. The checksum file
is served by the same account as the artifact, so anyone able to replace one
can replace both. Where a project also publishes a signature, the catalog pins
its key and :mod:`loadout.signature` checks it -- and unlike a missing
checksum, a *declared* signature that fails cannot be waived with
``--allow-unverified``.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ..errors import ProviderError, VerificationError
from ..model import InstallMethod, Tool
from ..policy import parse_checksum_file, verify_digest
from ..signature import SIGNS_CHECKSUMS, SignatureSpec, parse_spec, verify_signature
from .base import CommandStep, Provider, ProviderStatus, PythonStep, Step

if TYPE_CHECKING:  # pragma: no cover
    from ..executor import ExecContext

logger = logging.getLogger("loadout.providers.github")

API_ROOT = "https://api.github.com"


def _describe_signature(spec: SignatureSpec | None) -> str:
    """What --dry-run shows about verification, in one line."""
    if spec is None:
        return "<none published>"
    where = "the checksum file" if spec.signs == SIGNS_CHECKSUMS else "the artifact"
    pin = spec.key_fingerprint or "pinned key"
    return f"{spec.type} over {where}, {pin}"


def user_bin_dir() -> Path:
    return Path(os.environ.get("LOADOUT_BIN_DIR") or Path.home() / ".local" / "bin")


@dataclass
class ReleaseAsset:
    name: str
    url: str
    size: int


class GithubReleaseProvider(Provider):
    name = "github"
    label = "GitHub releases (verified download)"
    required_spec_keys = ("repo",)
    executables = ()
    needs_root = False
    default_priority = 60

    def detect(self) -> ProviderStatus:
        try:
            import requests  # noqa: F401
        except ImportError:
            return ProviderStatus(
                name=self.name,
                available=False,
                detail="requests not installed",
            )
        return ProviderStatus(name=self.name, available=True, detail="network required")

    # -- planning ----------------------------------------------------------

    def plan_install(self, tool: Tool, method: InstallMethod) -> list[Step]:
        repo = str(self.spec_value(method, "repo")).strip()
        if repo.count("/") != 1:
            raise ProviderError(f"github repo must be 'owner/name', got {repo!r}")
        pattern = str(method.spec.get("asset") or "")
        checksums = str(method.spec.get("checksums") or "")
        # Parsed here so a malformed signature block fails the plan rather
        # than surfacing halfway through a download.
        signature = parse_spec(method.spec.get("signature"))
        binary = tool.primary_binary or repo.split("/")[1]
        target = user_bin_dir() / binary

        def _install(ctx: ExecContext) -> None:
            self._download_and_install(
                ctx,
                repo=repo,
                pattern=pattern,
                checksums_name=checksums,
                signature=signature,
                binary=binary,
                target=target,
                tag=str(method.spec.get("tag") or ""),
            )

        return [
            PythonStep(
                fn=_install,
                description=f"download {repo} release and install {binary}",
                detail=(
                    f"GET {API_ROOT}/repos/{repo}/releases/latest\n"
                    f"  match asset: {pattern or '<auto: platform match>'}\n"
                    f"  verify: {checksums or '<none published -- will refuse>'}\n"
                    f"  signature: {_describe_signature(signature)}\n"
                    f"  install: {target}"
                ),
            )
        ]

    def plan_remove(self, tool: Tool, method: InstallMethod) -> list[Step]:
        repo = str(self.spec_value(method, "repo")).strip()
        binary = tool.primary_binary or repo.split("/")[1]
        target = user_bin_dir() / binary
        return [
            CommandStep(argv=["rm", "-f", "--", str(target)], description=f"remove {target}")
        ]

    def list_installed(self) -> set[str]:
        bin_dir = user_bin_dir()
        if not bin_dir.is_dir():
            return set()
        try:
            return {p.name for p in bin_dir.iterdir() if p.is_file()}
        except OSError:
            return set()

    def installed_version(self, tool: Tool, method: InstallMethod) -> str | None:
        binary = tool.primary_binary
        if binary and shutil.which(binary):
            return ""
        return None

    # -- execution ---------------------------------------------------------

    def _download_and_install(
        self,
        ctx: ExecContext,
        *,
        repo: str,
        pattern: str,
        checksums_name: str,
        binary: str,
        target: Path,
        tag: str = "",
        signature: SignatureSpec | None = None,
    ) -> None:
        release = self._fetch_release(repo, tag)
        assets = [
            ReleaseAsset(
                name=a.get("name", ""),
                url=a.get("browser_download_url", ""),
                size=int(a.get("size") or 0),
            )
            for a in release.get("assets", [])
            if a.get("browser_download_url")
        ]
        if not assets:
            raise ProviderError(f"{repo}: release has no downloadable assets")

        asset = self._select_asset(assets, pattern)
        if asset is None:
            names = ", ".join(a.name for a in assets[:6])
            raise ProviderError(
                f"{repo}: no asset matched {pattern or 'this platform'}. Available: {names}"
            )

        checksum_text = ""
        expected = ""
        if checksums_name:
            checksum_text = self._fetch_checksum_file(assets, checksums_name)
            expected = parse_checksum_file(checksum_text, asset.name)
            if not expected:
                raise VerificationError(
                    f"{checksums_name} has no entry for {asset.name}"
                )

        with tempfile.TemporaryDirectory(prefix="loadout-gh-") as tmpdir:
            tmp = Path(tmpdir)
            archive = tmp / asset.name
            ctx.progress(f"downloading {asset.name}", 10.0)
            self._download(asset.url, archive)

            if signature is not None:
                ctx.progress(f"checking {signature.type} signature", 40.0)
                self._verify_signature(
                    assets=assets,
                    spec=signature,
                    archive=archive,
                    checksum_text=checksum_text,
                    checksums_name=checksums_name,
                    workdir=tmp,
                )

            ctx.progress(f"verifying {asset.name}", 55.0)
            verify_digest(archive, expected, allow_unverified=ctx.allow_unverified)

            ctx.progress("extracting", 70.0)
            extracted = self._extract(archive, tmp / "unpacked", binary)

            ctx.progress(f"installing to {target}", 90.0)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(extracted, target)
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        if str(target.parent) not in os.environ.get("PATH", "").split(os.pathsep):
            ctx.warn(f"{target.parent} is not on your PATH; {binary} will not be runnable")

    def _fetch_release(self, repo: str, tag: str = "") -> dict[str, Any]:
        from ..http_util import polite_get

        url = (
            f"{API_ROOT}/repos/{repo}/releases/tags/{tag}"
            if tag
            else f"{API_ROOT}/repos/{repo}/releases/latest"
        )
        response = polite_get(url, headers={"Accept": "application/vnd.github+json"})
        if response is None:
            raise ProviderError(
                f"could not reach the GitHub API for {repo}",
            )
        if response.status_code == 404:
            raise ProviderError(f"{repo}: no such release")
        if response.status_code == 403:
            raise ProviderError(
                f"{repo}: GitHub API rate limit reached. "
                "Set GITHUB_TOKEN to raise the limit."
            )
        if response.status_code != 200:
            raise ProviderError(f"{repo}: GitHub API returned {response.status_code}")
        try:
            return json.loads(response.text)
        except ValueError as exc:
            raise ProviderError(f"{repo}: malformed API response ({exc})") from exc

    @staticmethod
    def _select_asset(assets: list[ReleaseAsset], pattern: str) -> ReleaseAsset | None:
        if pattern:
            for asset in assets:
                if fnmatch.fnmatch(asset.name, pattern):
                    return asset
            return None
        # No explicit pattern: guess from the running platform.
        import platform

        system = platform.system().lower()
        machine = platform.machine().lower()
        arch_aliases = {
            "x86_64": ("amd64", "x86_64", "x64"),
            "amd64": ("amd64", "x86_64", "x64"),
            "aarch64": ("arm64", "aarch64"),
            "arm64": ("arm64", "aarch64"),
        }.get(machine, (machine,))
        for asset in assets:
            lowered = asset.name.lower()
            if (
                system in lowered
                and any(a in lowered for a in arch_aliases)
                and lowered.endswith((".tar.gz", ".tgz", ".zip", ".tar.xz"))
            ):
                return asset
        return None

    @staticmethod
    def _fetch_checksum_file(assets: list[ReleaseAsset], checksums_name: str) -> str:
        """The whole checksum file, not just one digest.

        A signature over ``SHA256SUMS`` covers the bytes of that file, so the
        caller needs the text itself and not only the line it cares about.
        """
        from ..http_util import polite_get

        match = next(
            (a for a in assets if fnmatch.fnmatch(a.name, checksums_name)),
            None,
        )
        if match is None:
            raise VerificationError(
                f"checksum file {checksums_name!r} is not in the release assets"
            )
        response = polite_get(match.url)
        if response is None or response.status_code != 200:
            raise VerificationError(f"could not download {match.name}")
        return response.text

    def _verify_signature(
        self,
        *,
        assets: list[ReleaseAsset],
        spec: SignatureSpec,
        archive: Path,
        checksum_text: str,
        checksums_name: str,
        workdir: Path,
    ) -> None:
        """Check the detached signature over whichever file it was made about.

        Signing the checksum file and letting the checksums cover the artifacts
        is the more common release practice, so both are supported and the
        catalog says which.
        """
        signature_asset = next(
            (a for a in assets if fnmatch.fnmatch(a.name, spec.asset)),
            None,
        )
        if signature_asset is None:
            names = ", ".join(a.name for a in assets[:6])
            raise VerificationError(
                f"signature file {spec.asset!r} is not in the release assets. "
                f"Available: {names}"
            )

        if spec.signs == SIGNS_CHECKSUMS:
            if not checksums_name:
                raise VerificationError(
                    "the catalog says the signature covers the checksum file, "
                    "but this entry publishes no 'checksums'"
                )
            payload = workdir / "signed-checksums.txt"
            # Write the exact bytes that were downloaded: re-encoding or
            # normalising newlines here would break an otherwise good
            # signature and look like tampering.
            payload.write_bytes(checksum_text.encode("utf-8"))
        else:
            payload = archive

        signature_path = workdir / signature_asset.name
        self._download(signature_asset.url, signature_path)
        verify_signature(payload, signature_path, spec)

    @staticmethod
    def _download(url: str, destination: Path) -> None:
        from ..http_util import polite_get

        response = polite_get(url, timeout=120, stream=True)
        if response is None or response.status_code != 200:
            raise ProviderError(f"download failed: {url}")
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    handle.write(chunk)

    @staticmethod
    def _extract(archive: Path, into: Path, binary: str) -> Path:
        """Unpack and return the path to *binary*, refusing traversal entries."""
        into.mkdir(parents=True, exist_ok=True)
        name = archive.name.lower()

        if name.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")):
            with tarfile.open(archive) as tar:
                members = [m for m in tar.getmembers() if _safe_member(m.name)]
                _extract_tar(tar, into, members)
        elif name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    if _safe_member(info.filename):
                        zf.extract(info, into)
        else:
            # A bare binary, not an archive.
            return archive

        for candidate in sorted(into.rglob("*")):
            if candidate.is_file() and candidate.name == binary:
                return candidate

        # Some projects name the binary after the archive rather than the tool.
        # Only accept that when the archive holds a single file: "there is one
        # candidate" is evidence, "one file happens to be executable" is not --
        # and on Windows every file reports as executable.
        files = [p for p in sorted(into.rglob("*")) if p.is_file()]
        if len(files) == 1:
            return files[0]
        raise ProviderError(
            f"{binary!r} not found inside {archive.name} "
            f"({len(files)} file(s) extracted). "
            f"Set the catalog entry's `binaries:` to the real name."
        )


def _safe_member(name: str) -> bool:
    """Reject absolute paths and ``..`` traversal in archive members.

    Uses PurePosixPath deliberately: archive members always use forward
    slashes, and ``Path("/etc/passwd").is_absolute()`` is *False* on Windows
    because there is no drive letter -- which would have let an absolute member
    through on exactly the platform least able to cope with it.
    """
    if not name or "\x00" in name:
        return False
    normalised = name.replace("\\", "/")
    path = PurePosixPath(normalised)
    if path.is_absolute() or normalised.startswith("/"):
        return False
    # A Windows drive-qualified member is absolute in intent even here.
    if len(normalised) > 1 and normalised[1] == ":":
        return False
    return ".." not in path.parts


def _extract_tar(tar: tarfile.TarFile, into: Path, members: list[tarfile.TarInfo]) -> None:
    # Python 3.12+ enforces a filter; ask for the safe one where available.
    try:
        tar.extractall(into, members=members, filter="data")  # type: ignore[call-arg]
    except TypeError:  # pragma: no cover - Python < 3.12
        tar.extractall(into, members=members)  # noqa: S202 - members pre-filtered

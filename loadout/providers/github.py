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
import re
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
from ..signature import (
    ASSET_PLACEHOLDER,
    SIGNS_CHECKSUMS,
    SignatureSpec,
    parse_spec,
    resolve_asset,
    verify_signature,
)
from .base import CommandStep, Provider, ProviderStatus, PythonStep, Step

if TYPE_CHECKING:  # pragma: no cover
    from ..executor import ExecContext

logger = logging.getLogger("loadout.providers.github")

API_ROOT = "https://api.github.com"


def _describe_verify(checksums: str, spec: SignatureSpec | None) -> str:
    """What --dry-run shows about the checksum step.

    A signature over the artifact makes the checksum optional rather than
    missing, so a signed entry with no checksum file must not be described as
    one that will refuse to install.
    """
    if checksums:
        return checksums
    if spec is not None and spec.signs != SIGNS_CHECKSUMS:
        return f"<none published -- covered by the {spec.type} signature>"
    return "<none published -- will refuse>"


def _describe_signature(spec: SignatureSpec | None) -> str:
    """What --dry-run shows about verification, in one line."""
    if spec is None:
        return "<none published>"
    if spec.signs == SIGNS_CHECKSUMS:
        where = "the checksum file"
    elif ASSET_PLACEHOLDER in spec.asset:
        where = "the selected asset"
    else:
        where = "the artifact"
    pin = spec.key_fingerprint or "pinned key"
    return f"{spec.type} over {where}, {pin}"


def user_bin_dir() -> Path:
    return Path(os.environ.get("LOADOUT_BIN_DIR") or Path.home() / ".local" / "bin")


@dataclass
class ReleaseAsset:
    name: str
    url: str
    size: int


#: Never the binary, even when platform and architecture both match: a
#: detached signature, a checksum listing, an installer this provider does
#: not drive, or metadata. `_select_asset` accepts a bare binary with no
#: extension at all (`_extract` already handles that case), so this list is
#: what stands between "accept anything platform-matched" and picking a
#: project's `tool-linux-amd64.sig` instead of `tool-linux-amd64`.
_NOT_A_BINARY_EXTS = (
    ".sig", ".asc", ".pem", ".pub",
    ".sha256", ".sha256sum", ".sha512", ".sum",
    ".sbom", ".spdx", ".spdx.json", ".cdx.json",
    ".txt", ".json", ".yaml", ".yml", ".md",
    ".msi", ".deb", ".rpm", ".pkg", ".dmg", ".apk",
)


def _any_token(lowered_name: str, tokens: tuple[str, ...]) -> bool:
    """Does *lowered_name* contain any of *tokens* as a whole word?

    A plain substring test is what a short alias needs to be safe: "win" is
    a legitimate short form of "windows" in a release filename, but it is
    also the middle three letters of "darwin" -- a bare `"win" in name` would
    make a Windows host match a macOS asset. Hyphen, underscore, dot and the
    start/end of the string all count as a word boundary, matching how these
    tokens actually appear in release filenames.
    """
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lowered_name)
        for token in tokens
    )


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
                    f"  verify: {_describe_verify(checksums, signature)}\n"
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
        assets = self._assets_of(release, repo)

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

            artifact_signed = False
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
                # verify_signature() raises on failure and is silent on
                # success -- logs a debug line nobody sees during an
                # interactive install. Say so where the log is actually
                # visible: a passing check should be as visible as a failing
                # one, not just the absence of an error.
                ctx.output(f"[green]✓[/green] {signature.type} signature verified")
                ctx.verified(signature.type, True)
                artifact_signed = signature.signs != SIGNS_CHECKSUMS

            ctx.progress(f"verifying {asset.name}", 55.0)
            # Skipped, not waived: claiming --allow-unverified in the log for
            # an install the user verified with a signature would be a lie
            # about how the file was checked.
            if expected or not artifact_signed:
                verify_digest(archive, expected, allow_unverified=ctx.allow_unverified)
            self._report_digest_result(ctx, expected, artifact_signed=artifact_signed)

            self._place_binary(ctx, archive, binary, target, workdir=tmp)

    def _place_binary(
        self, ctx: ExecContext, archive: Path, binary: str, target: Path, *, workdir: Path
    ) -> None:
        """Extract *archive* and put *binary* at *target*.

        Shared by the online install and the offline bundle install, so the two
        cannot drift on extraction safety, permissions, or the PATH warning.
        """
        ctx.progress("extracting", 70.0)
        extracted = self._extract(archive, workdir / "unpacked", binary)

        ctx.progress(f"installing to {target}", 90.0)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted, target)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        if str(target.parent) not in os.environ.get("PATH", "").split(os.pathsep):
            ctx.warn(f"{target.parent} is not on your PATH; {binary} will not be runnable")

    # -- offline bundles ---------------------------------------------------

    def plan_fetch(self, tool: Tool, method: InstallMethod, dest: Path) -> list[Step]:
        """Download the release artifact into *dest*, fully verified.

        Verification happens here, on the connected machine, because that is
        the only place the checksum file and the signature can be fetched from.
        What travels is an artifact already checked against its publisher's
        key; the bundle manifest's own sha256 then covers it in transit.
        """
        repo = str(self.spec_value(method, "repo")).strip()
        if repo.count("/") != 1:
            raise ProviderError(f"github repo must be 'owner/name', got {repo!r}")
        pattern = str(method.spec.get("asset") or "")
        checksums = str(method.spec.get("checksums") or "")
        signature = parse_spec(method.spec.get("signature"))
        tag = str(method.spec.get("tag") or "")

        def _fetch(ctx: ExecContext) -> None:
            release = self._fetch_release(repo, tag)
            assets = self._assets_of(release, repo)
            asset = self._select_asset(assets, pattern)
            if asset is None:
                names = ", ".join(a.name for a in assets[:6])
                raise ProviderError(
                    f"{repo}: no asset matched {pattern or 'this platform'}. "
                    f"Available: {names}"
                )

            checksum_text = ""
            expected = ""
            if checksums:
                checksum_text = self._fetch_checksum_file(assets, checksums)
                expected = parse_checksum_file(checksum_text, asset.name)
                if not expected:
                    raise VerificationError(f"{checksums} has no entry for {asset.name}")

            dest.mkdir(parents=True, exist_ok=True)
            archive = dest / asset.name
            ctx.progress(f"downloading {asset.name}", 20.0)
            self._download(asset.url, archive)

            artifact_signed = False
            if signature is not None:
                ctx.progress(f"checking {signature.type} signature", 55.0)
                self._verify_signature(
                    assets=assets,
                    spec=signature,
                    archive=archive,
                    checksum_text=checksum_text,
                    checksums_name=checksums,
                    workdir=dest,
                )
                ctx.output(f"[green]✓[/green] {signature.type} signature verified")
                ctx.verified(signature.type, True)
                artifact_signed = signature.signs != SIGNS_CHECKSUMS
            ctx.progress(f"verifying {asset.name}", 80.0)
            # Skipped, not waived: claiming --allow-unverified in the log for
            # an install the user verified with a signature would be a lie
            # about how the file was checked.
            if expected or not artifact_signed:
                verify_digest(archive, expected, allow_unverified=ctx.allow_unverified)
            self._report_digest_result(ctx, expected, artifact_signed=artifact_signed)

        return [
            PythonStep(
                fn=_fetch,
                description=f"fetch {repo} release artifact for the bundle",
                detail=(
                    f"GET {API_ROOT}/repos/{repo}/releases/"
                    f"{'tags/' + tag if tag else 'latest'}\n"
                    f"  match asset: {pattern or '<auto: platform match>'}\n"
                    f"  verify: {_describe_verify(checksums, signature)}\n"
                    f"  signature: {_describe_signature(signature)}\n"
                    f"  into: {dest}"
                ),
            )
        ]

    def plan_install_local(
        self, tool: Tool, method: InstallMethod, files: list[Path]
    ) -> list[Step]:
        """Install from an artifact already extracted out of a bundle."""
        repo = str(self.spec_value(method, "repo")).strip()
        binary = tool.primary_binary or repo.split("/")[1]
        target = user_bin_dir() / binary
        archives = [f for f in files if not f.name.endswith((".asc", ".sig", ".minisig"))]
        if not archives:
            raise ProviderError(f"{tool.id}: the bundle holds no artifact for this tool")
        archive = archives[0]

        def _install(ctx: ExecContext) -> None:
            with tempfile.TemporaryDirectory(prefix="loadout-gh-") as tmpdir:
                self._place_binary(ctx, archive, binary, target, workdir=Path(tmpdir))

        return [
            PythonStep(
                fn=_install,
                description=f"install {binary} from the bundle",
                detail=f"{archive.name}  ->  {target}",
            )
        ]

    @staticmethod
    def _assets_of(release: dict[str, Any], repo: str) -> list[ReleaseAsset]:
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
        return assets

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
        # Found live, against real releases, not guessed at: hayabusa names
        # its Linux assets "lin", not "linux"
        # ("hayabusa-4.0.0-lin-x64-gnu.zip"), so the exact string match never
        # fired. trivy -- already in this catalog -- names its macOS assets
        # "macOS", not "darwin" ("trivy_0.74.0_macOS-64bit.tar.gz"), the same
        # gap on the platform this repository cannot run CI on to notice it
        # on. velociraptor ships bare binaries with no archive extension at
        # all ("velociraptor-v0.77.2-linux-amd64") -- _extract() already
        # returns a non-archive asset as-is, but the old extension allowlist
        # excluded it from ever being selected. None of this is a one-off
        # catalog mistake; every catalog entry relies on this same guess.
        system_aliases = {
            "linux": ("linux", "lin"),
            "darwin": ("darwin", "macos", "mac", "osx"),
            "windows": ("windows", "win"),
        }.get(system, (system,))
        arch_aliases = {
            # "64bit" is trivy's -- already in this catalog -- own naming for
            # amd64 ("trivy_0.74.0_macOS-64bit.tar.gz"); confirmed against its
            # real release rather than guessed at.
            "x86_64": ("amd64", "x86_64", "x64", "64bit"),
            "amd64": ("amd64", "x86_64", "x64", "64bit"),
            "aarch64": ("arm64", "aarch64"),
            "arm64": ("arm64", "aarch64"),
        }.get(machine, (machine,))
        for asset in assets:
            lowered = asset.name.lower()
            if lowered.endswith(_NOT_A_BINARY_EXTS):
                # A detached signature or checksum file matches on platform
                # and architecture too whenever it is named after the binary
                # it covers -- "tool-linux-amd64.sig" right next to
                # "tool-linux-amd64" -- so this has to run before, not after,
                # the match below.
                continue
            if _any_token(lowered, system_aliases) and _any_token(lowered, arch_aliases):
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

    @staticmethod
    def _report_digest_result(
        ctx: ExecContext, expected: str, *, artifact_signed: bool = False
    ) -> None:
        """The confirmation verify_digest() itself never gives.

        It raises on a mismatch or a missing checksum without
        ``--allow-unverified``, and is silent -- a debug log line nobody sees
        during an interactive install -- on everything else, which conflates
        two different outcomes into one: reaching this line means the digest
        either matched or was skipped by ``--allow-unverified``, and only
        *expected* being empty tells the two apart. Both need to be visible;
        a passing check should be as loud as a failing one, and skipping the
        check entirely is not a detail to leave to a debug log.

        *artifact_signed* is the third outcome: a signature was checked over
        these exact bytes, so there is nothing left for a checksum to prove.
        Reporting that as "unverified" would be false, and refusing the
        install over it would reject the stronger check for lacking the
        weaker one.
        """
        if expected:
            ctx.output("[green]✓[/green] checksum verified (sha256)")
            ctx.verified("checksum", True)
        elif not artifact_signed:
            ctx.warn("[yellow]![/yellow] no checksum published -- installed unverified")
            ctx.verified("checksum", False)

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
        # Resolved against the artifact that was actually selected, so an
        # entry whose upstream signs each asset separately picks the
        # signature belonging to this platform's asset rather than some
        # other platform's.
        pattern = resolve_asset(spec, archive.name)
        signature_asset = next(
            (a for a in assets if fnmatch.fnmatch(a.name, pattern)),
            None,
        )
        if signature_asset is None:
            names = ", ".join(a.name for a in assets[:6])
            raise VerificationError(
                f"signature file {pattern!r} is not in the release assets. "
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

        versioned = _versioned_binary(into, binary)
        if versioned is not None:
            return versioned

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


#: What may separate a binary's name from the version and platform glued onto
#: it: hayabusa ships ``hayabusa-4.0.0-lin-x64-gnu``, others ``tool_v1.2_linux``.
#: A digit has to follow, because a bare prefix match also takes
#: ``hayabusa_report.css`` out of the same archive.
_VERSIONED_NAME_RE = re.compile(r"^[-_]v?\d")

#: Extensions that rule a file out however well its name matches. Releases
#: routinely ship ``tool-1.2.3.sha256`` and ``tool-1.2.3.sig`` beside the
#: binary those files describe, and a nested archive is not the binary either.
_NOT_A_BINARY = (
    ".txt", ".md", ".rst", ".sig", ".asc", ".pem", ".sha256", ".sha512",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".conf", ".cfg",
    ".css", ".html", ".htm", ".js", ".png", ".svg", ".jpg", ".ico",
    ".gz", ".xz", ".bz2", ".zip", ".tar", ".7z", ".deb", ".rpm",
)


def _root_level_files(into: Path) -> list[Path]:
    """The extracted files at the archive's top level.

    Sees through a single wrapping directory, because tarballs conventionally
    have one and it carries no information about which file is the binary.
    """
    try:
        entries = sorted(into.iterdir())
    except OSError:
        return []
    if len(entries) == 1 and entries[0].is_dir():
        entries = sorted(entries[0].iterdir())
    return [p for p in entries if p.is_file()]


def _versioned_binary(into: Path, binary: str) -> Path | None:
    """The root-level file that is *binary* with a version glued on, if unambiguous.

    Restricted to the archive root on purpose. Hayabusa's release holds 5,670
    files, one of which is the binary ``hayabusa-4.0.0-lin-x64-gnu`` and
    another of which is ``config/html_report/hayabusa_report.css``; matching
    anywhere in the tree would make the choice between them a coin toss.
    Two candidates is a catalog problem, not something to guess at.
    """
    candidates = [
        path
        for path in _root_level_files(into)
        if path.name.startswith(binary)
        and _VERSIONED_NAME_RE.match(path.name[len(binary) :])
        and not path.name.lower().endswith(_NOT_A_BINARY)
    ]
    return candidates[0] if len(candidates) == 1 else None


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

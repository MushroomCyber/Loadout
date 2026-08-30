"""APT provider -- Debian, Kali, Parrot, Ubuntu.

Carries the fixes for the privileged path:

* every package name is validated before it reaches an argv, on install *and*
  remove (the previous release validated only on install);
* ``--`` always separates options from package names, so a name beginning with
  ``-`` can never be read as a flag;
* ``DEBIAN_FRONTEND=noninteractive`` is set, so a debconf prompt whose output is
  piped away cannot hang the process forever;
* installed state comes from one bulk ``dpkg-query``, not one call per package.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from ..errors import ProviderError
from ..model import InstallMethod, Tool
from ..policy import validate_package_name
from .base import CommandStep, Provider, ProviderStatus, PythonStep, Step

#: Written by `loadout mirror set`; used only when offline mode is active.
LOCAL_SOURCES = Path("/etc/apt/sources.list.d/loadout-local.list")


class AptProvider(Provider):
    name = "apt"
    label = "APT (Debian/Kali/Parrot/Ubuntu)"
    required_spec_keys = ("package",)
    executables = ("apt-get",)
    needs_root = True
    default_priority = 10

    def __init__(self, *, offline: bool = False) -> None:
        self.offline = offline

    def _probe_version(self, executable: str) -> str:
        out = self._run_text([executable, "--version"])
        return out.splitlines()[0].strip() if out else ""

    def detect(self) -> ProviderStatus:
        status = super().detect()
        if not status.available:
            return status
        if not shutil.which("dpkg-query"):
            return ProviderStatus(
                name=self.name,
                available=False,
                detail="apt-get present but dpkg-query missing",
            )
        return status

    # -- planning ----------------------------------------------------------

    def _base_argv(self) -> list[str]:
        argv = ["apt-get"]
        if self.offline and LOCAL_SOURCES.exists():
            argv += [
                "-o", "Dir::Etc::sourcelist=/dev/null",
                "-o", f"Dir::Etc::sourceparts={LOCAL_SOURCES.parent}",
            ]
        return argv

    def plan_install(self, tool: Tool, method: InstallMethod) -> list[Step]:
        package = validate_package_name(self.spec_value(method, "package"))
        argv = [
            *self._base_argv(),
            "install",
            "-y",
            "-o", "Dpkg::Options::=--force-confold",
            "--",
            package,
        ]
        return [
            CommandStep(
                argv=argv,
                description=f"apt-get install {package}",
                elevate=True,
            )
        ]

    def plan_fetch(self, tool: Tool, method: InstallMethod, dest: Path) -> list[Step]:
        """Download the package *and its dependency closure* into *dest*.

        `apt-get download` fetches one package and nothing it needs, which on
        an isolated machine means an install that stops on the first missing
        dependency. `install --download-only` resolves the closure the same way
        a real install would, so what lands in the bundle is what the target
        will actually need.

        `--reinstall` matters: without it apt downloads nothing for a package
        already present on the *building* machine, and the bundle silently
        comes out empty for exactly the tools its author uses most.
        """
        package = validate_package_name(self.spec_value(method, "package"))

        def _prepare(ctx) -> None:
            # apt refuses an archives directory that has no `partial/` inside
            # it -- "Archives directory .../partial is missing" -- and will not
            # create one itself. Done as a step rather than in the planner so
            # planning still touches nothing.
            (dest / "partial").mkdir(parents=True, exist_ok=True)

        argv = [
            *self._base_argv(),
            "install",
            "--download-only",
            "--reinstall",
            "-y",
            "-o", f"Dir::Cache::archives={dest}",
            "--",
            package,
        ]
        return [
            PythonStep(
                fn=_prepare,
                description=f"prepare {dest} for apt",
                detail=f"mkdir -p {dest}/partial",
            ),
            CommandStep(
                argv=argv,
                description=f"download {package} and its dependencies",
                # apt takes its own lock and writes to the cache directory even
                # when only downloading.
                elevate=True,
            ),
        ]

    def plan_install_local(
        self, tool: Tool, method: InstallMethod, files: list[Path]
    ) -> list[Step]:
        """Install from .deb files already on disk, resolving between them.

        `apt-get install ./a.deb ./b.deb` rather than `dpkg -i`: apt orders the
        packages and satisfies each one's dependencies from the others in the
        list, where dpkg would fail on whichever it happened to unpack first.
        """
        debs = [str(f) for f in files if f.name.endswith(".deb")]
        if not debs:
            raise ProviderError(
                f"{tool.id}: the bundle holds no .deb files for this tool"
            )
        argv = [
            *self._base_argv(),
            "install",
            "-y",
            "--no-download",
            "--allow-downgrades",
            "-o", "Dpkg::Options::=--force-confold",
            "--",
            *debs,
        ]
        return [
            CommandStep(
                argv=argv,
                description=f"install {tool.id} from {len(debs)} bundled package(s)",
                elevate=True,
            )
        ]

    def plan_remove(self, tool: Tool, method: InstallMethod) -> list[Step]:
        package = validate_package_name(self.spec_value(method, "package"))
        purge = bool(method.spec.get("purge", False))
        argv = [*self._base_argv(), "purge" if purge else "remove", "-y", "--", package]
        return [
            CommandStep(
                argv=argv,
                description=f"apt-get {'purge' if purge else 'remove'} {package}",
                elevate=True,
            )
        ]

    def plan_update(self) -> list[Step]:
        return [
            CommandStep(
                argv=[*self._base_argv(), "update"],
                description="refresh package lists",
                elevate=True,
            )
        ]

    def plan_upgrade(self) -> list[Step]:
        return [
            CommandStep(
                argv=[
                    *self._base_argv(),
                    "upgrade",
                    "-y",
                    "-o", "Dpkg::Options::=--force-confold",
                ],
                description="upgrade all packages",
                elevate=True,
            )
        ]

    # -- inspection --------------------------------------------------------

    def list_installed(self) -> set[str]:
        """One dpkg-query for the whole system."""
        if not shutil.which("dpkg-query"):
            return set()
        out = self._run_text(
            [
                "dpkg-query",
                "-W",
                "-f=${binary:Package}\\t${db:Status-Status}\\n",
            ],
            timeout=30,
        )
        installed: set[str] = set()
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[1].strip() == "installed":
                installed.add(parts[0].split(":")[0])
        return installed

    def installed_version(self, tool: Tool, method: InstallMethod) -> str | None:
        try:
            package = validate_package_name(self.spec_value(method, "package"))
        except Exception:
            return None
        out = self._run_text(
            ["dpkg-query", "-W", "-f=${db:Status-Status}\\t${Version}", "--", package]
        )
        if not out:
            return None
        parts = out.split("\t")
        if len(parts) != 2 or parts[0].strip() != "installed":
            return None
        return parts[1].strip()

    def package_size(self, package: str) -> int:
        """Installed-Size in bytes, from the repo metadata."""
        try:
            package = validate_package_name(package)
        except Exception:
            return 0
        out = self._run_text(["apt-cache", "show", "--", package], timeout=7)
        for line in out.splitlines():
            if line.startswith("Installed-Size:"):
                token = line.split(":", 1)[1].strip().split()
                if token and token[0].isdigit():
                    return int(token[0]) * 1024
        return 0

    def upgradable(self) -> dict[str, str]:
        """``{package: new_version}`` for everything with a pending upgrade."""
        out = self._run_text(["apt", "list", "--upgradable"], timeout=60)
        result: dict[str, str] = {}
        for line in out.splitlines()[1:]:
            if "/" not in line:
                continue
            name = line.split("/", 1)[0].strip()
            parts = line.split()
            version = parts[1] if len(parts) > 1 else ""
            if name:
                result[name] = version
        return result

    def held(self) -> set[str]:
        out = self._run_text(["apt-mark", "showhold"], timeout=10)
        return {line.strip() for line in out.splitlines() if line.strip()}

    def plan_hold(self, package: str, *, hold: bool = True) -> list[Step]:
        package = validate_package_name(package)
        action = "hold" if hold else "unhold"
        return [
            CommandStep(
                argv=["apt-mark", action, "--", package],
                description=f"apt-mark {action} {package}",
                elevate=True,
            )
        ]

    # -- progress ----------------------------------------------------------

    @staticmethod
    def parse_status_line(line: str) -> tuple[float, str] | None:
        """Parse one ``APT::Status-Fd`` record into ``(percent, message)``.

        APT emits ``pmstatus:<pkg>:<percent>:<message>`` on the status fd. This
        is the real progress the previous release faked with
        ``min(95, 5 + line_count * 2)`` -- which pinned every install at 95%
        after 45 lines of output regardless of package size.
        """
        if not line or ":" not in line:
            return None
        parts = line.strip().split(":", 3)
        if len(parts) < 4:
            return None
        kind, _package, percent, message = parts
        if kind not in ("pmstatus", "dlstatus"):
            return None
        try:
            value = float(percent)
        except ValueError:
            return None
        # float() accepts "nan" and "inf". NaN survives the clamp below and
        # would jump the bar straight to 100%, so reject non-finite values here.
        if not math.isfinite(value):
            return None
        return max(0.0, min(100.0, value)), message.strip()


def apt_status_fd_args(fd: int) -> list[str]:
    """Options that make apt report machine-readable progress on *fd*."""
    return ["-o", f"APT::Status-Fd={fd}"]


def dpkg_binaries(package: str, *, limit: int = 12) -> list[str]:
    """Executables a package installs -- the fix for "run the package name".

    ``metasploit-framework`` installs ``msfconsole``; ``exploitdb`` installs
    ``searchsploit``. Synthesising the command from the package name, as the
    previous model did, produced "command not found" for every such tool.
    """
    dpkg = shutil.which("dpkg")
    if not dpkg:
        return []
    try:
        package = validate_package_name(package)
    except Exception:
        return []
    try:
        proc = subprocess.run(  # noqa: S603 - resolved path, validated argv
            [dpkg, "-L", "--", package],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    found: list[str] = []
    for line in proc.stdout.splitlines():
        path = line.strip()
        if not path or path.endswith("/"):
            continue
        parent = Path(path).parent.name
        if parent in ("bin", "sbin"):
            name = Path(path).name
            if name and name not in found:
                found.append(name)
    if not found:
        return []

    # dpkg -L emits paths in alphabetical order, so a naive head-of-list pick
    # makes coreutils' primary binary '[' rather than anything a user wants.
    # Promote the executable that matches the package name, then its prefixed
    # siblings, and only then the rest.
    def rank(name: str) -> int:
        if name == package:
            return 0
        if package.startswith(name) or name.startswith(package):
            return 1
        return 2

    # Stable sort: promotes the package-named binary without reshuffling the
    # rest, so exploitdb keeps searchsploit ahead of an incidental helper.
    found.sort(key=rank)
    return found[:limit]

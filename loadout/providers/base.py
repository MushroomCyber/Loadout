"""Provider contract.

A provider knows how to install one *kind* of thing (apt packages, Go modules,
GitHub release archives). It never prints, never prompts, and never runs a
subprocess during planning -- it returns :class:`Step` objects describing what
*would* happen. That separation is what makes the install path unit-testable
without a Kali box: assert on the planned argv, not on side effects.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import BundleError
from ..model import InstallMethod, Tool

if TYPE_CHECKING:  # pragma: no cover
    from ..executor import ExecContext


#: WSL mounts the Windows drives under /mnt and puts them on PATH, so
#: `which npm` can find a Windows program from inside Linux.
_WINDOWS_MOUNT_RE = re.compile(r"^/mnt/[a-z]/", re.IGNORECASE)


def is_windows_interop(path: str) -> bool:
    """True when *path* is a Windows executable reached through WSL interop.

    It will run -- that is the point of interop -- but a toolchain invoked
    this way installs under a Windows prefix, and the executables it produces
    are Windows binaries that the Linux side cannot run. The install appears
    to succeed, slowly, and leaves nothing usable behind.
    """
    return bool(_WINDOWS_MOUNT_RE.match(str(path)))


@dataclass(frozen=True)
class ProviderStatus:
    """Whether this provider can do anything on this machine."""

    name: str
    available: bool
    version: str = ""
    detail: str = ""
    executable: str = ""


@dataclass
class CommandStep:
    """Run an argv. The only way a provider gets to touch the system."""

    argv: list[str]
    description: str
    elevate: bool = False
    env: dict[str, str] = field(default_factory=dict)
    #: Non-zero exit aborts the plan when True.
    check: bool = True
    #: Seconds; None means no limit (apt installs can legitimately take hours).
    timeout: float | None = None

    def render(self) -> str:
        """The command as it will run. Privilege is shown separately by the UI,
        so do not fake a `sudo` prefix here -- as root there will not be one."""
        return " ".join(self.argv)


@dataclass
class PythonStep:
    """In-process work: download, checksum, extract, symlink.

    Kept distinct from :class:`CommandStep` so a plan can still be *displayed*
    and diffed without executing anything.
    """

    fn: Callable[[ExecContext], None]
    description: str
    #: Human-readable summary of what this does, shown by --dry-run.
    detail: str = ""

    def render(self) -> str:
        return self.detail or self.description


Step = CommandStep | PythonStep


class Provider(ABC):
    """Base class for every install backend."""

    #: Registry key and the value used in catalog ``install:`` entries.
    name: str = ""
    #: Shown in `loadout providers`.
    label: str = ""
    #: Spec keys a catalog entry must supply for this provider.
    required_spec_keys: tuple[str, ...] = ()
    #: Executables that must exist for this provider to be usable.
    executables: tuple[str, ...] = ()
    #: True when its operations need root.
    needs_root: bool = False

    def unusable_reason(self, method: InstallMethod) -> str:
        """Why this specific route cannot work here, or "" if it can.

        Distinct from :class:`ProviderStatus`, which answers "is this backend
        installed at all". A backend can be perfectly healthy and still be
        unable to install one particular package -- pipx is fine, but it cannot
        put a package pinned to Python <3.13 on a machine that only has 3.13.
        Knowing that at planning time turns a wall of pip output into a
        sentence, and turns it up before anything is downloaded.
        """
        return ""
    #: Lower wins when several providers can install the same tool.
    default_priority: int = 50

    # -- detection ---------------------------------------------------------

    #: Reject an executable that is really a Windows program reached through
    #: WSL's interop mounts. See :func:`is_windows_interop`.
    rejects_windows_interop: bool = False

    #: Executables that must also be present, beyond the one that gets invoked.
    #: npm without node is the case that matters: `npm --version` answers
    #: happily from its shell wrapper and then every install fails.
    companion_executables: tuple[str, ...] = ()

    def detect(self) -> ProviderStatus:
        """Is this provider usable here? Cheap; called on every startup."""
        rejected = ""
        for executable in self.executables:
            path = shutil.which(executable)
            if not path:
                continue
            if self.rejects_windows_interop and is_windows_interop(path):
                # Keep looking: a real Linux one may be further along PATH.
                rejected = rejected or path
                continue
            missing = [n for n in self.companion_executables if not shutil.which(n)]
            if missing:
                return ProviderStatus(
                    name=self.name,
                    available=False,
                    detail=f"{executable} is on PATH but {', '.join(missing)} is not",
                )
            return ProviderStatus(
                name=self.name,
                available=True,
                version=self._probe_version(path),
                executable=path,
            )
        if rejected:
            return ProviderStatus(
                name=self.name,
                available=False,
                detail=(
                    f"only the Windows build is on PATH ({rejected}); it installs "
                    "into the Windows filesystem, not this one"
                ),
            )
        return ProviderStatus(
            name=self.name,
            available=False,
            detail=f"none of {', '.join(self.executables)} found on PATH",
        )

    def _probe_version(self, executable: str) -> str:  # pragma: no cover - env specific
        return ""

    # -- planning ----------------------------------------------------------

    @abstractmethod
    def plan_install(self, tool: Tool, method: InstallMethod) -> list[Step]:
        """Steps that would install *tool* via *method*."""

    @abstractmethod
    def plan_remove(self, tool: Tool, method: InstallMethod) -> list[Step]:
        """Steps that would remove *tool* installed via *method*."""

    def plan_fetch(self, tool: Tool, method: InstallMethod, dest: Path) -> list[Step]:
        """Steps that download everything *tool* needs into *dest*, installing
        nothing. This is what makes an offline bundle possible.

        The default refuses. A provider that cannot fetch without also
        installing, or whose artifacts are useless without a compiler on the
        far side, should leave it that way: a bundle that silently contained
        less than it claimed would be discovered on the isolated machine,
        which is the worst possible place to discover it.
        """
        raise BundleError(
            f"{self.name} cannot be bundled for offline install",
            remediation=(
                f"{self.name} needs a build toolchain or a package index on the "
                "target machine. Prefer an apt or github route for tools that "
                "have to travel."
            ),
        )

    def plan_install_local(
        self, tool: Tool, method: InstallMethod, files: list[Path]
    ) -> list[Step]:
        """Steps that install *tool* from already-downloaded *files*.

        The other half of :meth:`plan_fetch`, and the only path that runs on
        the isolated machine.
        """
        raise BundleError(f"{self.name} cannot install from a bundle")

    # -- inspection --------------------------------------------------------

    def installed_version(self, tool: Tool, method: InstallMethod) -> str | None:
        """Installed version, or ``None`` when absent. Default: probe binaries."""
        for binary in tool.binaries:
            if shutil.which(binary):
                return ""
        return None

    def list_installed(self) -> set[str]:
        """Everything this provider manages, for a single bulk status sweep.

        Bulk beats per-tool probing: the previous release ran one ``dpkg -l``
        per package in some paths.
        """
        return set()

    # -- helpers for subclasses -------------------------------------------

    @staticmethod
    def _run_text(argv: list[str], *, timeout: float = 10) -> str:
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout if result.returncode == 0 else ""

    def spec_value(self, method: InstallMethod, key: str, default: Any = None) -> Any:
        value = method.spec.get(key, default)
        if value is None and key in self.required_spec_keys:
            raise KeyError(f"{self.name} install method missing {key!r}")
        return value

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Provider {self.name}>"

"""Provider contract.

A provider knows how to install one *kind* of thing (apt packages, Go modules,
GitHub release archives). It never prints, never prompts, and never runs a
subprocess during planning -- it returns :class:`Step` objects describing what
*would* happen. That separation is what makes the install path unit-testable
without a Kali box: assert on the planned argv, not on side effects.
"""

from __future__ import annotations

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
    #: Lower wins when several providers can install the same tool.
    default_priority: int = 50

    # -- detection ---------------------------------------------------------

    def detect(self) -> ProviderStatus:
        """Is this provider usable here? Cheap; called on every startup."""
        for executable in self.executables:
            path = shutil.which(executable)
            if path:
                return ProviderStatus(
                    name=self.name,
                    available=True,
                    version=self._probe_version(path),
                    executable=path,
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

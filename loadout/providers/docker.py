"""Container provider -- docker or podman.

Also the backend for ``loadout run``: the right answer for a tool you need
exactly once and do not want installed on the box.
"""

from __future__ import annotations

import re
import shutil

from ..errors import ProviderError
from ..model import InstallMethod, Tool
from .base import CommandStep, Provider, ProviderStatus, Step

#: Registry references: ``[host[:port]/]path[:tag][@digest]``.
_IMAGE_RE = re.compile(
    r"^[a-z0-9]([a-z0-9._\-]*[a-z0-9])?"
    r"(:[0-9]+)?"
    r"(/[a-z0-9]([a-z0-9._\-]*[a-z0-9])?)*"
    r"(:[a-zA-Z0-9._\-]+)?"
    r"(@sha256:[a-f0-9]{64})?$"
)


class DockerProvider(Provider):
    name = "docker"
    label = "Containers (docker / podman)"
    required_spec_keys = ("image",)
    executables = ("docker", "podman")
    needs_root = False
    default_priority = 70

    def __init__(self) -> None:
        self._engine: str | None = None

    def engine(self) -> str:
        if self._engine is None:
            for candidate in self.executables:
                if shutil.which(candidate):
                    self._engine = candidate
                    break
            else:
                self._engine = "docker"
        return self._engine

    def detect(self) -> ProviderStatus:
        status = super().detect()
        if not status.available:
            return status
        # Present on PATH is not the same as usable; the daemon may be down.
        probe = self._run_text([self.engine(), "info", "--format", "{{.ServerVersion}}"])
        if not probe.strip():
            return ProviderStatus(
                name=self.name,
                available=False,
                executable=status.executable,
                detail=f"{self.engine()} found but the daemon is not responding",
            )
        return ProviderStatus(
            name=self.name,
            available=True,
            version=probe.strip(),
            executable=status.executable,
        )

    def _validate_image(self, image: str) -> str:
        text = str(image).strip()
        if not text or not _IMAGE_RE.match(text):
            raise ProviderError(f"invalid container image reference: {image!r}")
        return text

    def plan_install(self, tool: Tool, method: InstallMethod) -> list[Step]:
        image = self._validate_image(self.spec_value(method, "image"))
        return [
            CommandStep(
                argv=[self.engine(), "pull", "--", image],
                description=f"pull {image}",
            )
        ]

    def plan_remove(self, tool: Tool, method: InstallMethod) -> list[Step]:
        image = self._validate_image(self.spec_value(method, "image"))
        return [
            CommandStep(
                argv=[self.engine(), "rmi", "--", image],
                description=f"remove image {image}",
                check=False,
            )
        ]

    def plan_run(
        self, tool: Tool, method: InstallMethod, args: list[str]
    ) -> list[Step]:
        """One-shot execution without installing anything."""
        image = self._validate_image(self.spec_value(method, "image"))
        argv = [self.engine(), "run", "--rm", "-it"]
        if method.spec.get("network") == "host":
            argv += ["--network", "host"]
        for volume in method.spec.get("volumes") or []:
            argv += ["-v", str(volume)]
        argv += ["--", image, *args]
        return [
            CommandStep(
                argv=argv,
                description=f"run {image}",
                timeout=None,
            )
        ]

    def list_installed(self) -> set[str]:
        out = self._run_text(
            [self.engine(), "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=20
        )
        return {line.strip() for line in out.splitlines() if line.strip()}

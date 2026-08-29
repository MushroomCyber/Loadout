"""Language-toolchain providers: go, cargo, pipx, gem, npm.

These share a shape -- "a toolchain installs a named module into the user's
prefix, no root required" -- so they share a base. This is where the modern kit
actually lives: nuclei, subfinder, httpx and gowitness ship via ``go install``;
impacket and sqlmap's dev tree via pipx. An apt-only manager misses all of it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..model import InstallMethod, Tool
from .base import CommandStep, Provider, Step


class _ToolchainProvider(Provider):
    """Common behaviour for user-scoped toolchain installs."""

    needs_root = False
    #: argv fragments; ``{spec}`` is substituted with the resolved module spec.
    install_template: tuple[str, ...] = ()
    remove_template: tuple[str, ...] = ()
    spec_key = "module"
    version_flag = "version"

    def _probe_version(self, executable: str) -> str:
        out = self._run_text([executable, self.version_flag])
        return out.strip().splitlines()[0] if out else ""

    def _resolve_spec(self, method: InstallMethod) -> str:
        value = str(self.spec_value(method, self.spec_key)).strip()
        if not value:
            raise KeyError(f"{self.name}: empty {self.spec_key!r}")
        if any(ch.isspace() for ch in value):
            raise ValueError(f"{self.name}: {self.spec_key} may not contain whitespace")
        version = method.spec.get("version")
        if version and "@" not in value and self.name == "go":
            value = f"{value}@{version}"
        return value

    def _render(self, template: tuple[str, ...], spec: str) -> list[str]:
        return [part.replace("{spec}", spec) for part in template]

    def plan_install(self, tool: Tool, method: InstallMethod) -> list[Step]:
        spec = self._resolve_spec(method)
        return [
            CommandStep(
                argv=self._render(self.install_template, spec),
                description=f"{self.name} install {spec}",
                elevate=self.needs_root,
            )
        ]

    def plan_remove(self, tool: Tool, method: InstallMethod) -> list[Step]:
        spec = self._resolve_spec(method)
        if not self.remove_template:
            raise NotImplementedError(
                f"{self.name} cannot uninstall automatically; "
                f"remove the binary from your {self.name} bin directory"
            )
        name = method.spec.get("name") or _basename(spec)
        return [
            CommandStep(
                argv=self._render(self.remove_template, name),
                description=f"{self.name} uninstall {name}",
                elevate=self.needs_root,
            )
        ]


class GoProvider(_ToolchainProvider):
    name = "go"
    label = "Go toolchain (go install)"
    required_spec_keys = ("module",)
    executables = ("go",)
    spec_key = "module"
    install_template = ("go", "install", "{spec}")
    default_priority = 30

    def plan_remove(self, tool: Tool, method: InstallMethod) -> list[Step]:
        """``go`` has no uninstall; deleting the installed binary is the removal."""
        binary = tool.primary_binary or _basename(self._resolve_spec(method)).split("@")[0]
        target = go_bin_dir() / binary
        return [
            CommandStep(
                argv=["rm", "-f", "--", str(target)],
                description=f"remove {target}",
            )
        ]

    def list_installed(self) -> set[str]:
        bin_dir = go_bin_dir()
        if not bin_dir.is_dir():
            return set()
        try:
            return {p.name for p in bin_dir.iterdir() if p.is_file()}
        except OSError:
            return set()


class CargoProvider(_ToolchainProvider):
    name = "cargo"
    label = "Rust toolchain (cargo install)"
    required_spec_keys = ("crate",)
    executables = ("cargo",)
    spec_key = "crate"
    install_template = ("cargo", "install", "--locked", "{spec}")
    remove_template = ("cargo", "uninstall", "{spec}")
    default_priority = 35

    def list_installed(self) -> set[str]:
        out = self._run_text(["cargo", "install", "--list"], timeout=20)
        found: set[str] = set()
        for line in out.splitlines():
            if line and not line.startswith((" ", "\t")):
                found.add(line.split()[0].rstrip(":"))
        return found


class PipxProvider(_ToolchainProvider):
    name = "pipx"
    label = "pipx (isolated Python applications)"
    required_spec_keys = ("package",)
    executables = ("pipx",)
    spec_key = "package"
    install_template = ("pipx", "install", "{spec}")
    remove_template = ("pipx", "uninstall", "{spec}")
    version_flag = "--version"
    default_priority = 25

    def list_installed(self) -> set[str]:
        import json

        out = self._run_text(["pipx", "list", "--json"], timeout=20)
        if not out:
            return set()
        try:
            data = json.loads(out)
        except ValueError:
            return set()
        venvs = data.get("venvs")
        return set(venvs) if isinstance(venvs, dict) else set()


class GemProvider(_ToolchainProvider):
    name = "gem"
    label = "RubyGems"
    required_spec_keys = ("gem",)
    executables = ("gem",)
    spec_key = "gem"
    install_template = ("gem", "install", "--user-install", "{spec}")
    remove_template = ("gem", "uninstall", "-x", "{spec}")
    version_flag = "--version"
    default_priority = 45


class NpmProvider(_ToolchainProvider):
    name = "npm"
    label = "npm (global packages)"
    required_spec_keys = ("package",)
    executables = ("npm",)
    spec_key = "package"
    install_template = ("npm", "install", "--global", "{spec}")
    remove_template = ("npm", "uninstall", "--global", "{spec}")
    version_flag = "--version"
    default_priority = 55


class BrewProvider(_ToolchainProvider):
    name = "brew"
    label = "Homebrew (macOS / Linuxbrew)"
    required_spec_keys = ("formula",)
    executables = ("brew",)
    spec_key = "formula"
    install_template = ("brew", "install", "{spec}")
    remove_template = ("brew", "uninstall", "{spec}")
    version_flag = "--version"
    default_priority = 15

    def plan_install(self, tool: Tool, method: InstallMethod) -> list[Step]:
        spec = self._resolve_spec(method)
        argv = ["brew", "install"]
        if method.spec.get("cask"):
            argv.append("--cask")
        argv.append(spec)
        return [
            CommandStep(argv=argv, description=f"brew install {spec}")
        ]

    def list_installed(self) -> set[str]:
        out = self._run_text(["brew", "list", "--formula", "-1"], timeout=30)
        return {line.strip() for line in out.splitlines() if line.strip()}


def go_bin_dir() -> Path:
    """Where ``go install`` puts binaries: $GOBIN, else $GOPATH/bin, else ~/go/bin."""
    gobin = os.environ.get("GOBIN")
    if gobin:
        return Path(gobin)
    gopath = os.environ.get("GOPATH")
    if gopath:
        return Path(gopath.split(os.pathsep)[0]) / "bin"
    if shutil.which("go"):
        out = Provider._run_text(["go", "env", "GOPATH"])
        if out.strip():
            return Path(out.strip().splitlines()[0]) / "bin"
    return Path.home() / "go" / "bin"


def _basename(spec: str) -> str:
    """``github.com/ffuf/ffuf/v2@latest`` -> ``ffuf``."""
    head = spec.split("@", 1)[0].rstrip("/")
    parts = [p for p in head.split("/") if p]
    if not parts:
        return spec
    tail = parts[-1]
    # Go module major-version suffixes are not the binary name.
    if len(parts) > 1 and tail.startswith("v") and tail[1:].isdigit():
        tail = parts[-2]
    return tail

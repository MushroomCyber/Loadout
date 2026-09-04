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

from ..errors import BundleError
from ..model import InstallMethod, Tool
from .base import CommandStep, Provider, PythonStep, Step

#: Written beside a fetched payload to record the interpreter the artifacts
#: were built for. Wheels and gems are not always portable across minor
#: versions -- `pip download` on a 3.13 box pulls `...-cp313-...whl` -- and an
#: air-gapped machine is the worst place to find that out.
BUILD_MARKER = "loadout-build.json"


class _ToolchainProvider(Provider):
    """Common behaviour for user-scoped toolchain installs."""

    needs_root = False
    #: These install into a prefix and produce executables for the host they
    #: ran on. Reached through WSL interop that prefix is a Windows one, so
    #: the install lands somewhere this system cannot run.
    rejects_windows_interop = True
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

    def _requires_python(self, method: InstallMethod) -> str:
        return str(method.spec.get("requires_python") or "").strip()

    def unusable_reason(self, method: InstallMethod) -> str:
        """Refuse a route no interpreter on this machine can satisfy."""
        specifier = self._requires_python(method)
        if not specifier:
            return ""
        from ..pyversion import InvalidSpecifier, explain_gap, find_interpreter

        try:
            if find_interpreter(specifier) is not None:
                return ""
            return explain_gap(specifier)
        except InvalidSpecifier as exc:  # pragma: no cover - schema rejects these
            return f"unreadable requires_python: {exc}"

    def plan_install(self, tool: Tool, method: InstallMethod) -> list[Step]:
        """Pick the interpreter explicitly when the package constrains it.

        pipx builds the venv with whatever `python3` happens to be, so a
        package supporting 3.10-3.12 fails on a 3.13 box even when 3.12 is
        installed alongside. Naming the interpreter is the difference between
        "cannot install this" and "installs fine".
        """
        specifier = self._requires_python(method)
        if not specifier:
            return super().plan_install(tool, method)

        from ..pyversion import find_interpreter, python_from_path

        interpreter = find_interpreter(specifier)
        if interpreter is None:
            # The planner screens this out via unusable_reason(); reaching here
            # means someone called the provider directly.
            raise NotImplementedError(self.unusable_reason(method))

        spec = self._resolve_spec(method)
        argv = ["pipx", "install", "--python", interpreter, spec]
        return [
            CommandStep(
                argv=argv,
                description=f"pipx install {spec} (on {python_from_path(interpreter)})",
                elevate=self.needs_root,
            )
        ]

    def _fetch_interpreter(self, method: InstallMethod) -> str:
        """The interpreter whose wheels this bundle must contain.

        Downloading with a different one than pipx will install with is how a
        bundle ends up holding a `cp313` wheel for a machine running 3.11.
        """
        specifier = self._requires_python(method)
        if specifier:
            from ..pyversion import find_interpreter

            found = find_interpreter(specifier)
            if found:
                return found
        return shutil.which("python3") or "python3"

    def plan_fetch(self, tool: Tool, method: InstallMethod, dest: Path) -> list[Step]:
        """Download the package and its dependency closure as wheels.

        `--prefer-binary` because the point of a bundle is a target with no
        compiler: given the choice between a wheel and an sdist, take the
        wheel. An sdist that has no wheel still comes down, and still needs a
        toolchain on the far side -- that is upstream's constraint, not one
        this can remove, so it is recorded rather than hidden.
        """
        spec = self._resolve_spec(method)
        interpreter = self._fetch_interpreter(method)

        def _stamp(ctx) -> None:
            import json
            import subprocess as _sp

            probe = "import sys,sysconfig;print(f'{sys.version_info.major}.{sys.version_info.minor}');print(sysconfig.get_platform())"
            try:
                out = _sp.run(  # noqa: S603 - resolved interpreter, fixed argv
                    [interpreter, "-c", probe],
                    capture_output=True, text=True, timeout=30, check=True,
                ).stdout.split()
            except Exception as exc:  # pragma: no cover - probe is trivial
                raise BundleError(f"{tool.id}: could not probe {interpreter}: {exc}") from exc
            wheels = [p for p in dest.glob("*") if p.is_file() and p.name != BUILD_MARKER]
            if not wheels:
                raise BundleError(f"{tool.id}: pip downloaded nothing into {dest}")
            (dest / BUILD_MARKER).write_text(
                json.dumps({"python": out[0], "platform": out[1] if len(out) > 1 else ""}),
                encoding="utf-8",
            )
            ctx.progress(f"{len(wheels)} file(s) for python {out[0]}")

        return [
            CommandStep(
                argv=[
                    interpreter, "-m", "pip", "download",
                    "--dest", str(dest),
                    "--prefer-binary",
                    "--", spec,
                ],
                description=f"download {spec} and its dependencies",
            ),
            PythonStep(
                fn=_stamp,
                description=f"record the interpreter {tool.id} was built for",
                detail=f"write {dest}/{BUILD_MARKER}",
            ),
        ]

    def plan_install_local(
        self, tool: Tool, method: InstallMethod, files: list[Path]
    ) -> list[Step]:
        """Install from the bundled wheels, with no index and no network."""
        spec = self._resolve_spec(method)
        payload = [f for f in files if f.name != BUILD_MARKER]
        if not payload:
            raise BundleError(f"{tool.id}: the bundle holds no wheels for this tool")
        directory = payload[0].parent
        interpreter = self._fetch_interpreter(method)

        def _check(ctx) -> None:
            import json
            import subprocess as _sp

            marker = directory / BUILD_MARKER
            if not marker.exists():
                return
            try:
                built = json.loads(marker.read_text(encoding="utf-8")).get("python", "")
            except ValueError:  # pragma: no cover - corrupt marker
                return
            try:
                here = _sp.run(  # noqa: S603 - resolved interpreter, fixed argv
                    [interpreter, "-c",
                     "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                    capture_output=True, text=True, timeout=30, check=False,
                ).stdout.strip()
            except (OSError, _sp.SubprocessError):
                # Could not measure, so claim nothing. The pipx step that
                # follows reports a missing interpreter far better than a
                # traceback out of a version check would.
                return
            if built and here and built != here:
                raise BundleError(
                    f"{tool.id}: bundled for Python {built}, this machine has {here}",
                    remediation=(
                        f"Wheels compiled for {built} will not import on {here}. "
                        f"Rebuild the bundle on a Python {here} machine, or install "
                        f"Python {built} here."
                    ),
                )

        pip_args = f"--no-index --find-links={directory}"
        return [
            PythonStep(
                fn=_check,
                description="check this machine's Python matches the bundle",
                detail=f"compare {directory}/{BUILD_MARKER} against {interpreter}",
            ),
            CommandStep(
                argv=[
                    "pipx", "install",
                    "--python", interpreter,
                    f"--pip-args={pip_args}",
                    "--", spec,
                ],
                description=f"install {tool.id} from {len(payload)} bundled file(s)",
            ),
        ]

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

    #: Where `gem install --install-dir` is staged before the .gem files are
    #: lifted out of its cache. Kept inside the payload dir so a failed fetch
    #: leaves nothing outside the bundle tree.
    _STAGE = ".stage"

    def plan_fetch(self, tool: Tool, method: InstallMethod, dest: Path) -> list[Step]:
        """Download the gem *and its dependency closure* into *dest*.

        `gem fetch` was the obvious choice and is wrong: it downloads exactly
        the named gem. Measured against zsteg it produced 1 file where the
        install needs 7, so the bundle would have failed on the isolated
        machine with a missing `prime`. `gem install --install-dir` resolves
        the closure properly and leaves every .gem in its `cache/`, which is
        what actually travels.
        """
        spec = self._resolve_spec(method)
        stage = dest / self._STAGE

        def _harvest(ctx) -> None:
            import shutil as _shutil

            cache = stage / "cache"
            gems = sorted(cache.glob("*.gem")) if cache.is_dir() else []
            if not gems:
                raise BundleError(f"{tool.id}: gem downloaded nothing into {cache}")
            for path in gems:
                _shutil.move(str(path), str(dest / path.name))
            # The staged install is a built copy for *this* machine; only the
            # .gem files are portable, so the rest must not reach the bundle.
            _shutil.rmtree(stage, ignore_errors=True)
            ctx.progress(f"{len(gems)} gem(s) staged for {tool.id}")

        return [
            CommandStep(
                # No `--` separator: RubyGems treats it as end-of-arguments
                # and then reports "Please specify at least one gem name".
                # The spec is validated by _resolve_spec, which rejects
                # whitespace, so it cannot smuggle another argument in.
                argv=[
                    "gem", "install",
                    "--install-dir", str(stage),
                    "--no-document",
                    spec,
                ],
                description=f"resolve {spec} and its dependencies",
            ),
            PythonStep(
                fn=_harvest,
                description=f"collect the .gem files for {tool.id}",
                detail=f"move {stage}/cache/*.gem -> {dest}",
            ),
        ]

    def plan_install_local(
        self, tool: Tool, method: InstallMethod, files: list[Path]
    ) -> list[Step]:
        """Install every bundled .gem in one command.

        One `gem install` per file does not work even in dependency order:
        `--install-dir` does not put what it just installed on the resolver's
        path, so the second gem cannot see the first. Handing the whole set to
        one invocation lets RubyGems order them itself.
        """
        gems = [str(f) for f in files if f.name.endswith(".gem")]
        if not gems:
            raise BundleError(f"{tool.id}: the bundle holds no .gem files for this tool")
        return [
            CommandStep(
                argv=[
                    "gem", "install",
                    "--local", "--user-install", "--no-document",
                    *sorted(gems),
                ],
                description=f"install {tool.id} from {len(gems)} bundled gem(s)",
            )
        ]


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
    #: npm is a JavaScript program. Without an interpreter it still answers
    #: `--version` from its shell wrapper, so it looks available right up
    #: until the first install fails.
    companion_executables = ("node",)


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

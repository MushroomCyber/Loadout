"""Executor event stream, provider argv shapes, and archive-handling safety."""

from __future__ import annotations

import tarfile
import zipfile

import pytest

from loadout.errors import LoadoutError, ProviderError
from loadout.executor import (
    EVENT_ACTION_DONE,
    EVENT_PLAN_DONE,
    EVENT_PLAN_START,
    Executor,
)
from loadout.model import InstallMethod, Tool
from loadout.planner import Planner
from loadout.providers.base import PythonStep
from loadout.providers.github import GithubReleaseProvider, ReleaseAsset
from loadout.providers.lang import CargoProvider, GoProvider, PipxProvider, _basename


def method(provider: str, **spec) -> InstallMethod:
    return InstallMethod(provider=provider, spec=spec)


class TestDryRun:
    def test_dry_run_runs_nothing(self, catalog, all_available, monkeypatch):
        import subprocess

        def explode(*_a, **_k):  # pragma: no cover
            raise AssertionError("dry run must not spawn a process")

        monkeypatch.setattr(subprocess, "Popen", explode)
        plan = Planner(catalog, distro="kali", statuses=all_available).plan(["nmap"])
        result = Executor(dry_run=True).run(plan)
        assert result.ok
        assert result.dry_run

    def test_dry_run_emits_the_commands(self, catalog, all_available):
        events = []
        plan = Planner(catalog, distro="kali", statuses=all_available).plan(["nmap"])
        Executor(dry_run=True, sink=events.append).run(plan)
        messages = " ".join(e.message for e in events)
        assert "dry-run" in messages
        assert "apt-get" in messages


class TestEventStream:
    def test_lifecycle_events_are_emitted(self, catalog, all_available):
        events = []
        plan = Planner(catalog, distro="kali", statuses=all_available).plan(["nmap"])
        Executor(dry_run=True, sink=events.append).run(plan)
        kinds = [e.kind for e in events]
        assert kinds[0] == EVENT_PLAN_START
        assert EVENT_ACTION_DONE in kinds
        assert kinds[-1] == EVENT_PLAN_DONE

    def test_failure_is_captured_not_raised(self, catalog, all_available):
        plan = Planner(catalog, distro="kali", statuses=all_available).plan(["nmap"])
        plan.actions[0].steps = [
            PythonStep(fn=_boom, description="explode")
        ]
        result = Executor().run(plan)
        assert not result.ok
        assert "kaboom" in result.failures[0].error

    def test_one_failure_does_not_stop_the_rest(self, catalog, all_available):
        planner = Planner(catalog, distro="kali", statuses=all_available)
        plan = planner.plan(["ffuf", "nuclei"], provider_override="go")
        plan.actions[0].steps = [PythonStep(fn=_boom, description="explode")]
        result = Executor(dry_run=False, sink=lambda _e: None).run(plan)
        assert len(result.results) == 2

    def test_result_serialises(self, catalog, all_available):
        plan = Planner(catalog, distro="kali", statuses=all_available).plan(["nmap"])
        payload = Executor(dry_run=True).run(plan).to_dict()
        assert payload["ok"] is True
        assert payload["results"][0]["tool"] == "nmap"
        assert payload["results"][0]["provider"] == "apt"


class TestLanguageProviders:
    def test_go_install(self):
        steps = GoProvider().plan_install(
            Tool(id="ffuf"), method("go", module="github.com/ffuf/ffuf/v2@latest")
        )
        assert steps[0].argv == ["go", "install", "github.com/ffuf/ffuf/v2@latest"]
        assert steps[0].elevate is False

    def test_go_version_pin_is_applied(self):
        steps = GoProvider().plan_install(
            Tool(id="x"), method("go", module="example.com/x", version="v1.2.3")
        )
        assert steps[0].argv[-1] == "example.com/x@v1.2.3"

    def test_cargo_uses_locked(self):
        steps = CargoProvider().plan_install(
            Tool(id="feroxbuster"), method("cargo", crate="feroxbuster")
        )
        assert steps[0].argv == ["cargo", "install", "--locked", "feroxbuster"]

    def test_pipx_install_and_remove(self):
        provider = PipxProvider()
        tool = Tool(id="sqlmap")
        assert provider.plan_install(tool, method("pipx", package="sqlmap"))[0].argv == [
            "pipx",
            "install",
            "sqlmap",
        ]
        assert provider.plan_remove(tool, method("pipx", package="sqlmap"))[0].argv == [
            "pipx",
            "uninstall",
            "sqlmap",
        ]

    def test_whitespace_in_a_spec_is_refused(self):
        with pytest.raises(ValueError, match="whitespace"):
            GoProvider().plan_install(Tool(id="x"), method("go", module="a b; rm -rf /"))

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("github.com/ffuf/ffuf/v2@latest", "ffuf"),
            ("github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest", "nuclei"),
            ("github.com/sensepost/gowitness@latest", "gowitness"),
            ("example.com/tool", "tool"),
        ],
    )
    def test_module_basename(self, spec, expected):
        assert _basename(spec) == expected

    def test_go_removal_deletes_the_binary(self):
        steps = GoProvider().plan_remove(
            Tool(id="ffuf", binaries=("ffuf",)),
            method("go", module="github.com/ffuf/ffuf/v2@latest"),
        )
        assert steps[0].argv[0] == "rm"
        assert steps[0].argv[-1].endswith("ffuf")


class TestGithubAssetSelection:
    def _assets(self, *names):
        return [ReleaseAsset(name=n, url=f"https://x/{n}", size=1) for n in names]

    def test_explicit_pattern_wins(self):
        assets = self._assets("tool_linux_amd64.tar.gz", "tool_darwin_arm64.tar.gz")
        chosen = GithubReleaseProvider._select_asset(assets, "*linux_amd64*")
        assert chosen.name == "tool_linux_amd64.tar.gz"

    def test_no_match_returns_none_rather_than_guessing(self):
        assets = self._assets("tool_windows.zip")
        assert GithubReleaseProvider._select_asset(assets, "*solaris*") is None

    def test_repo_must_be_owner_slash_name(self):
        with pytest.raises(ProviderError, match="owner/name"):
            GithubReleaseProvider().plan_install(
                Tool(id="x"), method("github", repo="not-a-slug")
            )


class TestGithubSignature:
    """Signatures are resolved while planning, so a broken catalog entry fails
    before anything is downloaded rather than halfway through."""

    def _assets(self, *names):
        return [ReleaseAsset(name=n, url=f"https://x/{n}", size=1) for n in names]

    def test_a_malformed_signature_block_fails_the_plan(self):
        from loadout.errors import VerificationError

        with pytest.raises(VerificationError, match="missing 'asset'"):
            GithubReleaseProvider().plan_install(
                Tool(id="x"),
                method(
                    "github",
                    repo="owner/x",
                    signature={"type": "gpg", "public_key": "k"},
                ),
            )

    def test_no_signature_block_still_plans(self):
        steps = GithubReleaseProvider().plan_install(
            Tool(id="x"), method("github", repo="owner/x")
        )
        assert steps

    def test_dry_run_says_what_will_be_checked(self):
        steps = GithubReleaseProvider().plan_install(
            Tool(id="x"),
            method(
                "github",
                repo="owner/x",
                checksums="*SHA256SUMS",
                signature={
                    "type": "gpg",
                    "asset": "*SHA256SUMS.asc",
                    "public_key": "-----BEGIN PGP PUBLIC KEY BLOCK-----",
                    "key_fingerprint": "A" * 40,
                    "signs": "checksums",
                },
            ),
        )
        detail = steps[0].detail
        assert "gpg over the checksum file" in detail
        assert "A" * 40 in detail

    def test_dry_run_is_explicit_when_nothing_is_signed(self):
        steps = GithubReleaseProvider().plan_install(
            Tool(id="x"), method("github", repo="owner/x", checksums="*SHA256SUMS")
        )
        assert "signature: <none published>" in steps[0].detail

    def test_a_missing_signature_asset_names_what_was_available(self, tmp_path):
        from loadout.errors import VerificationError
        from loadout.signature import parse_spec

        spec = parse_spec(
            {"type": "gpg", "asset": "*.minisig", "public_key": "k"}
        )
        with pytest.raises(VerificationError, match="not in the release assets"):
            GithubReleaseProvider()._verify_signature(
                assets=self._assets("tool.tar.gz", "SHA256SUMS"),
                spec=spec,
                archive=tmp_path / "tool.tar.gz",
                checksum_text="",
                checksums_name="",
                workdir=tmp_path,
            )

    def test_signing_the_checksums_requires_a_checksums_entry(self, tmp_path):
        """`signs: checksums` with nothing to sign is a catalog mistake that
        would otherwise verify a file that was never downloaded."""
        from loadout.errors import VerificationError
        from loadout.signature import parse_spec

        spec = parse_spec(
            {
                "type": "gpg",
                "asset": "*.asc",
                "public_key": "k",
                "signs": "checksums",
            }
        )
        with pytest.raises(VerificationError, match="publishes no 'checksums'"):
            GithubReleaseProvider()._verify_signature(
                assets=self._assets("SHA256SUMS.asc"),
                spec=spec,
                archive=tmp_path / "tool.tar.gz",
                checksum_text="",
                checksums_name="",
                workdir=tmp_path,
            )

    def test_the_checksum_file_is_signed_byte_for_byte(self, tmp_path, monkeypatch):
        """Re-encoding or normalising newlines before verifying would break an
        otherwise good signature and look exactly like tampering."""
        from loadout.signature import parse_spec

        text = "abc123  tool.tar.gz\r\ndef456  other.tar.gz\n"
        seen = {}

        def fake_download(url, destination):
            destination.write_bytes(b"signature")

        def fake_verify(payload, signature, spec):
            seen["payload"] = payload.read_bytes()

        monkeypatch.setattr(GithubReleaseProvider, "_download", staticmethod(fake_download))
        monkeypatch.setattr("loadout.providers.github.verify_signature", fake_verify)

        spec = parse_spec(
            {
                "type": "gpg",
                "asset": "*.asc",
                "public_key": "k",
                "signs": "checksums",
            }
        )
        GithubReleaseProvider()._verify_signature(
            assets=self._assets("SHA256SUMS.asc"),
            spec=spec,
            archive=tmp_path / "tool.tar.gz",
            checksum_text=text,
            checksums_name="*SHA256SUMS",
            workdir=tmp_path,
        )
        assert seen["payload"] == text.encode("utf-8")


class TestArchiveSafety:
    """Extraction must refuse traversal entries -- this runs on downloaded code."""

    @pytest.mark.parametrize(
        "name",
        ["../../etc/passwd", "/etc/passwd", "a/../../b", "../evil", "C:/windows/x",
         r"..\..\windows", ""],
    )
    def test_traversal_members_rejected(self, name):
        from loadout.providers.github import _safe_member

        assert _safe_member(name) is False

    @pytest.mark.parametrize("name", ["tool", "dir/tool", "./tool"])
    def test_normal_members_allowed(self, name):
        from loadout.providers.github import _safe_member

        assert _safe_member(name) is True

    def test_tar_traversal_is_not_written(self, tmp_path):
        archive = tmp_path / "evil.tar.gz"
        payload = tmp_path / "payload"
        payload.write_text("owned", encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(payload, arcname="../escaped")
            tar.add(payload, arcname="tool")

        target = tmp_path / "out"
        found = GithubReleaseProvider._extract(archive, target, "tool")
        assert found.name == "tool"
        assert not (tmp_path / "escaped").exists()

    def test_zip_traversal_is_not_written(self, tmp_path):
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escaped", "owned")
            zf.writestr("tool", "binary")
        target = tmp_path / "out"
        GithubReleaseProvider._extract(archive, target, "tool")
        assert not (tmp_path / "escaped").exists()

    def test_missing_binary_is_an_error(self, tmp_path):
        """Ambiguous archive: several files and none named like the binary."""
        archive = tmp_path / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("something-else", "x")
            zf.writestr("README.md", "docs")
        with pytest.raises(ProviderError, match="not found inside"):
            GithubReleaseProvider._extract(archive, tmp_path / "out", "tool")

    def test_single_file_archive_is_accepted(self, tmp_path):
        """Projects that name the binary after the release still resolve."""
        archive = tmp_path / "b.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("tool_v1.2_linux", "binary")
        found = GithubReleaseProvider._extract(archive, tmp_path / "out", "tool")
        assert found.name == "tool_v1.2_linux"


class TestDockerProvider:
    def test_image_reference_is_validated(self):
        from loadout.providers.docker import DockerProvider

        provider = DockerProvider()
        with pytest.raises(ProviderError, match="invalid container image"):
            provider.plan_install(Tool(id="x"), method("docker", image="bad image; rm -rf /"))

    def test_valid_references_pass(self):
        from loadout.providers.docker import DockerProvider

        provider = DockerProvider()
        for image in ("alpine", "org/tool:1.2", "ghcr.io/org/tool:latest"):
            steps = provider.plan_install(Tool(id="x"), method("docker", image=image))
            assert steps[0].argv[-1] == image


def _boom(_ctx) -> None:
    raise LoadoutError("kaboom")


def test_an_elevated_step_carries_the_noninteractive_env_into_the_argv(monkeypatch):
    """End to end for the wireshark freeze: the executor must name the
    variables it wants preserved, or elevate() has nothing to carry and sudo's
    env_reset drops them. Pinning the argv is the only way to catch this --
    the environment dict looks correct either way.
    """
    from loadout.executor import CommandStep, ExecContext, Executor
    from loadout.policy import Privilege

    spawned: list = []
    executor = Executor(privilege=Privilege(is_root=False, sudo_path="/usr/bin/sudo"))
    monkeypatch.setattr(
        Executor, "_spawn", lambda self, argv, step, ctx: spawned.append(argv) or 0
    )

    step = CommandStep(
        argv=["apt-get", "install", "-y", "--", "wireshark"],
        description="apt-get install wireshark",
        elevate=True,
    )
    executor._run_step(step, ExecContext(emit=lambda _e: None, tool_id="wireshark"))

    argv = spawned[0]
    assert "DEBIAN_FRONTEND=noninteractive" in argv
    assert "NEEDRESTART_MODE=a" in argv
    assert argv.index("DEBIAN_FRONTEND=noninteractive") < argv.index("apt-get")


def test_apt_install_never_prompts_about_a_config_file():
    """`-y` answers apt's own questions; a conffile that changed upstream is
    dpkg's question and needs its own answer. Unanswered, it draws a prompt on
    /dev/tty -- which under a TUI is an invisible hang, not a question."""
    from loadout.model import InstallMethod, Tool
    from loadout.providers.apt import AptProvider

    tool = Tool(id="wireshark")
    method = InstallMethod(provider="apt", spec={"package": "wireshark"})
    argv = AptProvider().plan_install(tool, method)[0].argv
    assert "Dpkg::Options::=--force-confdef" in argv
    assert "Dpkg::Options::=--force-confold" in argv


# ---------------------------------------------------------------------------
# A route the machine cannot run
# ---------------------------------------------------------------------------


def test_pipx_names_the_interpreter_when_the_package_pins_one(monkeypatch):
    """pipx builds the venv with whatever `python3` is, so a package supporting
    3.10-3.12 fails on a 3.13 box even when 3.12 is installed alongside."""
    from loadout.model import InstallMethod, Tool
    from loadout.providers.lang import PipxProvider

    monkeypatch.setattr("loadout.pyversion.find_interpreter", lambda _s: "/usr/bin/python3.12")
    method = InstallMethod(
        provider="pipx", spec={"package": "modelscan", "requires_python": ">=3.10,<3.13"}
    )
    step = PipxProvider().plan_install(Tool(id="modelscan"), method)[0]
    assert step.argv == ["pipx", "install", "--python", "/usr/bin/python3.12", "modelscan"]
    assert "python3.12" in step.description


def test_pipx_leaves_the_argv_alone_when_no_interpreter_is_pinned():
    """Most packages have no meaningful pin; adding --python to every install
    would break the day a machine's python3 moves."""
    from loadout.model import InstallMethod, Tool
    from loadout.providers.lang import PipxProvider

    method = InstallMethod(provider="pipx", spec={"package": "garak"})
    step = PipxProvider().plan_install(Tool(id="garak"), method)[0]
    assert step.argv == ["pipx", "install", "garak"]


def test_a_route_no_interpreter_satisfies_is_reported_before_anything_runs(monkeypatch):
    """Stock Kali has 3.13 and nothing else, so modelscan cannot be installed
    there at all. The answer must be a sentence, not a pip traceback."""
    from loadout.model import InstallMethod
    from loadout.providers.lang import PipxProvider

    monkeypatch.setattr("loadout.pyversion.find_interpreter", lambda _s: None)
    monkeypatch.setattr("loadout.pyversion.explain_gap", lambda s: f"needs Python {s}; has 3.13.12")
    method = InstallMethod(
        provider="pipx", spec={"package": "modelscan", "requires_python": ">=3.10,<3.13"}
    )
    reason = PipxProvider().unusable_reason(method)
    assert ">=3.10,<3.13" in reason
    assert "3.13.12" in reason


def test_a_usable_route_reports_no_reason(monkeypatch):
    from loadout.model import InstallMethod
    from loadout.providers.lang import PipxProvider

    monkeypatch.setattr("loadout.pyversion.find_interpreter", lambda _s: "/usr/bin/python3")
    method = InstallMethod(
        provider="pipx", spec={"package": "pyrit", "requires_python": ">=3.10,<3.15"}
    )
    assert PipxProvider().unusable_reason(method) == ""
    assert PipxProvider().unusable_reason(InstallMethod(provider="pipx", spec={"package": "x"})) == ""


def test_the_planner_drops_an_unusable_route_and_keeps_the_reason(catalog, all_available, monkeypatch):
    """A route that will certainly fail must not be planned. When it was the
    only route, the error has to say why -- "install a package manager" is
    wrong advice when the manager is installed and the package is the problem.
    """
    from loadout.errors import NoViableProvider
    from loadout.model import InstallMethod, Tool
    from loadout.planner import Planner
    from loadout.providers.lang import PipxProvider

    monkeypatch.setattr(
        PipxProvider, "unusable_reason", lambda self, m: "needs Python >=3.10,<3.13; has 3.13.12"
    )
    tool = Tool(
        id="modelscan",
        install=(
            InstallMethod(
                provider="pipx", spec={"package": "modelscan", "requires_python": ">=3.10,<3.13"}
            ),
        ),
    )
    planner = Planner(catalog, distro="kali", statuses=all_available)
    assert planner.viable_methods(tool) == []
    with pytest.raises(NoViableProvider) as caught:
        planner.choose_method(tool)
    assert "3.13.12" in caught.value.remediation


def test_the_merged_apt_transaction_is_as_noninteractive_as_a_single_one(catalog, all_available):
    """Installing a loadout merges every apt action into one apt-get call. That
    merged argv is built separately from the single-package one, and drifted:
    it carried --force-confold but not --force-confdef. It is the path most
    likely to meet a debconf question, since it installs the most packages.
    """
    from loadout.planner import ACTION_INSTALL, Planner

    plan = Planner(catalog, distro="kali", statuses=all_available).plan(
        ["nmap", "masscan"], action=ACTION_INSTALL, skip_installed=False
    )
    merged = [s for a in plan.actions for s in a.steps if "apt-get" in s.argv[0]]
    assert merged, "expected a merged apt step"
    argv = merged[0].argv
    assert "Dpkg::Options::=--force-confdef" in argv
    assert "Dpkg::Options::=--force-confold" in argv


def test_both_apt_install_paths_use_one_definition_of_the_options():
    """Two copies of the same argv fragment is how the drift happened."""
    import inspect

    from loadout.providers.apt import AptProvider

    source = inspect.getsource(AptProvider)
    assert source.count('"Dpkg::Options::=--force-confold"') == 1


# ---------------------------------------------------------------------------
# A toolchain that is on PATH but cannot work here
# ---------------------------------------------------------------------------


def test_a_windows_toolchain_reached_through_wsl_is_not_available(monkeypatch):
    """Under WSL /mnt/c is on PATH, so `which npm` finds the Windows npm even
    when Linux has no node at all. It runs -- that is what interop is for --
    but it installs into a Windows prefix and produces Windows binaries, so the
    install crawls for minutes and leaves nothing this system can execute.
    """
    from loadout.providers.lang import NpmProvider

    monkeypatch.setattr(
        "shutil.which",
        lambda n: "/mnt/c/Program Files/nodejs/npm" if n == "npm" else None,
    )
    status = NpmProvider().detect()
    assert status.available is False
    assert "Windows" in status.detail
    assert "/mnt/c/Program Files/nodejs/npm" in status.detail


def test_a_real_linux_toolchain_is_still_preferred_over_the_windows_one(monkeypatch):
    """Rejecting the Windows build must not reject a working Linux one that
    happens to sit later on PATH."""
    from loadout.providers.lang import NpmProvider

    monkeypatch.setattr(
        "shutil.which", lambda n: {"npm": "/usr/bin/npm", "node": "/usr/bin/node"}.get(n)
    )
    monkeypatch.setattr(NpmProvider, "_probe_version", lambda self, path: "10.9.0")
    status = NpmProvider().detect()
    assert status.available is True
    assert status.executable == "/usr/bin/npm"


def test_npm_without_node_is_not_available(monkeypatch):
    """`npm --version` answers from its shell wrapper with no interpreter
    present, so npm looks fine right up until the first install fails."""
    from loadout.providers.lang import NpmProvider

    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/npm" if n == "npm" else None)
    status = NpmProvider().detect()
    assert status.available is False
    assert "node" in status.detail


def test_apt_is_not_subject_to_the_interop_rule(monkeypatch):
    """The rule is about toolchains that install into a prefix. A system
    package manager is never going to be the Windows one, and blanket-rejecting
    /mnt paths would be a rule about paths rather than about behaviour."""
    from loadout.providers.apt import AptProvider

    assert AptProvider.rejects_windows_interop is False


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/mnt/c/Program Files/nodejs/npm", True),
        ("/mnt/d/tools/go", True),
        ("/MNT/C/npm", True),
        ("/usr/bin/npm", False),
        ("/home/user/.local/bin/npm", False),
        ("/mnt/data/npm", False),  # a real Linux mount, not a drive letter
        ("/mnt/npm", False),
    ],
)
def test_which_paths_count_as_windows_interop(path, expected):
    from loadout.providers.base import is_windows_interop

    assert is_windows_interop(path) is expected


def test_an_unavailable_provider_explains_itself_in_the_plan(catalog, monkeypatch):
    """"No available installer" sends someone to install a package manager they
    already have. The backend's own reason is the actionable half."""
    from loadout.errors import NoViableProvider
    from loadout.model import InstallMethod, Tool
    from loadout.planner import Planner
    from loadout.providers.base import ProviderStatus

    statuses = {
        "npm": ProviderStatus(
            name="npm", available=False, detail="only the Windows build is on PATH"
        )
    }
    tool = Tool(
        id="promptfoo",
        install=(InstallMethod(provider="npm", spec={"package": "promptfoo"}),),
    )
    planner = Planner(catalog, distro="kali", statuses=statuses)
    assert planner.viable_methods(tool) == []
    with pytest.raises(NoViableProvider) as caught:
        planner.choose_method(tool)
    assert "Windows" in caught.value.remediation

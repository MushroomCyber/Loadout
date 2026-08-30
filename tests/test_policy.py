"""The privilege and trust boundary.

Everything here guards something that would be a root-level or code-execution
bug if it regressed, so the coverage is deliberately paranoid.
"""

from __future__ import annotations

import pytest

from loadout.errors import UnsafeArgument, VerificationError
from loadout.policy import (
    Privilege,
    elevate,
    file_digest,
    parse_checksum_file,
    subprocess_env,
    validate_argv,
    validate_package_name,
    verify_digest,
)


class TestPackageNameValidation:
    @pytest.mark.parametrize(
        "name",
        ["nmap", "kali-tools-web", "lib32z1", "python3.11", "g++", "a", "7zip"],
    )
    def test_accepts_real_debian_names(self, name):
        assert validate_package_name(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "; rm -rf /",
            "$(id)",
            "`id`",
            "pkg with space",
            "Package",
            "--force-yes",
            "-o",
            "../../etc/passwd",
            "pkg\nother",
            "pkg&&id",
            "",
            "   ",
            "pkg|tee",
        ],
    )
    def test_rejects_everything_else(self, name):
        with pytest.raises(UnsafeArgument):
            validate_package_name(name)

    def test_leading_dash_is_rejected(self):
        """A name apt would parse as an option must never get through."""
        with pytest.raises(UnsafeArgument):
            validate_package_name("-rf")


class TestArgvValidation:
    def test_rejects_control_characters(self):
        for bad in ["a\nb", "a\x00b", "a\rb", "a\x1bb"]:
            with pytest.raises(UnsafeArgument):
                validate_argv(["echo", bad])

    def test_rejects_empty_argv(self):
        with pytest.raises(UnsafeArgument):
            validate_argv([])

    def test_passes_normal_arguments(self):
        assert validate_argv(["apt-get", "install", "-y", "nmap"]) == [
            "apt-get",
            "install",
            "-y",
            "nmap",
        ]


class TestElevation:
    def test_root_needs_no_prefix(self):
        privilege = Privilege(is_root=True, sudo_path=None)
        assert elevate(["apt-get", "update"], privilege=privilege) == [
            "apt-get",
            "update",
        ]

    def test_non_root_gets_sudo(self):
        privilege = Privilege(is_root=False, sudo_path="/usr/bin/sudo")
        assert elevate(["apt-get", "update"], privilege=privilege)[0] == "/usr/bin/sudo"

    def test_no_sudo_and_not_root_raises(self):
        from loadout.errors import PrivilegeError

        privilege = Privilege(is_root=False, sudo_path=None)
        with pytest.raises(PrivilegeError):
            elevate(["apt-get", "update"], privilege=privilege)

    def test_elevation_still_validates(self):
        privilege = Privilege(is_root=True, sudo_path=None)
        with pytest.raises(UnsafeArgument):
            elevate(["apt-get", "install", "pkg\nrm -rf /"], privilege=privilege)

    def test_sudo_appears_in_exactly_one_module(self):
        """The audit surface for 'what runs as root' must stay one function."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "loadout"
        offenders = []
        for path in root.rglob("*.py"):
            if path.name in ("policy.py",):
                continue
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or "sudo" not in stripped:
                    continue
                # Prose and user-facing strings are fine; an argv literal is not.
                if '"sudo"' in stripped or "'sudo'" in stripped:
                    offenders.append(f"{path.name}:{number}")
        assert offenders == [], f"sudo constructed outside policy.py: {offenders}"

    def test_no_module_ever_asks_for_a_shell(self):
        """README promises "No `shell=True` anywhere" and SECURITY.md rests on
        it: every argv this program builds comes from catalog or user input, so
        one shell would turn a catalog entry into remote code execution.

        The sudo check above proves the claim next to it is enforced; this one
        was not, which is how a documented security property quietly stops
        being true.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "loadout"
        offenders = []
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "shell=True" in stripped.replace(" ", ""):
                    offenders.append(f"{path.name}:{number}")
        assert offenders == [], f"shell=True found: {offenders}"

    def test_no_module_runs_a_string_through_os_system_or_popen(self):
        """`os.system` and `os.popen` are shells by another name."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "loadout"
        offenders = []
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "os.system(" in stripped or "os.popen(" in stripped:
                    offenders.append(f"{path.name}:{number}")
        assert offenders == [], f"shell-by-another-name found: {offenders}"


class TestSubprocessEnv:
    def test_forces_noninteractive_frontends(self):
        env = subprocess_env()
        assert env["DEBIAN_FRONTEND"] == "noninteractive"
        assert env["NEEDRESTART_MODE"] == "a"

    def test_extra_values_are_merged(self):
        assert subprocess_env({"GOBIN": "/tmp/bin"})["GOBIN"] == "/tmp/bin"


class TestVerification:
    def test_matching_digest_passes(self, tmp_path):
        artifact = tmp_path / "tool.tar.gz"
        artifact.write_bytes(b"payload")
        verify_digest(artifact, file_digest(artifact))

    def test_mismatched_digest_raises(self, tmp_path):
        artifact = tmp_path / "tool.tar.gz"
        artifact.write_bytes(b"payload")
        with pytest.raises(VerificationError, match="mismatch"):
            verify_digest(artifact, "0" * 64)

    def test_missing_checksum_is_a_failure_not_a_free_pass(self, tmp_path):
        """No published checksum means refuse, unless explicitly overridden."""
        artifact = tmp_path / "tool.tar.gz"
        artifact.write_bytes(b"payload")
        with pytest.raises(VerificationError, match="No checksum"):
            verify_digest(artifact, "")

    def test_explicit_override_is_honoured(self, tmp_path):
        artifact = tmp_path / "tool.tar.gz"
        artifact.write_bytes(b"payload")
        verify_digest(artifact, "", allow_unverified=True)

    def test_digest_comparison_is_case_insensitive(self, tmp_path):
        artifact = tmp_path / "tool.bin"
        artifact.write_bytes(b"payload")
        verify_digest(artifact, file_digest(artifact).upper())


class TestChecksumParsing:
    def test_coreutils_format(self):
        text = (
            "abc123  other_file.tar.gz\n"
            f"{'d' * 64}  ffuf_2.1.0_linux_amd64.tar.gz\n"
        )
        assert parse_checksum_file(text, "ffuf_2.1.0_linux_amd64.tar.gz") == "d" * 64

    def test_binary_marker_is_stripped(self):
        text = f"{'e' * 64} *tool_linux.zip\n"
        assert parse_checksum_file(text, "tool_linux.zip") == "e" * 64

    def test_comments_and_blank_lines_ignored(self):
        text = f"# generated\n\n{'f' * 64}  tool.tgz\n"
        assert parse_checksum_file(text, "tool.tgz") == "f" * 64

    def test_absent_entry_returns_empty(self):
        assert parse_checksum_file(f"{'a' * 64}  other.tgz\n", "mine.tgz") == ""

    def test_path_prefixes_are_matched_on_basename(self):
        text = f"{'b' * 64}  ./dist/tool_linux_amd64.tar.gz\n"
        assert parse_checksum_file(text, "tool_linux_amd64.tar.gz") == "b" * 64


# ---------------------------------------------------------------------------
# Crossing the sudo boundary
# ---------------------------------------------------------------------------


def test_elevate_carries_named_variables_across_sudos_env_reset():
    """sudo runs with `env_reset`: the environment handed to the sudo process is
    discarded before the real command is exec'd, and DEBIAN_FRONTEND is not in
    Debian's env_keep. Setting it on the subprocess -- which is what
    subprocess_env alone does -- therefore never reached apt-get, and dpkg
    configured packages with the interactive dialog frontend. wireshark-common
    asks whether non-superusers may capture packets, so an install froze on a
    prompt drawn underneath the TUI.
    """
    from loadout.policy import Privilege, elevate

    argv = elevate(
        ["apt-get", "install", "-y", "--", "wireshark"],
        privilege=Privilege(is_root=False, sudo_path="/usr/bin/sudo"),
        preserve={"DEBIAN_FRONTEND": "noninteractive"},
    )
    assert argv[0] == "/usr/bin/sudo"
    assert argv[1].endswith("env")
    assert "DEBIAN_FRONTEND=noninteractive" in argv
    # env must come before the command, or it is an argument to apt-get.
    assert argv.index("DEBIAN_FRONTEND=noninteractive") < argv.index("apt-get")


def test_elevate_does_not_wrap_in_env_when_already_root():
    """No sudo means no env_reset: the subprocess environment arrives intact,
    and an extra exec through env would be noise in the audit log."""
    from loadout.policy import Privilege, elevate

    argv = elevate(
        ["apt-get", "install", "-y"],
        privilege=Privilege(is_root=True, sudo_path=None),
        preserve={"DEBIAN_FRONTEND": "noninteractive"},
    )
    assert argv[0] == "apt-get"


def test_elevate_preserves_nothing_unless_asked():
    """Passing the whole environment through sudo is the thing env_reset exists
    to prevent. Preservation is opt-in, by name."""
    from loadout.policy import Privilege, elevate

    argv = elevate(
        ["apt-get", "update"],
        privilege=Privilege(is_root=False, sudo_path="/usr/bin/sudo"),
    )
    assert argv == ["/usr/bin/sudo", "apt-get", "update"]


def test_a_preserved_variable_name_is_validated():
    """The assignments land in an argv that runs as root. A name carrying its
    own `=` would smuggle a second variable in."""
    import pytest

    from loadout.errors import UnsafeArgument
    from loadout.policy import Privilege, elevate

    for bad in ("PATH=/tmp/x LD_PRELOAD", "lowercase", "HAS SPACE", ""):
        with pytest.raises(UnsafeArgument):
            elevate(
                ["apt-get", "update"],
                privilege=Privilege(is_root=False, sudo_path="/usr/bin/sudo"),
                preserve={bad: "x"},
            )


def test_a_preserved_value_rejects_control_characters():
    import pytest

    from loadout.errors import UnsafeArgument
    from loadout.policy import Privilege, elevate

    with pytest.raises(UnsafeArgument):
        elevate(
            ["apt-get", "update"],
            privilege=Privilege(is_root=False, sudo_path="/usr/bin/sudo"),
            preserve={"DEBIAN_FRONTEND": "noninteractive\nEVIL=1"},
        )


def test_deliberate_env_is_the_noninteractive_set_plus_the_steps_own():
    from loadout.policy import deliberate_env

    env = deliberate_env({"GIT_TERMINAL_PROMPT": "0"})
    assert env["DEBIAN_FRONTEND"] == "noninteractive"
    assert env["NEEDRESTART_MODE"] == "a"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    # Not the inherited environment -- only what loadout sets on purpose.
    assert "PATH" not in env

"""The privilege and trust boundary.

Two rules the rest of the codebase relies on:

1. **``sudo`` appears in exactly one place: :func:`elevate`.** Every privileged
   argv is validated here, logged here, and nowhere else. The audit surface for
   "what can this tool run as root" is this one function.
2. **Nothing downloaded is executed unverified.** :func:`verify_digest` gates
   every artifact a provider fetches; bypassing it requires an explicit
   ``--allow-unverified`` that is threaded through from the CLI, never a default.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import (
    NothingToVerifyAgainst,
    PrivilegeError,
    UnsafeArgument,
    VerificationError,
)
from .model import PACKAGE_NAME_RE

logger = logging.getLogger("loadout.policy")

#: Characters that must never appear in an argv token we construct.
_CONTROL_CHARS = frozenset(chr(c) for c in [*range(0x00, 0x20), 0x7F])

#: Environment applied to every package-manager subprocess. Without this, apt
#: and friends can block forever on a debconf prompt whose output is piped away.
#:
#: These have to survive :func:`elevate` as well -- see :func:`_env_prefix`.
NONINTERACTIVE_ENV = {
    "DEBIAN_FRONTEND": "noninteractive",
    "NEEDRESTART_MODE": "a",
}

#: A shell-safe environment variable name.
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass(frozen=True)
class Privilege:
    """How this process can gain root, decided once at startup."""

    is_root: bool
    sudo_path: str | None

    @property
    def can_elevate(self) -> bool:
        return self.is_root or self.sudo_path is not None

    def prefix(self) -> list[str]:
        if self.is_root:
            return []
        if self.sudo_path:
            return [self.sudo_path]
        raise PrivilegeError(
            "Root privileges are required but neither root nor sudo is available.",
            remediation="Install sudo, or re-run as root.",
        )


def detect_privilege() -> Privilege:
    geteuid = getattr(os, "geteuid", None)
    is_root = False
    if callable(geteuid):
        try:
            is_root = geteuid() == 0
        except OSError:
            is_root = False
    return Privilege(is_root=is_root, sudo_path=shutil.which("sudo"))


def validate_package_name(name: str) -> str:
    """Gate for anything interpolated into a package-manager argv."""
    text = str(name or "").strip()
    if not text:
        raise UnsafeArgument("Empty package name.")
    if not PACKAGE_NAME_RE.match(text):
        raise UnsafeArgument(
            f"Refusing unsafe package name: {text!r}",
            remediation="Package names must match [a-z0-9][a-z0-9+.-]*",
        )
    return text


def validate_argv(argv: Sequence[str]) -> list[str]:
    """Reject control characters in any token before it reaches a subprocess."""
    out: list[str] = []
    for token in argv:
        text = str(token)
        if any(ch in _CONTROL_CHARS for ch in text):
            raise UnsafeArgument(f"Refusing argv token with control characters: {text!r}")
        out.append(text)
    if not out:
        raise UnsafeArgument("Refusing to run an empty command.")
    return out


#: sudo's own prompt, with loadout named in it. `%p` is the account whose
#: password is wanted -- sudo expands it, we never interpolate a username
#: ourselves. Without this the user sees a bare `[sudo] password for ...` with
#: nothing connecting it to the button they just pressed.
SUDO_PROMPT = "[loadout] password for %p: "


def refresh_credentials(
    privilege: Privilege | None = None,
    *,
    timeout: int = 120,
    prompt: str = SUDO_PROMPT,
) -> bool:
    """Prime the sudo timestamp *before* any UI takes over the terminal.

    The previous release ran ``sudo apt-get`` with stdout piped from inside a
    full-screen TUI, so the password prompt was drawn over and the install
    looked like a hang. Callers must invoke this while the terminal is still
    theirs, and treat ``False`` as "do not proceed".

    The password is read by sudo from ``/dev/tty`` and never passes through
    this process. That is deliberate, and it is why callers have to hand the
    terminal back rather than collecting it in a text box.
    """
    privilege = privilege or detect_privilege()
    if privilege.is_root:
        return True
    if not privilege.sudo_path:
        return False
    argv = [privilege.sudo_path, "-v"]
    if prompt:
        argv[1:1] = ["-p", prompt]
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("sudo -v timed out after %ss", timeout)
        return False
    except KeyboardInterrupt:
        # Ctrl+C at the password prompt is a cancellation, not a crash.
        return False
    except OSError as exc:
        logger.warning("sudo -v failed: %s", exc)
        return False
    return result.returncode == 0


def has_cached_credentials(privilege: Privilege | None = None) -> bool:
    """True when sudo will *not* prompt. Used to decide whether to suspend a TUI."""
    privilege = privilege or detect_privilege()
    if privilege.is_root:
        return True
    if not privilege.sudo_path:
        return False
    try:
        return (
            subprocess.run(  # noqa: S603
                [privilege.sudo_path, "-n", "true"],
                capture_output=True,
                timeout=5,
                check=False,
            ).returncode
            == 0
        )
    except (subprocess.TimeoutExpired, OSError):
        return False


def _env_prefix(preserve: Mapping[str, str]) -> list[str]:
    """``['env', 'NAME=value', ...]`` -- how variables cross the sudo boundary.

    sudo runs with ``env_reset`` by default, so the environment we hand to the
    *sudo* process is discarded before the real command is exec'd. Assigning on
    sudo's own command line (``sudo NAME=value cmd``) needs the SETENV sudoers
    tag and fails outright without it. Running ``env`` as root works
    everywhere, because env is just a program that builds an environment.
    """
    argv = [shutil.which("env") or "/usr/bin/env"]
    for name, value in sorted(preserve.items()):
        if not _ENV_NAME_RE.match(name):
            raise UnsafeArgument(f"Refusing to export unsafe variable name: {name!r}")
        text = str(value)
        if any(ch in _CONTROL_CHARS for ch in text):
            raise UnsafeArgument(f"Refusing control characters in ${name}")
        argv.append(f"{name}={text}")
    return argv


def elevate(
    argv: Sequence[str],
    *,
    privilege: Privilege | None = None,
    preserve: Mapping[str, str] | None = None,
) -> list[str]:
    """Return *argv* prefixed with sudo when required. The only sudo call site.

    *preserve* names the variables that must reach the elevated command rather
    than being dropped by sudo's ``env_reset``. Nothing is preserved unless a
    caller asks for it by name -- passing the whole environment through sudo is
    the thing env_reset exists to prevent.
    """
    privilege = privilege or detect_privilege()
    checked = validate_argv(argv)
    prefix = privilege.prefix()
    if prefix and preserve:
        # Already root means no sudo and no env_reset: the variables we set on
        # the subprocess arrive on their own.
        checked = [*_env_prefix(preserve), *checked]
    full = [*prefix, *checked]
    logger.info("elevating: %s", " ".join(full))
    return full


def deliberate_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The variables loadout sets on purpose, as opposed to those inherited.

    This is exactly the set that has to survive elevation: everything else in
    the environment either belongs to the user's shell or is sudo's business.
    """
    env = dict(NONINTERACTIVE_ENV)
    if extra:
        env.update(extra)
    return env


def subprocess_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(deliberate_env(extra))
    return env


# ---------------------------------------------------------------------------
# Artifact verification
# ---------------------------------------------------------------------------

_CHUNK = 1024 * 1024


def file_digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify_digest(
    path: Path,
    expected: str,
    *,
    algorithm: str = "sha256",
    allow_unverified: bool = False,
) -> None:
    """Raise :class:`VerificationError` unless *path* matches *expected*.

    An empty *expected* means the catalog offered no checksum: that is a
    verification failure, not a free pass, unless the caller explicitly opted
    out.
    """
    if not expected:
        if allow_unverified:
            logger.warning("installing %s with no checksum (--allow-unverified)", path.name)
            return
        raise NothingToVerifyAgainst(f"No checksum published for {path.name}.")

    actual = file_digest(path, algorithm)
    if actual.lower() != expected.strip().lower():
        raise VerificationError(
            f"{algorithm} mismatch for {path.name}: "
            f"expected {expected[:16]}..., got {actual[:16]}..."
        )
    logger.debug("%s verified (%s)", path.name, algorithm)


def parse_checksum_file(text: str, filename: str) -> str:
    """Pull one digest out of a ``sha256sums.txt``-style listing.

    Handles both ``<digest>  <name>`` (coreutils) and ``<name>: <digest>``.
    """
    target = Path(filename).name
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            digest, name = parts[0], parts[-1].lstrip("*")
            if Path(name).name == target and _looks_like_digest(digest):
                return digest
            if Path(parts[0].rstrip(":")).name == target and _looks_like_digest(parts[-1]):
                return parts[-1]
    return ""


def _looks_like_digest(value: str) -> bool:
    return len(value) in (32, 40, 56, 64, 96, 128) and all(
        c in "0123456789abcdefABCDEF" for c in value
    )

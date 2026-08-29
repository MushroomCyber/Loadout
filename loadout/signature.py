"""Cryptographic signature verification for downloaded artifacts.

A checksum proves a download was not corrupted in transit. It does not prove
who produced it: on GitHub the checksum file sits beside the artifact, served
by the same account, so whoever can replace one can replace both. A signature
is the part that binds an artifact to a key, and the key is pinned in the
catalog -- a file reviewed by pull request -- rather than fetched from a
keyserver at install time.

Three schemes are supported because release engineering has not converged on
one. Each delegates to the tool that owns the format rather than
reimplementing the cryptography:

* ``gpg``      -- detached OpenPGP signature, still the default for most
                  security tooling and every Debian derivative.
* ``minisign`` -- small ed25519 signatures, increasingly common for Go and
                  Rust projects.
* ``cosign``   -- Sigstore, either key-based or keyless with a pinned
                  certificate identity.

Two rules hold across all three, and are why this module exists rather than a
one-line ``subprocess.run(["gpg", "--verify", ...])``:

1. **A valid signature is not enough.** ``gpg --verify`` exits 0 for a good
   signature by *any* key the keyring happens to hold, so an attacker who can
   get a key in there passes. Verification here confirms the signing key is
   the pinned one by parsing the machine-readable status output, never the
   exit code alone.
2. **Verification never touches the user's keyring.** Every gpg invocation
   runs against a throwaway ``GNUPGHOME`` holding only the pinned key, so a
   catalog entry can neither read nor pollute the user's own trust store.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import VerificationError
from .policy import validate_argv

logger = logging.getLogger("loadout.signature")

#: What the signature is made over. Most projects sign the checksum file and
#: let the checksums cover the artifacts; some sign each artifact directly.
SIGNS_ARTIFACT = "artifact"
SIGNS_CHECKSUMS = "checksums"
SIGNS_CHOICES = (SIGNS_ARTIFACT, SIGNS_CHECKSUMS)

TYPE_GPG = "gpg"
TYPE_MINISIGN = "minisign"
TYPE_COSIGN = "cosign"
SIGNATURE_TYPES = (TYPE_GPG, TYPE_MINISIGN, TYPE_COSIGN)

#: An OpenPGP fingerprint as gpg reports it: 40 hex characters, no spaces.
_FINGERPRINT_RE = re.compile(r"^[0-9A-F]{40}$")

#: ``[GNUPG:] VALIDSIG <fpr> <date> ...`` on the status channel. The first
#: field is the fingerprint of the key that made the signature.
_VALIDSIG_RE = re.compile(r"^\[GNUPG:\] VALIDSIG ([0-9A-F]{40})\b", re.M)

_VERIFY_TIMEOUT = 60


@dataclass(frozen=True)
class SignatureSpec:
    """The signature block of a catalog install method, already validated."""

    type: str
    #: Glob matching the detached signature among the release's assets.
    asset: str
    #: Trust anchor. Armoured public key for gpg, base64 line for minisign,
    #: PEM public key for cosign. Empty only for keyless cosign.
    public_key: str = ""
    #: Optional extra assertion for gpg: the signature must be by this key.
    key_fingerprint: str = ""
    #: Keyless cosign only.
    certificate_identity: str = ""
    certificate_oidc_issuer: str = ""
    #: Whether the signature covers the artifact or the checksum file.
    signs: str = SIGNS_ARTIFACT

    @property
    def tool(self) -> str:
        return {
            TYPE_GPG: "gpg",
            TYPE_MINISIGN: "minisign",
            TYPE_COSIGN: "cosign",
        }[self.type]


def parse_spec(raw: Any) -> SignatureSpec | None:
    """Build a :class:`SignatureSpec`, or raise if the block is malformed.

    Returns ``None`` only when no signature was declared at all. A declared but
    broken block is an error, never a silent downgrade to unsigned.
    """
    if raw is None or raw == "":
        return None
    errors = validate_spec(raw)
    if errors:
        raise VerificationError("; ".join(errors))
    data = dict(raw)
    return SignatureSpec(
        type=str(data.get("type", "")).strip().lower(),
        asset=str(data.get("asset", "")).strip(),
        public_key=str(data.get("public_key", "")).strip(),
        key_fingerprint=str(data.get("key_fingerprint", "")).replace(" ", "").upper(),
        certificate_identity=str(data.get("certificate_identity", "")).strip(),
        certificate_oidc_issuer=str(data.get("certificate_oidc_issuer", "")).strip(),
        signs=str(data.get("signs", SIGNS_ARTIFACT)).strip().lower() or SIGNS_ARTIFACT,
    )


def validate_spec(raw: Any) -> list[str]:
    """Catalog-time validation, so a broken block fails review rather than an
    install on someone else's machine."""
    if not isinstance(raw, dict):
        return ["'signature' must be a mapping"]

    errors: list[str] = []
    kind = str(raw.get("type", "")).strip().lower()
    if not kind:
        errors.append("signature: missing 'type'")
    elif kind not in SIGNATURE_TYPES:
        errors.append(
            f"signature: unknown type {kind!r}. Valid: {', '.join(SIGNATURE_TYPES)}"
        )

    if not str(raw.get("asset", "")).strip():
        errors.append("signature: missing 'asset' (glob matching the signature file)")

    signs = str(raw.get("signs", SIGNS_ARTIFACT)).strip().lower() or SIGNS_ARTIFACT
    if signs not in SIGNS_CHOICES:
        errors.append(
            f"signature: 'signs' must be one of {', '.join(SIGNS_CHOICES)}, "
            f"got {signs!r}"
        )

    public_key = str(raw.get("public_key", "")).strip()
    fingerprint = str(raw.get("key_fingerprint", "")).replace(" ", "").upper()

    if kind == TYPE_GPG:
        if not public_key:
            errors.append(
                "signature: gpg requires 'public_key' (the armoured key). A "
                "fingerprint alone would mean fetching the key from a keyserver "
                "at install time, which is not a trust anchor."
            )
        if fingerprint and not _FINGERPRINT_RE.match(fingerprint):
            errors.append(
                "signature: 'key_fingerprint' must be 40 hex characters "
                f"(got {len(fingerprint)})"
            )
    elif kind == TYPE_MINISIGN:
        if not public_key:
            errors.append("signature: minisign requires 'public_key'")
    elif kind == TYPE_COSIGN:
        identity = str(raw.get("certificate_identity", "")).strip()
        issuer = str(raw.get("certificate_oidc_issuer", "")).strip()
        if not public_key and not (identity and issuer):
            errors.append(
                "signature: cosign requires either 'public_key', or both "
                "'certificate_identity' and 'certificate_oidc_issuer' for "
                "keyless verification. Keyless with no pinned identity would "
                "accept a signature from anyone."
            )
    return errors


def backend_available(spec: SignatureSpec) -> bool:
    return shutil.which(spec.tool) is not None


def verify_signature(payload: Path, signature: Path, spec: SignatureSpec) -> None:
    """Raise :class:`VerificationError` unless *signature* is a good signature
    over *payload* by the key the catalog pinned.

    There is deliberately no ``allow_unverified`` escape here. That flag is for
    the case where a project publishes nothing to verify against; a signature
    the catalog *declares* and the artifact fails is an active signal, not a
    missing one.
    """
    if shutil.which(spec.tool) is None:
        raise VerificationError(
            f"{spec.tool} is required to verify this download but is not installed."
        )

    verifier = {
        TYPE_GPG: _verify_gpg,
        TYPE_MINISIGN: _verify_minisign,
        TYPE_COSIGN: _verify_cosign,
    }[spec.type]
    verifier(payload, signature, spec)
    logger.debug("%s: %s signature verified", payload.name, spec.type)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def _run(
    argv: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run a verifier. No shell, argv validated, never inherits stdin."""
    checked = validate_argv(argv)
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    try:
        return subprocess.run(  # noqa: S603 - argv validated above, no shell
            checked,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_VERIFY_TIMEOUT,
            env=full_env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError(
            f"{checked[0]} did not finish within {_VERIFY_TIMEOUT}s"
        ) from exc


def _verify_gpg(payload: Path, signature: Path, spec: SignatureSpec) -> None:
    """Verify against a throwaway keyring holding only the pinned key.

    Using the user's real ``GNUPGHOME`` would mean any key they happen to trust
    could satisfy a catalog entry, and would let a catalog entry write into
    their trust store.
    """
    with tempfile.TemporaryDirectory(prefix="loadout-gpg-") as home:
        home_path = Path(home)
        # gpg refuses to run against a world-readable home.
        home_path.chmod(0o700)
        env = {"GNUPGHOME": str(home_path), "LC_ALL": "C"}
        base = ["gpg", "--batch", "--no-tty", "--homedir", str(home_path)]

        key_file = home_path / "pinned.asc"
        key_file.write_text(spec.public_key, encoding="utf-8")
        imported = _run([*base, "--import", str(key_file)], env=env)
        if imported.returncode != 0:
            raise VerificationError(
                "the public key pinned in the catalog could not be imported: "
                + _tail(imported.stderr)
            )

        result = _run(
            [*base, "--status-fd", "1", "--verify", str(signature), str(payload)],
            env=env,
        )
        fingerprints = _VALIDSIG_RE.findall(result.stdout)
        if result.returncode != 0 or not fingerprints:
            raise VerificationError(
                f"no valid signature for {payload.name}: " + _tail(result.stderr)
            )
        # The throwaway keyring already constrains this, but assert it
        # explicitly: the check is the whole point of the module and must not
        # depend on how the temporary keyring happened to be populated.
        if spec.key_fingerprint and spec.key_fingerprint not in fingerprints:
            raise VerificationError(
                f"{payload.name} is signed by {fingerprints[0]}, "
                f"not the pinned {spec.key_fingerprint}"
            )


def _verify_minisign(payload: Path, signature: Path, spec: SignatureSpec) -> None:
    result = _run(
        [
            "minisign",
            "-V",
            "-P",
            spec.public_key,
            "-x",
            str(signature),
            "-m",
            str(payload),
        ]
    )
    if result.returncode != 0:
        raise VerificationError(
            f"minisign rejected {payload.name}: "
            + _tail(result.stderr or result.stdout)
        )


def _verify_cosign(payload: Path, signature: Path, spec: SignatureSpec) -> None:
    argv = ["cosign", "verify-blob", "--signature", str(signature)]
    if spec.public_key:
        # cosign wants a file on disk; the catalog carries the key inline.
        with tempfile.TemporaryDirectory(prefix="loadout-cosign-") as tmp:
            key_file = Path(tmp) / "cosign.pub"
            key_file.write_text(spec.public_key, encoding="utf-8")
            result = _run([*argv, "--key", str(key_file), str(payload)])
    else:
        result = _run(
            [
                *argv,
                "--certificate-identity",
                spec.certificate_identity,
                "--certificate-oidc-issuer",
                spec.certificate_oidc_issuer,
                str(payload),
            ]
        )
    if result.returncode != 0:
        raise VerificationError(
            f"cosign rejected {payload.name}: " + _tail(result.stderr or result.stdout)
        )


def _tail(text: str, lines: int = 3) -> str:
    """The last few lines of a verifier's complaint.

    Reporting only "exit 2" makes the user re-run the command by hand to learn
    anything, which is the failure mode the executor already avoids.
    """
    kept = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " / ".join(kept[-lines:]) if kept else "no output"

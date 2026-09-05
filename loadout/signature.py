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

#: A bare detached signature. What gpg and minisign always produce, and what
#: cosign produced before Sigstore bundles.
FORMAT_DETACHED = "detached"

#: A Sigstore bundle -- one `.sigstore.json` carrying the signature, the
#: signing certificate and the transparency-log entry together. cosign now
#: deprecates `--signature` in favour of `--bundle`, and projects that publish
#: only a bundle (sigstore/cosign itself, trivy) could not be verified at all
#: while `--signature` was the only form this understood.
FORMAT_BUNDLE = "bundle"

FORMAT_CHOICES = (FORMAT_DETACHED, FORMAT_BUNDLE)

#: Stands, inside ``asset``, for the name of the release asset that platform
#: detection actually picked. Some projects publish no shared checksum file
#: and instead sign every asset separately, so the signature's name is only
#: knowable once the artifact has been chosen -- Velocidex ships
#: ``velociraptor-v0.77.2-linux-amd64.sig`` beside
#: ``velociraptor-v0.77.2-linux-amd64``, and a fixed glob would either miss
#: it or match the wrong platform's signature.
ASSET_PLACEHOLDER = "{asset}"

#: Anything else in braces is a typo, and one that would otherwise surface as
#: "signature file not in the release assets" during someone's install.
_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")

TYPE_GPG = "gpg"
TYPE_MINISIGN = "minisign"
TYPE_COSIGN = "cosign"
SIGNATURE_TYPES = (TYPE_GPG, TYPE_MINISIGN, TYPE_COSIGN)

#: An OpenPGP fingerprint as gpg reports it: 40 hex characters, no spaces.
_FINGERPRINT_RE = re.compile(r"^[0-9A-F]{40}$")

#: cosign matches ``--certificate-identity-regexp`` unanchored, so the pattern
#: ``github.com/aquasecurity/trivy`` is also satisfied by
#: ``https://evil.example/github.com/aquasecurity/trivy/...``. An identity
#: pattern that does not pin both ends is not a pin, so the catalog refuses
#: one rather than shipping a check that looks strict and is not.
_ANCHORED_RE = re.compile(r"^\^.*\$$", re.S)

#: ``[GNUPG:] VALIDSIG <fpr> <date> ...`` on the status channel. The first
#: field is the fingerprint of the key that made the signature.
_VALIDSIG_RE = re.compile(r"^\[GNUPG:\] VALIDSIG ([0-9A-F]{40})\b", re.M)

_VERIFY_TIMEOUT = 60

#: A Unix domain socket path is capped at about 104 bytes on macOS and 108 on
#: Linux, and gpg-agent puts its socket inside GNUPGHOME. macOS hands out
#: ``/var/folders/<hash>/T`` as TMPDIR, which is long enough that a temporary
#: home built there fails with "can't connect to the gpg-agent: File name too
#: long" before gpg does any work. Somewhere short is not a preference here.
_SOCKET_PATH_BUDGET = 90


def short_tmpdir() -> str | None:
    """A base for GNUPGHOME whose path leaves room for the agent socket.

    Returns ``None`` to mean "the platform default is fine", which is the case
    on Linux where TMPDIR is already ``/tmp``.
    """
    default = tempfile.gettempdir()
    if len(default) <= _SOCKET_PATH_BUDGET // 2:
        return None
    # Only ever the *parent*: TemporaryDirectory below still uses mkdtemp, so
    # the directory actually used has an unpredictable name and mode 0700.
    # That is the pattern S108 exists to require, not the one it warns about.
    candidate = Path("/tmp")  # noqa: S108
    if candidate.is_dir() and os.access(candidate, os.W_OK):
        return str(candidate)
    return None


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
    #: Alternative to ``certificate_identity`` for projects whose signing
    #: identity moves with each release. trivy signs from
    #: ``.../reusable-release.yaml@refs/tags/v0.74.0``, so a literal pin is
    #: stale the moment the next tag is cut; anchore/syft signs from
    #: ``@refs/heads/main`` and needs no pattern at all.
    certificate_identity_regexp: str = ""
    certificate_oidc_issuer: str = ""
    #: Glob matching the signing certificate, for keyless cosign in the
    #: pre-bundle form: anchore ships `checksums.txt.pem` beside
    #: `checksums.txt.sig`. Without the certificate there is nothing to check
    #: the pinned identity against, so keyless verification of that shape
    #: could not work at all.
    certificate: str = ""
    #: :data:`FORMAT_DETACHED` or :data:`FORMAT_BUNDLE`.
    format: str = FORMAT_DETACHED
    #: Whether the signature covers the artifact or the checksum file.
    signs: str = SIGNS_ARTIFACT

    @property
    def needs_certificate(self) -> bool:
        """Does verification need a separate certificate asset fetched too?"""
        return bool(self.certificate) and self.format != FORMAT_BUNDLE

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
        certificate_identity_regexp=str(
            data.get("certificate_identity_regexp", "")
        ).strip(),
        certificate_oidc_issuer=str(data.get("certificate_oidc_issuer", "")).strip(),
        certificate=str(data.get("certificate", "")).strip(),
        format=str(data.get("format", FORMAT_DETACHED)).strip().lower() or FORMAT_DETACHED,
        signs=str(data.get("signs", SIGNS_ARTIFACT)).strip().lower() or SIGNS_ARTIFACT,
    )


def resolve_asset(spec: SignatureSpec, artifact_name: str) -> str:
    """The glob to match the signature file with, for this artifact."""
    return spec.asset.replace(ASSET_PLACEHOLDER, artifact_name)


def resolve_asset_certificate(spec: SignatureSpec, artifact_name: str) -> str:
    """The glob to match the signing certificate with, for this artifact."""
    return spec.certificate.replace(ASSET_PLACEHOLDER, artifact_name)


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

    asset = str(raw.get("asset", "")).strip()
    if not asset:
        errors.append("signature: missing 'asset' (glob matching the signature file)")

    fmt = str(raw.get("format", FORMAT_DETACHED)).strip().lower() or FORMAT_DETACHED
    if fmt not in FORMAT_CHOICES:
        errors.append(
            f"signature: 'format' must be one of {', '.join(FORMAT_CHOICES)}, "
            f"got {fmt!r}"
        )
    if fmt == FORMAT_BUNDLE and kind != TYPE_COSIGN:
        errors.append(
            f"signature: 'format: bundle' is a Sigstore concept and only "
            f"applies to cosign, not {kind or 'an unset type'}"
        )

    certificate = str(raw.get("certificate", "")).strip()
    if certificate and kind != TYPE_COSIGN:
        errors.append(
            f"signature: 'certificate' only applies to cosign, not "
            f"{kind or 'an unset type'}"
        )
    if certificate and fmt == FORMAT_BUNDLE:
        errors.append(
            "signature: a bundle already carries its certificate; drop "
            "'certificate' or drop 'format: bundle'"
        )

    signs = str(raw.get("signs", SIGNS_ARTIFACT)).strip().lower() or SIGNS_ARTIFACT
    if signs not in SIGNS_CHOICES:
        errors.append(
            f"signature: 'signs' must be one of {', '.join(SIGNS_CHOICES)}, "
            f"got {signs!r}"
        )

    unknown = [
        token
        for token in _PLACEHOLDER_RE.findall(asset)
        if token != ASSET_PLACEHOLDER
    ]
    if unknown:
        errors.append(
            f"signature: unknown placeholder(s) in 'asset': {', '.join(unknown)}. "
            f"Only {ASSET_PLACEHOLDER} is substituted."
        )
    if ASSET_PLACEHOLDER in asset and signs == SIGNS_CHECKSUMS:
        errors.append(
            f"signature: 'asset' names itself after the artifact "
            f"({ASSET_PLACEHOLDER}) but 'signs' says it covers the checksum "
            "file. A per-artifact signature covers that artifact."
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
        pattern = str(raw.get("certificate_identity_regexp", "")).strip()
        issuer = str(raw.get("certificate_oidc_issuer", "")).strip()
        if identity and pattern:
            errors.append(
                "signature: set 'certificate_identity' or "
                "'certificate_identity_regexp', not both -- cosign takes one "
                "and which one won would not be visible from the catalog."
            )
        if not public_key and not ((identity or pattern) and issuer):
            errors.append(
                "signature: cosign requires either 'public_key', or "
                "'certificate_identity' (or 'certificate_identity_regexp') "
                "together with 'certificate_oidc_issuer' for keyless "
                "verification. Keyless with no pinned identity would accept a "
                "signature from anyone."
            )
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(
                    f"signature: 'certificate_identity_regexp' is not a valid "
                    f"regular expression: {exc}"
                )
            else:
                if not _ANCHORED_RE.match(pattern):
                    errors.append(
                        "signature: 'certificate_identity_regexp' must be "
                        "anchored with ^ and $. cosign matches it unanchored, "
                        "so an unanchored pattern also accepts identities that "
                        "merely contain it."
                    )
        if not public_key and fmt == FORMAT_DETACHED and not certificate:
            errors.append(
                "signature: keyless cosign with a detached signature also "
                "needs 'certificate' (the .pem beside the .sig). Without it "
                "there is no certificate to check the pinned identity against."
            )
    return errors


def backend_available(spec: SignatureSpec) -> bool:
    return shutil.which(spec.tool) is not None


def verify_signature(
    payload: Path,
    signature: Path,
    spec: SignatureSpec,
    *,
    certificate: Path | None = None,
) -> None:
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

    if spec.type == TYPE_COSIGN:
        _verify_cosign(payload, signature, spec, certificate=certificate)
    else:
        verifier = {TYPE_GPG: _verify_gpg, TYPE_MINISIGN: _verify_minisign}[spec.type]
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
    with tempfile.TemporaryDirectory(prefix="lo-gpg-", dir=short_tmpdir()) as home:
        home_path = Path(home)
        # gpg refuses to run against a world-readable home.
        home_path.chmod(0o700)
        env = {"GNUPGHOME": str(home_path), "LC_ALL": "C"}
        # --no-autostart: importing a public key and checking a detached
        # signature are both agent-free operations, so there is no reason to
        # spawn gpg-agent, open a socket under GNUPGHOME, or leave a stray
        # process behind on the user's machine.
        base = [
            "gpg",
            "--batch",
            "--no-tty",
            "--no-autostart",
            "--homedir",
            str(home_path),
        ]

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


def _verify_cosign(
    payload: Path,
    signature: Path,
    spec: SignatureSpec,
    *,
    certificate: Path | None = None,
) -> None:
    """Three shapes, because Sigstore has published three.

    A bundle carries signature, certificate and transparency-log entry in one
    `.sigstore.json` and goes in via ``--bundle``; cosign now deprecates
    ``--signature`` in its favour. The older keyless form is a `.sig` plus a
    separate `.pem`, and without that certificate there is nothing to check
    the pinned identity against. A key-based signature needs neither.
    """
    if spec.format == FORMAT_BUNDLE:
        argv = ["cosign", "verify-blob", "--bundle", str(signature)]
    else:
        argv = ["cosign", "verify-blob", "--signature", str(signature)]
        if certificate is not None:
            argv += ["--certificate", str(certificate)]

    if spec.public_key:
        # cosign wants a file on disk; the catalog carries the key inline.
        with tempfile.TemporaryDirectory(prefix="loadout-cosign-") as tmp:
            key_file = Path(tmp) / "cosign.pub"
            key_file.write_text(spec.public_key, encoding="utf-8")
            result = _run([*argv, "--key", str(key_file), str(payload)])
    else:
        if spec.certificate_identity_regexp:
            identity = ["--certificate-identity-regexp", spec.certificate_identity_regexp]
        else:
            identity = ["--certificate-identity", spec.certificate_identity]
        result = _run(
            [
                *argv,
                *identity,
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

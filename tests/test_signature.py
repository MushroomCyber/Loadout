"""Signature verification.

The interesting tests here drive real ``gpg`` with real keys, because the
property that matters cannot be tested against a mock: ``gpg --verify`` exits 0
for a good signature by *any* key it knows about, so a verifier that trusts the
exit code passes a signature made by an attacker's key. Only an end-to-end test
with two keys shows whether the pinned one is actually enforced.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from loadout.errors import VerificationError
from loadout.signature import (
    SIGNS_CHECKSUMS,
    SignatureSpec,
    parse_spec,
    validate_spec,
    verify_signature,
)

#: Git Bash ships an MSYS gpg that resolves Windows paths against its own
#: POSIX root, so `--homedir C:\...` lands somewhere that does not exist.
#: Loadout targets Linux, macOS and WSL; these run there and in CI.
gpg_required = pytest.mark.skipif(
    shutil.which("gpg") is None or sys.platform == "win32",
    reason="needs a POSIX gpg",
)


# ---------------------------------------------------------------------------
# Catalog-time validation
# ---------------------------------------------------------------------------


def test_no_signature_block_is_not_an_error():
    assert parse_spec(None) is None
    assert parse_spec("") is None


def test_a_declared_but_broken_block_raises_rather_than_downgrading():
    """The dangerous failure is a typo silently meaning 'unsigned'."""
    with pytest.raises(VerificationError):
        parse_spec({"type": "gpg"})  # no asset, no key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, "missing 'type'"),
        ({"type": "pgp", "asset": "x.sig"}, "unknown type"),
        ({"type": "gpg", "public_key": "k"}, "missing 'asset'"),
        ({"type": "gpg", "asset": "x.asc"}, "requires 'public_key'"),
        ({"type": "minisign", "asset": "x.minisig"}, "requires 'public_key'"),
        ({"type": "cosign", "asset": "x.sig"}, "requires either 'public_key'"),
        (
            {"type": "gpg", "asset": "x.asc", "public_key": "k", "signs": "sbom"},
            "'signs' must be one of",
        ),
        (
            {
                "type": "gpg",
                "asset": "x.asc",
                "public_key": "k",
                "key_fingerprint": "ABC123",
            },
            "40 hex characters",
        ),
    ],
)
def test_validate_spec_names_the_specific_problem(raw, expected):
    errors = " ".join(validate_spec(raw))
    assert expected in errors, errors


def test_a_fingerprint_alone_is_rejected_for_gpg():
    """Pinning only a fingerprint would mean fetching the key from a keyserver
    at install time, which moves the trust anchor off the reviewed catalog."""
    errors = " ".join(
        validate_spec(
            {
                "type": "gpg",
                "asset": "*.asc",
                "key_fingerprint": "A" * 40,
            }
        )
    )
    assert "requires 'public_key'" in errors


def test_keyless_cosign_needs_a_pinned_identity():
    """Keyless verification with no identity accepts a signature from anyone
    who can get a Sigstore certificate, which is everyone."""
    errors = " ".join(validate_spec({"type": "cosign", "asset": "*.sig"}))
    assert "certificate_identity" in errors

    ok = validate_spec(
        {
            "type": "cosign",
            "asset": "*.sig",
            "certificate_identity": "https://github.com/ffuf/ffuf/.github/workflows/release.yml@refs/tags/v2.1.0",
            "certificate_oidc_issuer": "https://token.actions.githubusercontent.com",
        }
    )
    assert ok == []


def test_fingerprint_is_normalised_the_way_people_paste_it():
    """gpg prints fingerprints in spaced groups; a catalog author will paste
    exactly that."""
    spec = parse_spec(
        {
            "type": "gpg",
            "asset": "*.asc",
            "public_key": "-----BEGIN PGP PUBLIC KEY BLOCK-----",
            "key_fingerprint": "aaaa bbbb cccc dddd eeee  ffff 0000 1111 2222 3333",
        }
    )
    assert spec is not None
    assert spec.key_fingerprint == "AAAABBBBCCCCDDDDEEEEFFFF00001111222233 33".replace(
        " ", ""
    )


# ---------------------------------------------------------------------------
# Real gpg
# ---------------------------------------------------------------------------


class Keyring:
    """A throwaway gpg home used to *produce* test signatures."""

    def __init__(self, home: Path) -> None:
        self.home = home
        home.mkdir(parents=True, exist_ok=True)
        try:
            home.chmod(0o700)
        except OSError:  # pragma: no cover - Windows
            pass

    def _gpg(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "gpg",
                "--batch",
                "--no-tty",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--homedir",
                str(self.home),
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def create_key(self, uid: str) -> str:
        result = self._gpg(
            "--quick-generate-key", uid, "ed25519", "sign", "never"
        )
        assert result.returncode == 0, result.stderr
        listing = self._gpg("--list-keys", "--with-colons", uid)
        for line in listing.stdout.splitlines():
            if line.startswith("fpr:"):
                return line.split(":")[9]
        raise AssertionError(f"no fingerprint for {uid}: {listing.stdout}")

    def export(self, fingerprint: str) -> str:
        result = self._gpg("--armor", "--export", fingerprint)
        assert result.returncode == 0, result.stderr
        return result.stdout

    def sign(self, fingerprint: str, payload: Path, out: Path) -> Path:
        result = self._gpg(
            "--local-user",
            fingerprint,
            "--armor",
            "--detach-sign",
            "--output",
            str(out),
            str(payload),
        )
        assert result.returncode == 0, result.stderr
        return out


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    """Two real keypairs: the one a catalog would pin, and an attacker's."""
    if shutil.which("gpg") is None or sys.platform == "win32":
        pytest.skip("needs a POSIX gpg")
    ring = Keyring(tmp_path_factory.mktemp("signing-home"))
    trusted = ring.create_key("Loadout Trusted <trusted@example.invalid>")
    attacker = ring.create_key("Loadout Attacker <attacker@example.invalid>")
    return {
        "ring": ring,
        "trusted": trusted,
        "trusted_pub": ring.export(trusted),
        "attacker": attacker,
        "attacker_pub": ring.export(attacker),
    }


@pytest.fixture
def artifact(tmp_path):
    path = tmp_path / "tool-1.2.3-linux-amd64.tar.gz"
    path.write_bytes(b"pretend this is a release archive")
    return path


@gpg_required
def test_a_signature_by_the_pinned_key_is_accepted(keys, artifact, tmp_path):
    signature = keys["ring"].sign(keys["trusted"], artifact, tmp_path / "a.asc")
    spec = parse_spec(
        {
            "type": "gpg",
            "asset": "*.asc",
            "public_key": keys["trusted_pub"],
            "key_fingerprint": keys["trusted"],
        }
    )
    assert spec is not None
    verify_signature(artifact, signature, spec)  # must not raise


@gpg_required
def test_a_signature_by_another_key_is_rejected(keys, artifact, tmp_path):
    """The whole point. `gpg --verify` would exit 0 for this signature if the
    attacker's key were in the keyring, so trusting the exit code alone is what
    this test exists to prevent."""
    signature = keys["ring"].sign(keys["attacker"], artifact, tmp_path / "a.asc")
    spec = parse_spec(
        {
            "type": "gpg",
            "asset": "*.asc",
            "public_key": keys["trusted_pub"],
            "key_fingerprint": keys["trusted"],
        }
    )
    assert spec is not None
    with pytest.raises(VerificationError, match="no valid signature"):
        verify_signature(artifact, signature, spec)


@gpg_required
def test_the_pinned_fingerprint_must_match_the_pinned_key(keys, artifact, tmp_path):
    """A catalog entry whose public_key and key_fingerprint disagree is an
    authoring mistake, and must fail rather than quietly trusting the key."""
    signature = keys["ring"].sign(keys["trusted"], artifact, tmp_path / "a.asc")
    spec = parse_spec(
        {
            "type": "gpg",
            "asset": "*.asc",
            "public_key": keys["trusted_pub"],
            "key_fingerprint": keys["attacker"],  # disagrees with the key above
        }
    )
    assert spec is not None
    with pytest.raises(VerificationError, match="not the pinned"):
        verify_signature(artifact, signature, spec)


@gpg_required
def test_a_tampered_artifact_is_rejected(keys, artifact, tmp_path):
    signature = keys["ring"].sign(keys["trusted"], artifact, tmp_path / "a.asc")
    artifact.write_bytes(b"pretend this is a MALICIOUS release archive")
    spec = parse_spec(
        {
            "type": "gpg",
            "asset": "*.asc",
            "public_key": keys["trusted_pub"],
        }
    )
    assert spec is not None
    with pytest.raises(VerificationError, match="no valid signature"):
        verify_signature(artifact, signature, spec)


@gpg_required
def test_verification_does_not_touch_the_users_keyring(
    keys, artifact, tmp_path, monkeypatch
):
    """A catalog entry must not be able to read or write the user's trust
    store. Point GNUPGHOME at a directory that must stay empty."""
    user_home = tmp_path / "user-gnupg"
    user_home.mkdir()
    monkeypatch.setenv("GNUPGHOME", str(user_home))

    signature = keys["ring"].sign(keys["trusted"], artifact, tmp_path / "a.asc")
    spec = parse_spec(
        {"type": "gpg", "asset": "*.asc", "public_key": keys["trusted_pub"]}
    )
    assert spec is not None
    verify_signature(artifact, signature, spec)

    assert list(user_home.iterdir()) == [], "verification wrote to the user's GNUPGHOME"


@gpg_required
def test_a_garbage_public_key_fails_loudly(keys, artifact, tmp_path):
    signature = keys["ring"].sign(keys["trusted"], artifact, tmp_path / "a.asc")
    spec = parse_spec(
        {"type": "gpg", "asset": "*.asc", "public_key": "not a key at all"}
    )
    assert spec is not None
    with pytest.raises(VerificationError, match="could not be imported"):
        verify_signature(artifact, signature, spec)


# ---------------------------------------------------------------------------
# Backend availability
# ---------------------------------------------------------------------------


def test_a_missing_verifier_is_a_refusal_not_a_skip(artifact, tmp_path, monkeypatch):
    """If the tool that checks the signature is absent, the safe answer is to
    refuse -- not to install the artifact unchecked."""
    monkeypatch.setattr("loadout.signature.shutil.which", lambda _name: None)
    spec = SignatureSpec(type="minisign", asset="*.minisig", public_key="RWQf6L")
    with pytest.raises(VerificationError, match="minisign is required"):
        verify_signature(artifact, tmp_path / "x.minisig", spec)


def test_verify_signature_has_no_allow_unverified_escape():
    """--allow-unverified covers 'the project publishes nothing to check
    against'. A signature the catalog declares and the artifact fails is an
    active signal, and must not be waivable by the same flag."""
    import inspect

    assert "allow_unverified" not in inspect.signature(verify_signature).parameters


def test_signs_defaults_to_the_artifact_but_can_name_the_checksum_file():
    default = parse_spec(
        {"type": "minisign", "asset": "*.minisig", "public_key": "RWQ"}
    )
    assert default is not None
    assert default.signs == "artifact"

    over_sums = parse_spec(
        {
            "type": "minisign",
            "asset": "*.minisig",
            "public_key": "RWQ",
            "signs": "checksums",
        }
    )
    assert over_sums is not None
    assert over_sums.signs == SIGNS_CHECKSUMS

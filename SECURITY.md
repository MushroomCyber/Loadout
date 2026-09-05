# Security Policy

## Reporting a vulnerability

**Please do not open a public issue.** Use a
[private security advisory](https://github.com/MushroomCyber/Loadout/security/advisories/new).

Include what the issue is, how to reproduce it, and your read on the impact.
Expect an acknowledgement within 7 days.

## Threat model

Loadout is privileged software with a supply chain. Four things make it worth
attacking:

1. **It runs `sudo`.** Anything that smuggles an argument into a package-manager
   invocation is a root-level bug.
2. **It downloads and executes third-party binaries.** The `github` provider
   fetches release archives, unpacks them and puts an executable on your PATH.
3. **It reads catalog data.** The bundled catalog, a downloaded one, and a
   user's own `loadout.yaml` are all untrusted input.
4. **It installs from offline bundles.** A bundle is a tar file carried in by
   hand, usually onto a machine chosen because it is isolated. It is the one
   input that arrives from outside with no network available to check it
   against.

## Controls

### Privilege

- `sudo` is constructed in exactly one function, `policy.elevate()`.
  `tests/test_policy.py::test_sudo_appears_in_exactly_one_module` scans the tree
  and fails the build if that stops being true.
- Every package name is validated against `^[a-z0-9][a-z0-9+.\-]*$` before it
  reaches an argv, on install *and* removal.
- `--` always separates options from package names, so a name beginning with `-`
  cannot be read as a flag.
- No `shell=True` anywhere. Every argv is a list, and control characters in any
  token are rejected.
- Subprocesses run with `stdin=DEVNULL` and `DEBIAN_FRONTEND=noninteractive`, so
  a prompt becomes a clean failure instead of an invisible hang.
- **Your sudo password never passes through Loadout.** The interactive browser
  hands the terminal back so `sudo` can read it from `/dev/tty` itself. That is
  why there is no password box in the app: a nicer-looking one would mean the
  password travelling through this process. A test fails if the credential path
  ever grows an `input()`, a stdin pipe or a `getpass` call.

### Downloads

- Release artifacts are checksummed against the release's own checksum file.
- **A missing checksum is a failure, not a warning.** Installing without one
  requires `--allow-unverified`, passed explicitly by the user.
- Archive extraction refuses absolute paths and `..` traversal, and uses tar's
  `data` filter on Python 3.12+.
- The GitHub API is reached over HTTPS only; non-HTTP(S) schemes are refused.
- `loadout audit` reports installed tools whose catalog entry offers no
  verifiable install route.

### Signatures

A checksum file sits beside the artifact on the same server. Whoever can
replace one can replace both, so a checksum proves the download arrived
intact, not that the right person published it. Where upstream signs its
releases, the catalog pins the trust anchor.

- `gpg`, `minisign` and `cosign` are all supported, including Sigstore in the
  three shapes projects actually publish: a `.sigstore.json` bundle, a detached
  `.sig` with its `.pem`, and a key-based signature.
- **The signer must be the pinned one.** `gpg --verify` exits 0 for a good
  signature by any key in the keyring, so the exit code alone is not the check.
  Loadout parses gpg's status output and compares the fingerprint.
- gpg runs against a throwaway `GNUPGHOME` holding only the pinned key. A
  catalog entry can neither read nor write your own keyring.
- Keyless Sigstore pins an OIDC identity. Where a project's identity carries
  the release tag it is pinned as a pattern, and **an unanchored pattern is
  refused when the catalog is built**: cosign matches these unanchored, so
  `github.com/owner/tool` alone would also accept
  `https://evil.example/github.com/owner/tool/...`.
- **A signature the catalog declares cannot be waived.** `--allow-unverified`
  covers projects that publish nothing to check against. It does not cover a
  check that ran and failed.

### Offline bundles

- Every file is checksummed into the manifest when the bundle is built and
  checked again before use. Without that, a bundle would be a clean way around
  every other check here.
- Member paths are validated before extraction. No `..`, absolute path,
  symlink or device node can write outside the target directory.
- The build platform and Python version are recorded, and a mismatch is
  refused. A bundle of amd64 debs is not a kit on an arm64 box.

### Data

- Catalog entries are validated against a schema before use; unknown providers,
  categories and phases are rejected.
- SQL is fully parameterised. The only interpolation is placeholder counts.
- Writes are atomic — a crash cannot leave a half-written catalog or state file.

### Project supply chain

- CI pins GitHub Actions to commit SHAs.
- `pip-audit` runs on every push and pull request. `--strict` is not set: the
  local package is not on PyPI so it has to be skipped, and `--strict` would
  make that skip fatal for a reason unrelated to any dependency. pip-audit
  still exits non-zero on a real vulnerability, which is the check that matters.
- `ruff --select S` (the bandit ruleset) runs on the package.
- Runtime dependencies are three (`rich`, `requests`, `PyYAML`), none needing a
  compiler.
- Releases are built only by a tag push, and the tag, `pyproject.toml` and
  `loadout/__init__.py` must all agree on the version or nothing is built.

## Residual risks

These are real and not currently mitigated. Know them before you rely on this:

- **Most catalog entries carry no signature pin.** The machinery works, but a
  pin only applies to a `github` route, and only five of the seventeen GitHub
  projects in the catalog publish anything to pin. Four of those five are
  pinned. Everything else rests on checksums, which prove the artifact matches
  what the release published and nothing about who published it.
- **You trust your APT sources.** Loadout proxies `apt-get`; it does not add
  verification on top of it.
- **You trust the catalog you are running.** A malicious catalog entry cannot
  inject shell metacharacters, but it can point a tool id at the wrong package
  or repository. Review catalog changes as you would code.
- **`--allow-unverified` does what it says.** It exists because some upstreams
  publish no checksums; using it is a decision, not a default.
- **`loadout run` executes container images** named by the catalog.

## Scope

In scope: privilege escalation, argument injection, path traversal, verification
bypass, catalog-driven code execution, state corruption.

Out of scope: vulnerabilities in the tools loadout installs (report those
upstream), and the inherent risk of running security tooling as root.

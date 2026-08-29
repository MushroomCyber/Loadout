# Security Policy

## Reporting a vulnerability

**Please do not open a public issue.** Use a
[private security advisory](https://github.com/MushroomCyber/Kali-Tools-Manager/security/advisories/new).

Include what the issue is, how to reproduce it, and your read on the impact.
Expect an acknowledgement within 7 days.

## Threat model

Loadout is privileged software with a supply chain. Three things make it worth
attacking:

1. **It runs `sudo`.** Anything that smuggles an argument into a package-manager
   invocation is a root-level bug.
2. **It downloads and executes third-party binaries.** The `github` provider
   fetches release archives, unpacks them and puts an executable on your PATH.
3. **It reads catalog data.** The bundled catalog, a downloaded one, and a
   user's own `loadout.yaml` must all be treated as untrusted input.

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

### Downloads

- Release artifacts are checksummed against the release's own checksum file.
- **A missing checksum is a failure, not a warning.** Installing without one
  requires `--allow-unverified`, passed explicitly by the user.
- Archive extraction refuses absolute paths and `..` traversal, and uses tar's
  `data` filter on Python 3.12+.
- The GitHub API is reached over HTTPS only; non-HTTP(S) schemes are refused.
- `loadout audit` reports installed tools whose catalog entry offers no
  verifiable install route.

### Data

- Catalog entries are validated against a schema before use; unknown providers,
  categories and phases are rejected.
- SQL is fully parameterised. The only interpolation is placeholder counts.
- Writes are atomic — a crash cannot leave a half-written catalog or state file.

### Project supply chain

- CI pins GitHub Actions to commit SHAs.
- `pip-audit --strict` runs on every push.
- `ruff --select S` (the bandit ruleset) runs on the package.
- Runtime dependencies are three (`rich`, `requests`, `PyYAML`), none needing a
  compiler.

## Residual risks

These are real and not currently mitigated. Know them before you rely on this:

- **Signature verification is not implemented.** Checksums prove the artifact
  matches what the release published; they do not prove who published it.
  Signature verification (cosign / minisign / GPG) is planned.
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

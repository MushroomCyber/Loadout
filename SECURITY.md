# Security Policy

## Supported Versions

Only the latest `main` branch is supported at this stage of the project.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub
issues.**

Instead, open a private security advisory on the
[GitHub Security tab](https://github.com/MushroomCyber/Kali-Tools-Manager/security/advisories/new),
or email the maintainer listed in `pyproject.toml`.

Please include:

- A description of the vulnerability and its impact.
- Steps to reproduce (ideally a minimal proof-of-concept).
- Your assessment of severity and any suggested mitigation.

You can expect an initial acknowledgement within 7 days.

## Threat Model

`kalitools` is a privileged tool:

1. It runs `sudo apt-get install / remove / update` on the user's behalf,
   which means any issue that lets an attacker smuggle arguments into those
   calls is a root-level bug.
2. It reads catalog metadata from multiple sources (the shipped JSON, the
   user's local overrides, and optionally live scraping of kali.org). Any
   one of those sources must be assumed possibly-hostile.
3. It can launch tool commands in a new terminal, which means executing
   attacker-controlled strings in an interactive shell.

### Mitigations already in place

- All `apt-get` invocations use list-form `subprocess.Popen` — no
  `shell=True` anywhere.
- Package names are validated against `^[a-z0-9][a-z0-9+.\-]*$` before being
  passed to `apt-get`.
- `setup_local_repo` rejects paths containing control characters, stages
  the sources-list file via `tempfile.mkstemp`, and only emits
  `[trusted=yes]` after an explicit user confirmation.
- `launch_tool` parses catalog commands with `shlex`, requires the leading
  token to match a strict allowlist regex, and prompts for confirmation on
  commands containing shell metacharacters.
- All JSON state writes go through an atomic temp-file + `os.replace`
  helper, so a crash cannot corrupt the cache, override, or catalog files.

### Known residual risks

- The user must still trust kali.org's TLS chain when live scraping.
- The user must still trust the contents of their Kali APT sources — this
  tool does not add signature verification, only proxies `apt-get`.
- Scraping now honours `robots.txt` via the `http_util.polite_get` helper
  (introduced in 0.3.0), but the user must still trust remote page content.

Thanks for helping keep the community safe.

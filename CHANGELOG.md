# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-04-16

### Added

- **APT-first catalog builder** — new [kalitools/apt_catalog.py](kalitools/apt_catalog.py):
  builds the tool catalog from `python3-apt` (preferred) or a
  `apt-cache dumpavail` fallback. Categorization uses, in order,
  `kali-tools-*` meta-package membership (`apt-cache depends`),
  debtags (`security::*`, `use::*`, `network::*`, `protocol::*`), and
  keyword hints.
- **`kalitools catalog refresh` / `catalog info`** subcommands to
  regenerate the catalog on demand.
- **SQLite state database** at `~/.local/state/kalitools/state.db` via
  [kalitools/state.py](kalitools/state.py) — stores `installed`,
  `last_used`, `starred`, and an `install`/`uninstall`/`launch`
  history. Survives catalog regeneration so the shipped JSON can be
  kept state-free.
- **Profiles system** — [kalitools/profiles.py](kalitools/profiles.py)
  plus five bundled profiles under
  [kalitools/data/profiles/](kalitools/data/profiles/):
  `pentester-web`, `forensics-starter`, `osint-minimal`, `bug-bounty`,
  `ctf-basics`. User profiles can be dropped into
  `~/.config/kalitools/profiles/`. Access via
  `kalitools profile {list,show,apply}`.
- **Non-interactive CLI** — the historical interactive launcher is
  still the default, but `kalitools` now also accepts subcommands:
  `list`, `search`, `show`, `install`, `remove`, `update`, `upgrade`,
  `catalog {refresh,info}`, `profile {list,show,apply}`,
  `history [--clear]`, `export --format {json,script}`.
  All major subcommands accept `--json` for machine-readable output.
  Destructive commands honour `--yes` and `--dry-run`.
- **Textual TUI** (optional) — [kalitools/tui/app.py](kalitools/tui/app.py).
  Launch with `kalitools --tui` or the new `kalitools-tui` console
  entry point. Requires the `[tui]` extra.
- **Rich theme registry** — [kalitools/theme.py](kalitools/theme.py)
  (`default`, `mono`, `solarized-dark`, `high-contrast`) plus
  `--no-emoji` to strip emoji glyphs for minimal terminals.
- **`ConfigManager.import_tools_list`** now actually installs. Callers
  pass an `installer` callback (or omit it to just parse). Supports
  `--yes` semantics for unattended use.
- **Operation history** — `kalitools history [--package PKG]
  [--limit N]`, plus `--clear`.
- **Test suite** — [tests/](tests/): 33 tests covering atomic I/O,
  catalog schema, security regexes, profile loader, APT
  categorization, the sqlite state DB, config import/export, the
  Rich theme registry, and CLI smoke tests.
- **CI** — [.github/workflows/ci.yml](.github/workflows/ci.yml): matrix
  across Python 3.10 / 3.11 / 3.12; runs `compileall`, `ruff`, and
  `pytest`.
- **Docs** — [docs/PROFILES.md](docs/PROFILES.md),
  [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- **Offline APT routing** — when `KALITOOLS_OFFLINE=1` is set and a local
  repository has been configured via `setup_local_repo`, `apt-get install`
  and `apt-get update` are restricted to the local sources file so they work
  on air-gapped hosts without stalling on unreachable remote mirrors.
- **`robots.txt` compliance** — the `http_util.polite_get` helper now checks
  `robots.txt` before every outbound request and skips disallowed URLs.

### Changed

- **Install / uninstall / launch** paths now record to the sqlite
  state DB in addition to the legacy `~/.kali_tools_cache.json`.
- Interactive help screen now reflects all key bindings (numeric jump,
  `U` updates, `R` re-scan, `Y` utilities, `ESC` exit).
- `pyproject.toml` ships profile JSON as package data and exposes
  `kalitools-tui` as a second console entry point.

### Fixed

- `kalitools` Homepage URL corrected (was a typo
  `egrep-Kali-Tools-Manager`).

## [0.2.0] — 2026-04-16

### Fixed

- **Crash**: missing `datetime` import in `kalitools.manager` made
  `create_backup()` raise `NameError` the first time it was called.
- **Crash on Python 3.10 / 3.11**: an f-string with a backslash in its
  expression prevented the module from even importing. Rewritten to use a
  local variable.
- **Broken sudo check**: `check_sudo_available()` ignored `sudo -n true`'s
  exit status and always returned `True`. It now honours the return code
  and logs whether credentials are cached.
- **Deadlock risk**: `uninstall_tool()` read `stderr` after the full `stdout`
  loop, which could block indefinitely on large error output. Output is now
  merged into `stdout` with a bounded ring buffer.
- **Dead stub**: `fetch_tools_from_web()` printed "success" without doing
  anything. Replaced by a thin wrapper around `discover_from_kali_site`.
- **Landmine**: an orphaned `parse_args()` in `kalitools.ui` referenced an
  `argparse` module that was never imported. Removed.
- **Unreachable**: `except subprocess.TimeoutExpired` branch in
  `install_tool()` — no timeout was ever set. Removed.
- **Non-atomic JSON writes**: every JSON state writer now routes through
  `_atomic_write_json` (temp file + `os.replace`), so Ctrl-C mid-write no
  longer corrupts `~/.kali_tools_cache.json`, overrides, meta hints, user
  settings, or `tools_merged.json`.

### Security

- **`setup_local_repo`** no longer interpolates raw user input into
  `/etc/apt/sources.list.d/local.list`. The path is validated (absolute,
  printable, no control characters, must exist as a directory), staged via
  `tempfile.mkstemp`, and `[trusted=yes]` is only emitted when the caller
  passes `allow_unsigned=True` **and** the user confirms the signature
  warning.
- **`launch_tool`** no longer concatenates catalog-sourced command strings
  into a shell template. The command is parsed with `shlex`, the leading
  token is required to match `[A-Za-z0-9_./-]+`, and any command containing
  shell metacharacters requires an explicit user confirmation before it is
  run.
- **User-Agent** now identifies the project and points at the GitHub repo
  instead of `example.local`, so upstream operators can reach us.
- **Privacy**: the shipped `kalitools/data/tools_merged.json` has been
  scrubbed of per-user `installed` / `size` state.

### Changed

- **Python 3.10+** is now the supported floor (previously claimed 3.8+ but
  did not parse on 3.10/3.11).
- `pyproject.toml` now declares a `kalitools` console entry point,
  packages the shipped JSON catalog via `[tool.setuptools.package-data]`,
  and splits optional dependencies into `notifications`, `disk`, `tui`,
  and `dev` extras.
- `requirements.txt` is retired in favour of `pip install -e '.[...]'`.
- Traceback dumps on fatal error are gated behind `--log-level DEBUG`;
  otherwise a single-line error plus a hint is shown.
- `tools_merged.json` gained a versioned schema wrapper
  (`{schema: 2, generated_at, source, tools: [...]}`). The loader still
  accepts the legacy bare-list shape.

### Removed

- The minimal `Console` shim in `kalitools/__init__.py` was cargo-cult —
  the rest of the package hard-imports `rich.*` anyway. Rich is now a
  declared runtime dependency.
- Dead non-Linux branch in `resolve_ui_mode()`.

### Added

- `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`, this `CHANGELOG.md`.
- `kalitools.manager._atomic_write_json` / `_atomic_write_text` helpers.

## [0.1.0]

Initial internal release.

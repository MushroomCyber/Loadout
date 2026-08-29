# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — unreleased

`kalitools` becomes **Loadout**. The rename ships with the capability
that justifies it: a tool is described once and installed by whichever backend
the machine actually has.

### Added

- **Provider layer** — `apt`, `brew`, `pipx`, `go`, `cargo`, `gem`, `npm`,
  `github` (verified release downloads) and `docker`. Loadout resolves a route
  per tool, per machine, honouring catalog priority, distro restrictions and
  `--prefer`.
- **YAML catalog** — one file per tool under `catalog/`, validated in CI and
  compiled to SQLite with an FTS5 index. Community-editable by pull request and
  versioned independently of the app.
- **`loadout sync`** — converge a machine to a `loadout.yaml` committed in the
  engagement repo. `--prune` also removes what the manifest does not declare.
- **`loadout report`** — signed inventory of the tools and versions used in a
  time window, in text, Markdown or JSON, for pentest reports and DFIR
  chain-of-custody.
- **`loadout audit`** — flags installed tooling that is superseded, has no
  recorded provenance, or installs from an unverified source.
- **`loadout run`** — run a tool, falling back to its container image when it is
  not installed.
- **`loadout alt`** and **`loadout phase`** — alternatives, and browsing by
  kill-chain stage.
- **`loadout export`** now emits `docker`, `ansible` and `loadout` formats
  alongside `json` and `script`.
- **`--json` on every command**, accepted before *or* after the subcommand.
- Blue- and purple-team coverage: `detection`, `incident-response`,
  `monitoring`, `malware`, `cloud` and `threat-intel` categories, plus the
  `dfir-responder`, `detection-engineer`, `cloud-auditor`, `ad-operator` and
  `recon-modern` loadouts.
- Headless tests for the interactive browser, driving the real app through
  Textual's own harness. Covers startup, filter-as-you-type, escape-clears,
  space-to-mark, the detail pane, and a check that every key binding resolves to
  a method that exists -- previously a broken binding surfaced only on keypress.
- Issue templates (including an "add a tool" form for the catalog) and a pull
  request checklist.
- **Mouse control throughout the browser.** An action row in the detail pane
  (Install/Remove, Star, Run, Alternatives), a batch bar that appears once
  something is marked, provider toggles, clickable category chips, and
  Retry/Close on the install modal so a failure has a next step. Every button
  calls the action its key binding already called, so the two cannot drift.
- **Command palette** (`ctrl+p`) with the bundled loadouts in it, matching
  either the name or the slug `loadout apply` takes.
- **`ctrl+r`** runs the highlighted tool, suspending the UI and restoring it
  afterwards.
- An `ansi_shadow` banner, baked in as a constant rather than a runtime
  dependency, with the machine's facts set beside it. Falls back to the
  existing one-line form below 96x30 and switches on resize.
- [TODO.md](TODO.md), tracking what is outstanding.

### Changed

- **The interactive browser is filter-first.** The cursor starts in the query
  box and the list narrows as you type; `space` marks tools and `ctrl+a`
  installs the set. Replaces paging through 31 screens of 25 rows.
- **Four columns, not six** — `status · tool · summary · via`. Summary is the
  column that matters and the old layout did not have one.
- `manager.py` (2,443 lines) is dissolved into `catalog/`, `providers/`,
  `planner`, `executor` and `policy`. Planning is pure and returns data, which
  is what makes the install path testable without a Debian box.
- One search implementation, in the catalog store. There were three, and they
  ranked the same query differently.
- Consecutive apt actions coalesce into a single transaction.
- Six `~/.kali_tools_*` files collapse into the XDG layout.
- Logging defaults to WARNING; a CLI that prints log lines during normal
  operation is unusable in a pipeline.
- Desktop notifications shell out to `notify-send` / `osascript`, dropping the
  unmaintained `notify2` dependency (and with it `dbus-python`). *(The module
  is not yet wired to a caller — see [TODO.md](TODO.md).)*
- **Category chips carry installed-over-total** (`wireless 14/48`) instead of a
  bare count, and use two colours rather than three. The amber "some but not
  many installed" state was unreadable: amber means *something is wrong*
  everywhere else in a security tool, and a partly-stocked category is not a
  warning.
- Provider toggles are offered only for backends that are usable on this
  machine *and* present in the catalog, labelled with their entry count. `npm`
  and `docker` are implemented but no catalog entry uses them, so a toggle for
  either could only ever empty the table.

### Fixed

- **Installs failed as a non-root sudo user** with `E: Write error - write (9:
  Bad file descriptor)` repeated once per status write, and were reported as
  failures even though dpkg had installed the package correctly. APT's progress
  fd was an anonymous pipe passed through `pass_fds`, which did not reliably
  survive `fork`/`exec` through sudo. It now points at the process's own
  stdout: always open, inherited by `exec()`, no bookkeeping and no reader
  thread.
- Selecting a category left no visual trace — only the hardcoded `all` chip was
  ever coloured, so the active filter was invisible.
- An empty result left the previous tool's facts and action row standing over an
  empty table, offering to install something the filter had just excluded. The
  detail pane clears, and the hint names the filters responsible instead of
  reporting a bare "0 tools".
- The marked-set watcher and the detail pane raised `NoMatches` during app
  teardown, when a queued message arrived after the widgets were removed.
- `list --installed` and `export` returned nothing on a fresh machine — silently,
  with exit code 0 — because installed state came from a cache file that started
  out empty. It is now queried live from the providers.
- The catalog was written into the installed package directory, so `pipx
  upgrade` discarded refreshes and read-only prefixes broke them. It now lives
  in `$XDG_DATA_HOME`.
- TUI installs hung when sudo credentials were not cached: the password prompt
  was drawn over by the app. Credentials are now primed before the UI takes the
  terminal, and `DEBIAN_FRONTEND=noninteractive` prevents debconf hangs.
- `search tag:osint` could never match — `Tool` had no `tags` field and the
  dict-shim turned the mistake into a silent `None`. The shim is gone and the
  field is real.
- `launch` and `--help` ran the package name rather than the binary, so
  `metasploit-framework` produced "command not found" instead of `msfconsole`.
  Binaries are now catalog data, resolved from `dpkg -L` during seeding.
- `prune_unknown` exceeded SQLite's 32,766-variable limit on a full APT catalog,
  and the failure was swallowed by a blanket `except`. It now stages ids in a
  temp table.
- Progress percentages were fabricated (`min(95, 5 + lines * 2)`, which pinned
  every install at 95% after 45 lines). They now come from APT's status fd.
- Package names were validated on install but not on removal, and `--` did not
  separate options from names.
- `doctor` ignored deb822 `*.sources` files, so it reported "clean" while not
  reading the real configuration on a modern Debian or Kali host.
- First run fired roughly 800 HTTP requests from a constructor. Nothing touches
  the network at startup.
- A malformed APT status line containing `nan` jumped the progress bar to 100%.
- Archive extraction treated `/etc/passwd` as a relative path on Windows,
  because `Path.is_absolute()` is False there without a drive letter.

Found only by running against real Kali (see `tools/verify_linux.sh`):

- `catalog update` aborted on the first package name containing `+`. 953 names
  on Kali have one (`afl++`, `bonnie++`, `g++`), so the APT catalog build never
  completed at all.
- The APT status-fd options were appended to an argv already ending in
  `-- <package>`, so apt read `-o` and `APT::Status-Fd=7` as packages and exited
  100. Every real install failed; no unit test could see it.
- A failed step reported only `apt-get exited 100`, forcing the user to re-run
  the command by hand to learn why. The last lines of output are now included.
- `--json` was not pure JSON: `catalog update` printed progress notes to stdout
  ahead of the payload, so piping it into a parser failed.
- `dpkg -L` lists paths alphabetically, which made coreutils' primary binary
  `[`. The binary matching the package name is now promoted, stably.
- `loadout report | head` printed "Exception ignored on flushing sys.stdout" --
  the interpreter re-raises BrokenPipeError at shutdown, after every handler.
- `report` listed all 385 installed base-system packages. It now defaults to
  what was actually used in the window, with `--all-installed` for the rest.
- Removing a tool printed "removeed".
- `setuptools-scm` was in the build requires but unused, and its git
  introspection failed on mounted volumes and in containers.

### Security

- `sudo` is constructed in exactly one function, `policy.elevate()`, enforced by
  a test that scans the tree.
- Downloaded artifacts are checksummed against the release's own checksum file.
  **No published checksum is a refusal**, not a warning; `--allow-unverified` is
  an explicit opt-in.
- Archive extraction refuses absolute paths and `..` traversal, and uses the
  `data` filter on Python 3.12+.
- CI runs `ruff --select S`, `pip-audit`, and pins GitHub Actions to commit SHAs.

### Dependencies

Floors raised to the versions the suite is verified against: rich 15.0.0,
requests 2.34.2, textual 8.2.8, setuptools 84.0.0, pytest 9.1.1, pytest-cov
7.1.0, ruff 0.16.4, mypy 2.3.1. GitHub Actions pinned to checkout v7.0.1 and
setup-python v7.0.0, clearing the Node 20 deprecation.

### Removed

- `kalitools_lib.scraping` and the kali.org scraper as a catalog source. APT
  metadata and curated YAML replace it.
- The keyword-substring categoriser, which put 655 of 764 tools in `other` while
  mis-filing much of the rest.
- `notify2`, `beautifulsoup4` and `rapidfuzz` dependencies.
- `requirements.txt` and `run.sh` — `pip install -e '.[dev]'` covers both.
- Every `kalitools` compatibility path: the `kalitools` console script, the
  `KALITOOLS_*` environment variable fallback, the on-disk migration from the
  old `~/.kali_tools_*` layout, and the v1 SQLite schema upgrade. Loadout is a
  full replacement, not an upgrade path — there is nothing to import from a
  previous install.

---

## [0.3.0] — 2026-04

Last release under the `kalitools` name. Rich CLI, Textual TUI, curated
profiles, offline APT support, SQLite state, 33 tests. Superseded by Loadout,
which does not read or migrate any state from this release.

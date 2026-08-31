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
- **Signature verification for downloaded release artifacts.** Where upstream
  signs, a catalog entry pins the key and `gpg`, `minisign` or `cosign` checks
  it. A checksum only proves the download was not corrupted -- the checksum
  file is served by the same account as the artifact, so whoever can replace
  one can replace both. Three properties are enforced and tested against real
  keys: the signature must be by the *pinned* key (a bare `gpg --verify` exit
  code is 0 for a good signature by any key it knows); verification runs in a
  throwaway `GNUPGHOME` so a catalog entry can neither read nor write the
  user's trust store; and a declared signature that fails is not waivable by
  `--allow-unverified`, which exists for projects that publish nothing to
  check against.
- **An `ai-security` category**, with sixteen entries: `garak`, `pyrit`,
  `promptfoo`, `adversarial-robustness-toolbox`, `textattack` and
  `agentic-security` for testing models and agents; `modelscan`, `picklescan`
  and `fickling` for the model files themselves (a pickle is executable code,
  so these are filed under `malware` as well); `llm-guard` for the defensive
  side; `counterfit` for driving ML attacks from a CLI; and `giskard`,
  `rebuff`, `vigil-llm`, `hexstrike-ai` and `dark-moon`, listed without an
  install route because none of them is installable as a command -- some are
  libraries with no console script (pipx refuses those outright), some are
  clone-and-run, and one was never published. Inventing a route for any of them
  would fail on a user's machine. `rebuff` and `vigil-llm` are unmaintained and
  carry `deprecated_by: llm-guard` so `loadout audit` says so. Plus an
  `ai-redteam` loadout of the eight that do install. It is the first category
  where `apt` is the answer for none of the entries, which is what the
  multi-provider design was for.
- **Offline bundles** -- `loadout bundle create` on a connected machine,
  `loadout bundle install` on one with no network at all. Carries apt packages
  with their full dependency closure and verified GitHub release artifacts;
  `inspect` shows what is inside without unpacking and `verify` checks a
  bundle is intact and built for this architecture. The archive is treated as
  untrusted input: every file is checksummed into the manifest and
  re-checksummed before use, member paths are validated before extraction, and
  the build platform is recorded. Providers that cannot travel are reported per
  tool with a reason instead of being silently dropped.
- **`loadout verify`** -- runs each installed tool's catalog `verify:` command
  and reports what actually works. An install that reported success is not the
  same as a tool that runs, and the difference shows up on site. Exits
  non-zero on failure so it can gate a pre-engagement script. Four outcomes
  stay distinct (verified / on PATH / failed / not checkable) rather than
  collapsing into pass-fail, because finding a binary is a weaker claim than
  running one. This also gives the catalog's `verify:` field its first
  consumer -- it had been parsed and stored since the rewrite and never read.
- Issue templates (including an "add a tool" form for the catalog) and a pull
  request checklist.
- **Mouse control throughout the browser.** An action row in the detail pane
  (Install/Remove, Star, Alternatives), a batch bar that appears once
  something is marked, provider toggles, clickable category chips, and
  Retry/Close on the install modal so a failure has a next step. Every button
  calls the action its key binding already called, so the two cannot drift.
- **Command palette** (`ctrl+p`) with the bundled loadouts in it, matching
  either the name or the slug `loadout apply` takes.
- An `ansi_shadow` banner, baked in as a constant rather than a runtime
  dependency, with the machine's facts set beside it. Falls back to the
  existing one-line form below 96x30 and switches on resize.

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
- Desktop notifications are gone, along with the unmaintained `notify2`
  dependency (and with it `dbus-python`) they were built on.
- **Category chips carry installed-over-total** (`wireless 14/48`) instead of a
  bare count, and use two colours rather than three. The amber "some but not
  many installed" state was unreadable: amber means *something is wrong*
  everywhere else in a security tool, and a partly-stocked category is not a
  warning.
- Provider toggles are offered only for backends that are usable on this
  machine *and* present in the catalog, labelled with their entry count. `npm`
  and `docker` are implemented but no catalog entry uses them, so a toggle for
  either could only ever empty the table.

### Added

- **A figlet-style banner on the sudo handover.** The terminal the browser hands
  over for `sudo` to use now opens with the same `LOADOUT` art the app itself
  starts with, not a bare word -- so it still looks like loadout rather than
  some other program asking for a password. Falls back to the plain word below
  96 columns or on a terminal that cannot encode the block glyphs, same as the
  app's own banner.
- **37 tools**, sourced from a diff against hackingtool's own catalog
  (Z4nzu/hackingtool) and vetted before anything was added: mobile and
  reverse-engineering (`androguard`, `frida`, `objection`,
  `mobile-security-framework-mobsf`, `apk2gold`), cloud (`checkov`), AD/Kerberos
  (`kerbrute`, `ldapdomaindump`), recon (`maigret`, `holehe`, `socialscan`,
  `rustscan`, `reconftw`, `sn1per`, `red_hawk`, `rang3r`), web (`xsstrike`,
  `kxss`, `secretfinder`, `xss-loader`), password/hash (`haiti`, `cupp`,
  `hash-buster`, `lazagne`), wireless (`fluxion`, `hcxdumptool`), steganography
  (`zsteg`, `stegseek`), C2 (`mythic`), and several more. Of 88 candidates in
  that list not already catalogued, 4 were dropped for being archived or quiet
  3+ years upstream, and a further dozen -- phishing/OTP credential-stealing
  kits, thin DoS scripts, and entries with sketchy provenance -- were excluded
  on review rather than imported wholesale. Every pipx and gem route was
  confirmed with a real install on Kali and the resulting binaries read back
  from the venv, not inferred from a wheel's declared metadata.

### Fixed

- **`npm install promptfoo` sat for minutes under WSL and then failed.** Under
  WSL the Windows drives are on `PATH`, so `which npm` finds
  `/mnt/c/Program Files/nodejs/npm` — the *Windows* npm — even when Linux has no
  `node` at all. It runs, because that is what interop is for, but every call
  crosses the interop boundary (the wait), it installs into a Windows prefix,
  and the executables it produces are Windows binaries this system cannot run.
  The install appears to work, slowly, and leaves nothing usable behind.

  Toolchain providers now refuse an executable reached through WSL interop, and
  npm additionally requires `node` to exist — `npm --version` answers from its
  shell wrapper with no interpreter present, so npm looks healthy right up until
  the first install fails. `loadout providers` reports it as unavailable with
  the reason, and a plan that has no other route now says why:

      promptfoo: No available installer for 'promptfoo' (catalog offers: npm)
        — npm: only the Windows build is on PATH (/mnt/c/Program Files/nodejs/npm);
          it installs into the Windows filesystem, not this one

  `apt` and `docker` are deliberately exempt: this is a rule about toolchains
  that install into a prefix, not a rule about paths.


- **The sudo handover from the browser was abrupt and unexplained.** The UI
  vanished, one bare line landed on top of whatever the shell had been showing,
  and then `[sudo] password for you:` with nothing connecting it to the button
  just pressed. It now clears the screen, names the tools it is about to change,
  says that root is needed and that nothing has been changed yet, and offers
  Ctrl+C — which is now treated as a cancellation rather than a traceback.
  sudo's own prompt reads `[loadout] password for %p:`, with `%p` expanded by
  sudo rather than a username guessed here.

  The password still goes to sudo and not through loadout: sudo reads it
  straight from `/dev/tty`. That is why this is a terminal handover rather than
  a password box inside the app, and a test now fails if `refresh_credentials`
  ever grows an `input()`, a stdin pipe or a `getpass` call.

- **Three pipx routes pointed at packages that were not what the entry meant.**
  Found by auditing every pipx route in the catalog against the live PyPI index
  rather than by a report: `netexec` has no package on PyPI at all (404);
  `spiderfoot` is a reserved-name placeholder whose own summary reads "Reserved
  name placeholder. No functionality."; `theHarvester` is a single 0.0.1 from
  2019 against a project now on 4.x. All three were inferred from the tool id
  when the catalog was seeded and never checked, and all three stayed hidden
  because apt is tried first on Kali — they would have fired on a machine
  without the Kali repositories. The routes are gone and a test pins the set of
  packages that have been verified, so adding one means checking it.
- **`pipx install modelscan` failed with forty lines of pip output** ending in
  "Ignored the following versions that require a different python version".
  A pipx route can now declare `requires_python`; loadout builds the venv with a
  matching interpreter when the machine has one (`pipx install --python …`), and
  otherwise refuses the route while planning with a sentence naming the gap and
  the release that closes it. On a stock Kali, `loadout install modelscan` now
  says: *needs Python >=3.10,<3.13; this machine has 3.13.12 — install
  python3.12 to use it*, before anything is downloaded.

- **Installing a package that asks a debconf question froze the browser.**
  `wireshark` was the reported case: the download finished, the bar sat at
  100%, and nothing else happened. `DEBIAN_FRONTEND=noninteractive` was being
  set on the `sudo` process, and sudo's `env_reset` discards the environment
  before exec'ing the real command — Debian's `env_keep` does not include it.
  dpkg therefore configured packages with the interactive dialog frontend, and
  wireshark-common's "should non-superusers be able to capture packets?" drew a
  prompt on `/dev/tty` underneath the full-screen UI, waiting for an answer
  that could not be typed. Elevated commands now cross the boundary as
  `sudo env VAR=value …`, which needs no sudoers cooperation, and apt-get
  passes `--force-confdef` alongside `--force-confold` so a changed config file
  is dpkg's decision rather than a second invisible question. (If your sudoers
  whitelists specific commands rather than granting general access, `env` now
  needs to be among them.)
- **Quitting during an install printed a `NoActiveAppError` traceback** over
  the terminal, once per line the package manager had left to say. The install
  modal reached the app through `self.app`, which walks the widget tree and
  fails the moment the screen is detached — and the executor's worker thread
  outlives its screen. It now captures the app at mount and drops updates once
  the UI is gone, which is the correct outcome for a screen nobody is looking
  at.
- **The browser's Run button is gone.** Handing a live terminal to an arbitrary
  tool from inside a full-screen app has to get stdin ownership, suspend and
  resume, and the press-Enter pause all right, and each of those broke in a
  different way. Installing tools is loadout's job; running them is the
  shell's. `loadout run` still exists on the command line, where a terminal is
  not something that has to be borrowed.
- A test replaced `subprocess.run` by assigning to the module attribute rather
  than through `monkeypatch`, so the stub leaked into every test that ran
  afterwards. Nothing later in the suite ran a subprocess, so it stayed
  invisible until one did.
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

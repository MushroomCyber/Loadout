<h1 align="center">Loadout</h1>

<p align="center">
  <b>Pick your kit, install it anywhere, prove what you used.</b><br>
  A security tool manager that isn't tied to one distro or one package manager.
</p>

<p align="center">
  <a href="https://github.com/MushroomCyber/Loadout/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/MushroomCyber/Loadout/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
  <img alt="Platforms" src="https://img.shields.io/badge/linux%20%C2%B7%20macos%20%C2%B7%20wsl-supported-informational?style=flat-square">
  <img alt="842 tools in the catalog" src="https://img.shields.io/badge/catalog-842%20tools-8a63d2?style=flat-square">
</p>

<p align="center">
  <a href="#why-this-instead-of-apt-install">Why</a> ·
  <a href="#install">Install</a> ·
  <a href="#everyday-use">Everyday use</a> ·
  <a href="#interactive-browser">Browser</a> ·
  <a href="#the-catalog">Catalog</a> ·
  <a href="#configuration">Config</a> ·
  <a href="#security">Security</a> ·
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <img alt="The Loadout browser running on Kali: two-tone LOAD/OUT banner, filter box, provider toggles, category sidebar with installed-over-total counts, a tool list with its resolved VIA provider, and the detail pane" src="docs/demo/loadout.png" width="900">
</p>

---

The tools worth having no longer ship from one place. `nuclei`, `subfinder` and
`httpx` come from the Go toolchain; `impacket` and `prowler` from pipx;
`hayabusa` and `velociraptor` from GitHub releases; plenty more from apt or
Homebrew. Loadout describes each tool **once** and installs it with whichever
backend your machine actually has.

```bash
loadout install nuclei          # apt on Kali, brew on macOS, go anywhere else
loadout bundle create -l dfir-responder -o kit.tar   # then install it with no network
loadout sync                    # converge this box to the team's loadout.yaml
loadout report --since 30d      # what you used, and how each download was checked
```

---

## Why this instead of `apt install`

|  | `apt` | Loadout |
|---|---|---|
| Works on Kali, Ubuntu, Arch, macOS, WSL | partly | yes |
| Installs Go / Rust / pipx / release-binary tools | no | yes |
| Verifies downloaded binaries against published checksums | n/a | yes, by default — and shows you it did |
| "Which tools exist for lateral movement?" | no | `loadout phase lateral-movement` |
| "What should I use instead of dirbuster?" | no | `loadout alt dirbuster` |
| Install onto a machine with no internet | no | `loadout bundle` |
| Reproduce this exact toolset on another box | no | `loadout sync` |
| Prove which tool versions an engagement used | no | `loadout report` |

---

## Install

Nothing is published to PyPI yet, so Loadout installs from a checkout. The same
five lines work on Kali, Debian, Ubuntu, Arch, macOS and WSL.

```bash
git clone https://github.com/MushroomCyber/Loadout.git
cd Loadout
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[tui]'
```

```bash
loadout doctor        # is this machine ready?
loadout               # the interactive browser
```

The first launch opens the browser. Type to filter, arrow keys to move, `enter`
to act on the highlighted tool. Everything is also clickable — see
[Interactive browser](#interactive-browser).

> **Prefer pipx?** `pipx install --editable .` from the same checkout.
> (`pipx install loadout` would fetch a published package; there isn't one
> yet.) Add the `dev` extra — `pip install -e '.[dev,tui]'` — only if you
> intend to run the test suite, since it pulls pytest, ruff and mypy.

### Upgrading Loadout

Two different things get upgraded, and they are separate commands on purpose:
**Loadout itself** comes from this repository, and **the tools it installed for
you** come from their own package managers.

To upgrade Loadout, from Loadout:

```bash
loadout self-update --check   # what would change, and where it would come from
loadout self-update           # fast-forward this checkout
```

or press `ctrl+u` in the interactive browser, which does the same thing behind a
screen that names the remote before it offers the button.

It is deliberately narrow. It **refuses** rather than guesses:

| Situation | What happens |
|---|---|
| Uncommitted changes in the checkout | refused — this may be your working copy |
| Local commits the remote doesn't have | refused — resolve it with git, not with a button |
| Anything but a fast-forward | refused — it can never rewrite history you have |
| A dependency change | reported, never auto-installed |

It runs `git` and nothing else: no pip, no hooks, no privilege escalation. It
also merges the exact commit it showed you, not whatever the branch moved to
while you were reading. Restart `loadout` afterwards — the running process
already imported the old code.

The equivalent by hand, which `self-update` is a wrapper around:

```bash
cd Loadout
git pull
source .venv/bin/activate
pip install -e '.[dev,tui]'   # only needed when dependencies changed
loadout --version
loadout catalog info          # confirm the new catalog is the one in use
```

With a pipx-managed checkout, `pipx install --editable . --force` instead of the
`pip install` line.

`git pull` brings a rebuilt catalog with it, so there is nothing to compile.
Run `loadout catalog build` only if you edited `catalog/` yourself.

Your data is untouched by an upgrade: loadouts, history, stars and provenance
live in `$XDG_STATE_HOME` and `$XDG_CONFIG_HOME`, never inside the package.

> **If `loadout catalog info` still shows the old tool count**, you have a
> refreshed catalog in `$XDG_DATA_HOME/loadout/catalog.db` from a previous
> `loadout catalog update`, and it deliberately wins over the one shipped with
> the release — otherwise an upgrade would throw away a refresh. Re-run
> `loadout catalog update` to rebuild it from this machine's package metadata,
> or delete that file to fall back to the catalog you just pulled.

To upgrade the tools themselves:

```bash
loadout update                # refresh package lists, report what is upgradable
loadout upgrade --dry-run     # show exactly what would change
loadout upgrade               # apply it
loadout hold burpsuite        # pin a version an engagement depends on
```

If an upgrade leaves the machine in an odd state, `loadout doctor` names the
problem instead of leaving you to guess.

---

## Everyday use

### Find things

```bash
loadout                          # interactive browser, filter as you type
loadout search fuzz              # full-text search across names and summaries
loadout show ffuf                # everything known, including how to install it here
loadout list --category web      # browse a category
loadout phase lateral-movement   # browse by engagement phase
loadout alt dirbuster            # what people use instead
loadout providers                # which installers work on this machine
```

### Install things

```bash
loadout install ffuf nuclei subfinder
loadout install nuclei --dry-run          # show the plan, change nothing
loadout install nuclei --provider go      # force a backend
loadout install nuclei --prefer brew      # nudge the resolver
loadout remove nikto --yes
loadout run gowitness scan file -f hosts  # run it, in a container if not installed
```

Loadout picks a provider per tool, per machine. `--dry-run` prints exactly what
would run before anything happens:

```
Plan (install):
  nuclei via go
      $ go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
  ffuf via apt
      $ apt-get install -y -o Dpkg::Options::=--force-confold -- ffuf
```

### Loadouts

A loadout is the kit for a job. Bundled ones cover common roles; you can write
your own or capture what you already have.

```bash
loadout loadout list
loadout loadout show ad-operator
loadout loadout apply dfir-responder
loadout loadout save my-kit               # snapshot this machine
```

Commit a `loadout.yaml` to the engagement repo and everyone gets the same box:

```yaml
slug: acme-webapp-2026
name: ACME web app test
tools: [ffuf, nuclei, sqlmap, burpsuite, httpx, katana]
```

```bash
loadout sync                    # install what's missing
loadout sync --prune            # and remove what isn't declared
loadout loadout diff acme-webapp-2026
```

#### Pin it: `loadout.lock`

A manifest lists tool ids and no versions, so applying it in March and again in
September builds two different boxes. `loadout lock` records what those ids
actually resolved to — provider, version, and how the download was checked —
into a `loadout.lock` you commit beside the manifest.

```bash
loadout lock                    # write it from this machine
loadout lock --check            # does this box still match? non-zero if not
loadout sync                    # reports drift against the lock when it exists
```

Drift is reported per tool, in three kinds that are kept apart:

| Kind | Means |
|---|---|
| **version differs** | Names both sides, so you can see which way it moved |
| **different provider** | Same version number, different build. `nmap 7.95` from apt and `nmap 7.95` from a release archive are not the same binary |
| **no version to compare** | The provider could not tell us. Reported, never passed silently |

The lock records and compares. It does not force a version at install time.
Only `go` can express a version pin today, so a lock that claimed to pin
everything would be pinning about a fifth of it. Saying exactly where a box
has diverged is a smaller claim, and one this can actually support.

| Loadout | For |
|---|---|
| `pentester-web` | Web application testing |
| `recon-modern` | The Go-toolchain recon pipeline, no apt needed |
| `ad-operator` | Active Directory assessment and lateral movement |
| `bug-bounty` | Public programme recon and web |
| `dfir-responder` | Host and log forensics |
| `detection-engineer` | Building and validating detections |
| `cloud-auditor` | Cloud and container posture |
| `ai-redteam` | LLM applications, agents, and model-file scanning |
| `forensics-starter`, `osint-minimal`, `ctf-basics` | Starter kits |

### Take a kit somewhere with no network

Incident response happens on isolated segments. Client sites block egress.
Classified work has no route out at all. Build the kit where there *is*
network, carry it in, install it where there is none.

```bash
# on a connected machine
loadout bundle create -l dfir-responder -o kit.tar
loadout bundle inspect kit.tar          # what is in it, and what was left out

# on the isolated one
loadout bundle verify kit.tar           # intact? built for this architecture?
loadout bundle install kit.tar
```

A bundle is a tar holding a `manifest.json` and the artifacts themselves — apt
packages **with their full dependency closure**, verified GitHub release
binaries, Python wheels, and RubyGems. Nothing about installing it touches the
network.

Every route carries its whole dependency closure, which is not what the obvious
command gives you. `gem fetch zsteg` downloads one gem; the install needs
seven, so a bundle built that way fails on the isolated machine looking for
`prime`. Loadout resolves the closure instead and carries all seven.

Python wheels are tied to an interpreter version — `pip download` on a 3.13 box
pulls `cp313` wheels — so the bundle records the Python it was built for and
**refuses on a mismatch** and names both versions. The alternative is an
obscure failure somewhere with no network to fix it from.

The bundle is the one thing here that arrives from outside, onto a machine
picked precisely because it is isolated, so it is treated as untrusted input:

- every file is checksummed into the manifest when built and **re-checksummed
  before use** — otherwise a bundle would be a clean way around all the
  checksum and signature verification everywhere else;
- member paths are validated before extraction, so no `..`, absolute path,
  symlink or device node can write outside the target;
- the build platform is recorded, because a bundle of amd64 debs is not a kit
  on an arm64 box and mid-engagement is the wrong time to find out.

**What cannot travel.** `go` and `cargo` need a build toolchain on the far
side. `npm` is left out for a different reason: the offline path has never been
tested end to end, and shipping it untested would mean it failing on the
isolated machine instead of at build time.

Either way you are told. Each excluded tool is listed with its reason when the
bundle is built. A bundle that quietly held less than it claimed would be
found out on the isolated machine, which is the worst possible place. Where a
tool has both a bundleable and a non-bundleable route, the bundleable one
wins even if the catalog ranks it lower.

### Check the kit before you need it

An install that reported success is not the same as a tool that runs. A Go
binary can land off `PATH`, an apt package can install while its interpreter
does not, a release archive can hold the wrong architecture — all three report
"installed" and all three fail on site.

```bash
loadout verify                  # everything installed
loadout verify nmap ffuf        # just these
loadout verify --quiet          # only what is broken
```

```
✓ ffuf   ffuf version v2.1.0
✓ nmap   Nmap version 7.99 ( https://nmap.org )
✗ nuclei nuclei: not found on PATH
  2 verified · 1 failed
```

It exits non-zero if anything failed, so it works as the last line of a
pre-engagement script. The four outcomes are kept separate, because they are
not equally strong claims:

| Outcome | Means |
|---|---|
| **verified** | The catalog's `verify:` command ran and exited 0 |
| **on PATH** | The binary was found, but never run |
| **failed** | It did not work |
| **not checkable** | The catalog knows neither a command nor a binary name |

Wordlists and payload collections have no command to run, so they are checked
differently. Entries marked `kind: content` — SecLists, the Kali wordlists,
PayloadsAllTheThings — declare `paths:` instead of `binaries:`, and `verify`
checks those paths exist. Before that distinction existed they were hunted for
a binary they were never going to have, and a working 1.8 GB install reported
as failed. `loadout run` on one of them prints where its files are instead of
trying to execute a directory.

### Prove what you used

Pentest reports and DFIR chain-of-custody both need "which tools, which
versions, when". Loadout records that as it works, so the report is evidence
rather than recollection.

```bash
loadout report --since 30d --format markdown -o tooling.md
loadout audit                   # unmaintained, superseded or unverified tooling
loadout history --tool nuclei
```

The report says how each tool's download was checked. Anything that arrived
unchecked gets its own section, along with the `--allow-unverified` that
permitted it. Three outcomes are kept apart:

- a check that passed, named by method (`checksum`, `gpg`);
- `n/a`, where there was no check of ours to run — apt verifies its own
  package signatures;
- a check that ran with nothing to verify against.

Only the third is an unverified install, and it is the one a challenged
finding asks about.

All of it is read out of the install history, not current state, so it
survives a reinstall and still answers for a tool you have since removed.

### Take it elsewhere

```bash
loadout export --format script   > install.sh
loadout export --format docker   > Dockerfile
loadout export --format ansible  > playbook.yml
loadout export --format loadout  > loadout.yaml
```

### Shell completion

```bash
loadout completions bash > ~/.local/share/bash-completion/completions/loadout
loadout completions zsh  > ~/.zfunc/_loadout      # ~/.zfunc must be in $fpath
loadout completions fish > ~/.config/fish/completions/loadout.fish
```

These are generated from the parser that actually runs, so they cover every
subcommand and flag and cannot fall out of step with them. They print to
stdout instead of installing themselves, because where the files belong
differs by shell and by distro — and a tool that edits your shell
configuration uninvited is one you stop trusting.

Tool ids are left out on purpose. There are 842 of them, they change with
every catalog update, and a completion script carrying a stale copy would
confidently offer tools that no longer exist.

### Everything speaks JSON

```bash
loadout list --installed --json | jq -r '.[].id'
loadout install nuclei --dry-run --json | jq '.actions[].steps'
loadout doctor --json | jq '.[] | select(.severity != "ok")'
loadout verify --json | jq -r '.[] | select(.status == "failed") | .tool'
```

---

## Interactive browser

`loadout` with no arguments. The cursor starts in the filter box and the list
narrows as you type.

The screenshot at the top of this page is exactly what you get. The banner
drops to a single line on terminals under 96x30, so a small window spends its
rows on tools instead of on the program's own name.

The whole catalog is browsable with an empty filter — starred first, then
installed, then alphabetical. Typing a query switches to relevance ranking and
shows the top 500 matches. The hint line says so outright, so the rest are
clearly still there.

The detail pane reports what state already knew and never showed: how a tool
was verified, when it was installed, and when it was last run through
`loadout run`. "last run 6mo ago" is what tells you a kit needs pruning.

An install that fails because upstream published nothing to check the download
against offers an **Install unverified** button, which does from the browser
what `--allow-unverified` does from the CLI. It appears only for that one
failure. A checksum *mismatch* never offers it: those bytes are not the ones
that were published, and no button should make them installable. The waiver lasts
exactly one attempt, and the install it produces is recorded as unverified, so
`loadout report` names it later.

### Keys

| Key | Action |
|---|---|
| *type* | Filter immediately |
| `↑` `↓` | Move |
| `space` | Mark a tool |
| `enter` | Install / remove the highlighted tool |
| `ctrl+a` | Install everything marked |
| `ctrl+s` | Star |
| `ctrl+p` | Command palette — fuzzy-find and apply a loadout |
| `ctrl+u` | Update Loadout itself (see [Upgrading Loadout](#upgrading-loadout)) |
| `f5` | Re-read what is installed |
| `esc` | Clear filter, then clear marks, then quit |

### Mouse

Every control calls the same action its key binding does, so nothing behaves
differently depending on how you reached it.

| Control | Where | Does |
|---|---|---|
| **Install** / **Remove** | Detail pane | Acts on the selected tool; the label tracks its state |
| **★ Star** | Detail pane | Toggles the star |
| **Run** | Detail pane | Only shown when the tool has a known binary or a container image |
| **Alternatives** | Detail pane | Only shown when the catalog lists any |
| **Install *n* marked** / **Clear** | Batch bar | Appears once something is marked, and counts it |
| Provider toggles | Under the filter box | Narrow the list to one or more backends |
| Category chips | Left sidebar | Filter by category |
| **Retry** / **Close** | Install modal | A failed install offers a next step, not just red text |

Two of those carry state you can read at a glance:

- **Provider toggles** are only offered for backends that are both usable on
  this machine *and* present in the catalog — listing `npm` on a box where no
  entry names it would be a control whose only outcome is an empty table. An
  active toggle takes the accent colour, bold and reverse.
- **Category chips** show installed-over-total (`wireless 14/48`). The active
  chip takes the accent, bold and reverse -- the only thing a chip's colour
  communicates is which one you clicked.

---

## The catalog

The catalog is **data, not code**: one YAML file per tool under
[`catalog/`](catalog/), reviewed by pull request and compiled into a SQLite
database with a full-text index.

```yaml
# catalog/web/ffuf.yaml
id: ffuf
summary: Fast web fuzzer for content and parameter discovery
categories: [web, fuzzing]
tags: [fuzzing, bug-bounty]
phases: [discovery]
binaries: [ffuf]
homepage: https://github.com/ffuf/ffuf
license: Apache-2.0
verify: ffuf -V
alternatives: [feroxbuster, gobuster, dirsearch]
install:
  - {provider: apt,    package: ffuf, distros: [kali, debian, parrot, ubuntu]}
  - {provider: brew,   formula: ffuf}
  - {provider: go,     module: github.com/ffuf/ffuf/v2@latest}
  - {provider: github, repo: ffuf/ffuf, checksums: "*checksums*.txt"}
```

```bash
loadout catalog validate        # check the source tree
loadout catalog build           # compile it
loadout catalog update          # enrich from local APT metadata
loadout catalog probe-verify    # suggest verify: commands by asking the binaries
loadout catalog info
```

Adding a tool is one file and a pull request. See
[docs/CATALOG.md](docs/CATALOG.md).

Coverage is uneven. **651 of 842** entries name the command they install, and
**48** go further and carry a `verify:` command that proves it runs. For each
tool, `loadout verify` says which of the two it managed, so a weaker check
never reads as a stronger one.

**Providers implemented:** `apt` · `brew` · `pipx` · `go` · `cargo` · `gem` ·
`npm` · `github` (verified release downloads) · `docker`

**Providers with catalog coverage today:** `apt` 774 · `brew` 33 · `pipx` 24 ·
`github` 17 · `go` 12 · `gem` 4 · `cargo` 2 · `npm` 1. `docker` works but no
entry uses it yet — adding one is a pull request, not a code change.

---

## Configuration

| Path | Holds |
|---|---|
| `$XDG_CONFIG_HOME/loadout/loadouts/*.yaml` | Your loadouts |
| `$XDG_STATE_HOME/loadout/state.db` | Installs, history, stars, provenance |
| `$XDG_DATA_HOME/loadout/catalog.db` | The compiled catalog |
| `./loadout.yaml` | Project manifest for `loadout sync` |
| `./loadout.lock` | What those ids resolved to, for `loadout lock --check` |

| Variable | Effect |
|---|---|
| `LOADOUT_OFFLINE=1` | Make no network calls |
| `LOADOUT_NO_EMOJI=1` | ASCII glyphs (auto-detected too) |
| `LOADOUT_THEME` | `default`, `mono`, `solarized-dark`, `high-contrast` |
| `LOADOUT_BIN_DIR` | Where release binaries land (default `~/.local/bin`) |
| `GITHUB_TOKEN` | Raises the GitHub API rate limit |
| `NO_COLOR` / `FORCE_COLOR` | Respected |

---

## Security

Loadout runs `sudo` and downloads binaries, so it holds itself to the bar that
implies:

- `sudo` is constructed in exactly one function, `policy.elevate()` — enforced by a test.
- Every package name is validated before it reaches an argv, and `--` always separates options from names.
- Downloaded artifacts are checksummed against the release's own checksum file. **No checksum means refusal**, not a warning; `--allow-unverified` is an explicit opt-in.
- **A check that passed says so.** The install screen shows the result in the progress block, above the divider and outside the scrolling log. The CLI prints it without needing `--log-level DEBUG`. It is recorded in state, so the detail pane still shows `✓ checksum verified` months later. A batch install collapses all of that to a count. A *skipped* check reads as `unverified`, never as a pass.
- Where upstream signs its releases, the catalog pins the trust anchor and `gpg`, `minisign` or `cosign` checks the signature. **The signer must be the pinned one.** A valid signature by some *other* key is a failure — a bare `gpg --verify` exit code would pass it, so Loadout reads gpg's status output and compares the fingerprint. Verification runs against a throwaway keyring, so a catalog entry can never touch your own.
- **A signature the catalog declares cannot be waived.** `--allow-unverified` is for projects that publish nothing to check against. It is not for a check that ran and failed.
- Sigstore is supported in all three shapes projects publish: a `.sigstore.json` bundle, a detached `.sig` with its `.pem`, and key-based cosign. Keyless pins an OIDC identity instead of a key. Some projects sign from an identity that carries the release tag, so the pin is a pattern, and **an unanchored pattern is refused when the catalog is built**. cosign matches these unanchored, which means `github.com/owner/tool` on its own would also accept `https://evil.example/github.com/owner/tool/...`.
- Archive extraction refuses absolute paths and `..` traversal.
- No `shell=True` anywhere.
- **Your sudo password never passes through loadout.** The browser hands the
  terminal back so `sudo` can read it from `/dev/tty` itself, which is why
  there is no password box in the app — a nicer-looking one would mean the
  password transiting this process. A test fails if the credential path ever
  grows an `input()`, a stdin pipe or a `getpass` call.

See [SECURITY.md](SECURITY.md) for the threat model and how to report an issue.

---

## Contributing

Adding a tool to the catalog is the easiest useful contribution — one YAML file,
no Python. See [docs/CATALOG.md](docs/CATALOG.md) for the format and
[CONTRIBUTING.md](CONTRIBUTING.md) for the workflow. There is an
[issue template](https://github.com/MushroomCyber/Loadout/issues/new?template=add-tool.yml)
if you would rather suggest a tool than write the entry yourself.

```bash
pip install -e '.[dev,tui]'
pytest
ruff check .
mypy loadout
```

Open [issues](https://github.com/MushroomCyber/Loadout/issues) are the place to
look for something to pick up, and the place to propose something that is not
there yet.

### Cutting a release

Pushing a `v*` tag builds an sdist and a wheel, installs the wheel into a clean
interpreter and runs it, then attaches the distributions and the three shell
completion scripts to a GitHub Release.

```bash
git tag v1.0.0 && git push origin v1.0.0
```

The version is written in `pyproject.toml` and in `loadout/__init__.py`, and the
tag is a third claim about it. All three must agree or the release fails before
anything is built: artifacts whose contents disagree with their own filename are
discovered by whoever installs them.

PyPI publishing is wired but switched off. It needs Trusted Publishing
configured on PyPI and the `PUBLISH_TO_PYPI` repository variable set to `true` —
left inert so the wiring can be reviewed before it is ever able to publish.

---

## License

MIT. See [LICENSE](LICENSE).

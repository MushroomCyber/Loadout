<h1 align="center">Loadout</h1>

<p align="center">
  <b>Pick your kit, install it anywhere, prove what you used.</b><br>
  A security tool manager that isn't tied to one distro or one package manager.
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
  <img alt="Platforms" src="https://img.shields.io/badge/linux%20%C2%B7%20macos%20%C2%B7%20wsl-supported-informational?style=flat-square">
</p>

---

The tools worth having no longer ship from one place. `nuclei`, `subfinder` and
`httpx` come from the Go toolchain; `impacket` and `prowler` from pipx;
`hayabusa` and `velociraptor` from GitHub releases; plenty more from apt or
Homebrew. Loadout describes each tool **once** and installs it with whichever
backend your machine actually has.

```bash
loadout install nuclei          # apt on Kali, brew on macOS, go anywhere else
loadout sync                    # converge this box to the team's loadout.yaml
loadout report --since 30d      # signed inventory of what you used, for the report
```

> **Previously Kali Tools Manager.** Your settings, stars and history are
> imported automatically on first run, and the `kalitools` command keeps working
> until 2.0. See [MIGRATION.md](docs/MIGRATION.md).

---

## Why this instead of `apt install`

|  | `apt` | Loadout |
|---|---|---|
| Works on Kali, Ubuntu, Arch, macOS, WSL | partly | yes |
| Installs Go / Rust / pipx / release-binary tools | no | yes |
| Verifies downloaded binaries against published checksums | n/a | yes, by default |
| "Which tools exist for lateral movement?" | no | `loadout phase lateral-movement` |
| "What should I use instead of dirbuster?" | no | `loadout alt dirbuster` |
| Reproduce this exact toolset on another box | no | `loadout sync` |
| Prove which tool versions an engagement used | no | `loadout report` |

---

## Install

From a local checkout of this repository:

```bash
git clone https://github.com/MushroomCyber/Kali-Tools-Manager.git
cd Kali-Tools-Manager
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,tui]'
```

If you want a pipx-managed environment for the local repo checkout instead of a
plain virtualenv:

```bash
pipx install --editable .
```

Then launch the interactive browser:

```bash
loadout
```

Check the machine is ready:

```bash
loadout doctor
```

> `pipx install loadout` is for a published package on PyPI; this repo is a local
> source checkout and is installed from the checkout with `pip install -e .` or
> `pipx install --editable .`.

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

| Loadout | For |
|---|---|
| `pentester-web` | Web application testing |
| `recon-modern` | The Go-toolchain recon pipeline, no apt needed |
| `ad-operator` | Active Directory assessment and lateral movement |
| `bug-bounty` | Public programme recon and web |
| `dfir-responder` | Host and log forensics |
| `detection-engineer` | Building and validating detections |
| `cloud-auditor` | Cloud and container posture |
| `forensics-starter`, `osint-minimal`, `ctf-basics` | Starter kits |

### Prove what you used

Pentest reports and DFIR chain-of-custody both need "which tools, which
versions, when". Loadout records that as it works, so the report is evidence
rather than recollection.

```bash
loadout report --since 30d --format markdown -o tooling.md
loadout audit                   # unmaintained, superseded or unverified tooling
loadout history --tool nuclei
```

### Take it elsewhere

```bash
loadout export --format script   > install.sh
loadout export --format docker   > Dockerfile
loadout export --format ansible  > playbook.yml
loadout export --format loadout  > loadout.yaml
```

### Everything speaks JSON

```bash
loadout list --installed --json | jq -r '.[].id'
loadout install nuclei --dry-run --json | jq '.actions[].steps'
loadout doctor --json | jq '.[] | select(.severity != "ok")'
```

---

## Interactive browser

`loadout` with no arguments. The cursor starts in the filter box and the list
narrows as you type.

| Key | Action |
|---|---|
| *type* | Filter immediately |
| `↑` `↓` | Move |
| `space` | Mark a tool |
| `ctrl+a` | Install everything marked |
| `enter` | Install / remove the highlighted tool |
| `ctrl+s` | Star |
| `esc` | Clear filter, then clear marks, then quit |

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
loadout catalog info
```

Adding a tool is one file and a pull request. See
[docs/CATALOG.md](docs/CATALOG.md).

**Supported providers:** `apt` · `brew` · `pipx` · `go` · `cargo` · `gem` ·
`npm` · `github` (verified release downloads) · `docker`

---

## Configuration

| Path | Holds |
|---|---|
| `$XDG_CONFIG_HOME/loadout/loadouts/*.yaml` | Your loadouts |
| `$XDG_STATE_HOME/loadout/state.db` | Installs, history, stars, provenance |
| `$XDG_DATA_HOME/loadout/catalog.db` | The compiled catalog |
| `./loadout.yaml` | Project manifest for `loadout sync` |

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
- Archive extraction refuses absolute paths and `..` traversal.
- No `shell=True` anywhere.

See [SECURITY.md](SECURITY.md) for the threat model and how to report an issue.

---

## Contributing

Adding a tool to the catalog is the easiest useful contribution — one YAML
file. See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
pip install -e '.[dev,tui]'
pytest
ruff check .
```

---

## License

MIT. See [LICENSE](LICENSE).

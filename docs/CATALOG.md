# The catalog

The catalog is **data, not code**. One YAML file per tool under `catalog/`,
reviewed by pull request, compiled by CI into a SQLite database with a full-text
index and published as a release asset.

That split matters: the app version and the catalog version move independently,
so a new tool reaches users without a release, and a bad catalog entry is a
revert rather than a hotfix.

## Adding a tool

Create `catalog/<category>/<id>.yaml`:

```yaml
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

Then:

```bash
loadout catalog validate --source catalog
loadout catalog build --source catalog
loadout show ffuf
```

Open a pull request. CI validates every entry and fails on any error.

## Fields

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Lowercase, `[a-z0-9][a-z0-9._-]*`. What users type. Stable across distros. |
| `summary` | strongly | One line, under 120 characters. This is the list view's main column. |
| `description` | no | A paragraph for the detail pane. |
| `categories` | yes | One or more from the vocabulary below. First is primary. |
| `tags` | no | Free-form, lowercase. Used by `--tag` and search. |
| `phases` | no | Engagement stages, from the list below. Powers `loadout phase`. |
| `binaries` | strongly | **The executables it installs.** See below — this one matters. |
| `homepage`, `repo`, `license` | no | Shown in `loadout show`. |
| `requires_root` | no | True if the tool itself needs root to run. |
| `verify` | no | Command proving the install works, e.g. `ffuf -V`. |
| `alternatives` | no | Other tool ids. Powers `loadout alt`. |
| `deprecated_by` | no | Tool id that supersedes this. `loadout audit` surfaces it. |
| `install` | yes* | Install routes. Without it the tool is browsable but not installable. |

### `binaries` is not optional in spirit

The package name is often not the command. `metasploit-framework` installs
`msfconsole`; `exploitdb` installs `searchsploit`. The previous release
synthesised the command from the package name and produced "command not found"
for every such tool, so the model now refuses to guess: an entry with no
`binaries` means `loadout run` and version detection cannot work for it.

List the primary command first.

## Install methods

Each entry in `install:` names a `provider` plus its own keys. Order does not
matter; use `priority` (lower wins, default 50) when you want to steer, and
`distros` to restrict a route.

| Provider | Required keys | Optional |
|---|---|---|
| `apt` | `package` | `purge`, `distros` |
| `brew` | `formula` | `cask` |
| `pipx` | `package` | |
| `go` | `module` | `version` |
| `cargo` | `crate` | |
| `gem` | `gem` | |
| `npm` | `package` | |
| `github` | `repo` | `asset`, `checksums`, `tag` |
| `docker` | `image` | `network`, `volumes` |

### GitHub releases and checksums

`checksums:` names the release asset holding the digests — usually a glob like
`"*checksums*.txt"`. **An entry with no `checksums` will refuse to install**
unless the user passes `--allow-unverified`. That is deliberate: this project
downloads and executes third-party binaries, and "we could not verify it" is not
a warning-level event.

If upstream publishes no checksums at all, prefer another provider, or say so in
the pull request so it can be discussed.

## Categories

`recon` `web` `network` `wireless` `exploitation` `post-exploitation`
`password` `vuln-scan` `fuzzing` `database` `social` `mobile` `cloud`
`hardware` `forensics` `incident-response` `detection` `monitoring`
`sniffing` `reverse` `malware` `crypto` `threat-intel` `reporting`
`utility` `other`

The vocabulary is deliberately wider than offence: "all good security tools"
includes the blue and purple team, or the name overpromises.

## Phases

`reconnaissance` `resource-development` `initial-access` `execution`
`persistence` `privilege-escalation` `defense-evasion` `credential-access`
`discovery` `lateral-movement` `collection` `command-and-control`
`exfiltration` `impact` `analysis` `reporting`

Aligned with PTES and the ATT&CK tactics people already plan around, so
`loadout phase lateral-movement` matches how the work is actually scoped.

## Style

- Write the summary for someone who does not know the tool. "Fast web fuzzer for
  content and parameter discovery" beats "ffuf — Fuzz Faster U Fool".
- No marketing. No "powerful", "advanced", "ultimate".
- Prefer the upstream project's own name for `id`, lowercased.
- Fill `alternatives` in both directions when you add a competitor to something
  already in the catalog.
- One tool per file. A file may hold a list of entries, but only do that for
  genuinely inseparable sets.

## Seeding and enrichment

Bulk gaps are filled from the machine's own APT metadata — summaries,
homepages, sizes, and categories derived from Kali meta-package membership and
debtags:

```bash
loadout catalog enrich        # write the findings back into catalog/*.yaml
loadout catalog update        # refresh the compiled catalog in $XDG_DATA_HOME
```

`enrich` edits the YAML source tree and is what a catalog pull request is built
from; `update` refreshes only your own compiled copy. Both need a Debian-family
box with APT present. CI runs `enrich` on a schedule in a
`kalilinux/kali-rolling` container and opens a pull request with the diff.

`loadout catalog update` never overwrites what an entry already states — APT
only supplies what is missing. Curation always wins.

# Legacy command compatibility

Loadout keeps compatibility with legacy `kalitools` commands while you move to the
new provider-based tool model. A tool is described once and installed by whichever
backend your machine has.

Nothing is deleted during migration. If you roll back, the old release still works.

## What happens automatically

The first time you run `loadout`, it imports the kalitools layout:

| From | To |
|---|---|
| `~/.local/state/kalitools/state.db` | `$XDG_STATE_HOME/loadout/state.db` (stars and history preserved) |
| `~/.kali_tools_settings.json` | read into the new config |
| `~/.kali_tools_cache.json` | read for installed state, then superseded by live queries |
| `~/.kali_tools_local_repo.txt` | offline mirror path |
| `~/.kali_tools_overrides.json` | category overrides |

Run it explicitly to see what would happen:

```bash
loadout migrate --dry-run
```

The old files stay where they are. A marker in the state directory stops the
import running twice.

## Command changes

| kalitools | loadout |
|---|---|
| `kalitools` | `loadout` |
| `kalitools --tui` | `loadout` (the browser is the default) |
| `kalitools list` | `loadout list` |
| `kalitools search X` | `loadout search X` |
| `kalitools install X` | `loadout install X` |
| `kalitools profile list` | `loadout loadout list` |
| `kalitools profile apply X` | `loadout loadout apply X` — or `loadout sync X` |
| `kalitools catalog refresh` | `loadout catalog update` |
| `kalitools export --format script` | same, plus `docker`, `ansible`, `loadout` |
| `kalitools history` | `loadout history` |
| `kalitools doctor` | `loadout doctor` |
| `kalitools hold X` | `loadout hold X` |

The `kalitools` command still works. It prints a deprecation notice **to stderr**
— so a script piping JSON on stdout is unaffected — and forwards to `loadout`.
It is removed in 2.0.

## Environment variables

`KALITOOLS_*` still work with a warning. `LOADOUT_*` take precedence.

| Old | New |
|---|---|
| `KALITOOLS_OFFLINE` | `LOADOUT_OFFLINE` |
| `KALITOOLS_NO_EMOJI` | `LOADOUT_NO_EMOJI` |
| `KALITOOLS_THEME` | `LOADOUT_THEME` |
| `KALITOOLS_LOG_FILE` | `LOADOUT_LOG_FILE` |

## Profiles become loadouts

Your `~/.config/kalitools/profiles/*.json` are not read automatically, because
the format changed from JSON to YAML. Converting one is quick — and the loader
still accepts the old `packages:` key, so only the file format really changes:

```yaml
# ~/.config/loadout/loadouts/my-kit.yaml
slug: my-kit
name: My Kit
tools: [nmap, ffuf, sqlmap]
```

Or just capture what you already have:

```bash
loadout loadout save my-kit
```

## Things that behave differently, on purpose

- **`list --installed` now tells the truth.** It queries the package managers
  instead of reading a cache file that started out empty.
- **Progress bars are real.** They come from APT's status file descriptor, not
  from counting output lines.
- **The catalog lives in `$XDG_DATA_HOME`**, not inside the installed package,
  so `pipx upgrade` no longer discards a refreshed catalog.
- **First run makes no network calls.** Catalog refresh is an explicit command.
- **Downloaded binaries are checksummed.** No checksum means refusal unless you
  pass `--allow-unverified`.

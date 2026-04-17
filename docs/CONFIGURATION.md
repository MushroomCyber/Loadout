# Configuration

Kali Tools Manager stores configuration and state in three well-known
locations, all of which obey the XDG Base Directory spec where
applicable.

| Path                                           | Purpose                        |
|------------------------------------------------|--------------------------------|
| `~/.config/kalitools/profiles/*.json`          | User-defined profiles          |
| `~/.local/state/kalitools/state.db`            | Installed state + history (sqlite) |
| `~/.kali_tools_cache.json`                     | Legacy installed cache (migration target) |
| `~/.kali_tools_overrides.json`                 | Category / subcategory overrides |
| `~/.kali_tools_meta_hints.json`                | Cached `apt-cache depends` meta info |
| `~/.kali_tools_local_repo.txt`                 | Offline repo pointer          |

### State database

`state.db` is a plain sqlite file with three tables (`meta`,
`tool_state`, `history`). See [kalitools/state.py](../kalitools/state.py).
The catalog JSON shipped with the package is treated as a static
resource; installed-state lives exclusively in `state.db` so that
catalog regeneration is lossless.

### Environment variables

| Variable              | Purpose                                                    |
|-----------------------|------------------------------------------------------------|
| `KALITOOLS_NO_EMOJI`  | When set, strip emoji glyphs in rendered output            |
| `XDG_STATE_HOME`      | Override the base of the state directory                   |
| `XDG_CONFIG_HOME`     | Override the base of the user-profiles directory           |
| `GITHUB_TOKEN`        | Used by [kalitools_lib/github_metrics.py](../kalitools_lib/github_metrics.py) for higher rate limits |

### Themes

The Rich interface supports several themes (`default`, `mono`,
`solarized-dark`, `high-contrast`). See
[kalitools/theme.py](../kalitools/theme.py). Pass `--no-emoji` to strip
emoji glyphs on terminals that render them poorly.

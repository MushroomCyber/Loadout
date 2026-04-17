# Profiles

Kali Tools Manager ships *profiles* — curated bundles of packages for
common workflows. Apply a profile with a single command:

```bash
kalitools profile list
kalitools profile show pentester-web
kalitools profile apply pentester-web
kalitools profile apply bug-bounty --yes
```

## Bundled profiles

| Slug               | Audience                       | Size  |
|--------------------|--------------------------------|-------|
| `pentester-web`    | Web-application testing        | ~18 pkgs |
| `forensics-starter`| DFIR starter toolkit           | ~15 pkgs |
| `osint-minimal`    | Lightweight OSINT collection   | ~12 pkgs |
| `bug-bounty`       | Public bug-bounty recon/web    | ~17 pkgs |
| `ctf-basics`       | Jeopardy-style CTF starter     | ~15 pkgs |

All bundled profiles live in
[kalitools/data/profiles/](../kalitools/data/profiles/).

## User-defined profiles

Drop any JSON file in `~/.config/kalitools/profiles/` and it will be
picked up automatically. User profiles shadow bundled profiles with the
same `slug`.

Schema:

```json
{
  "slug": "my-profile",
  "name": "Human-readable name",
  "description": "One-line description",
  "tags": ["tag1", "tag2"],
  "packages": ["pkg1", "pkg2", "pkg3"]
}
```

## Behaviour notes

- `apply` installs packages one-by-one via `apt-get install -y`. Each
  install goes through the same hardened path as the interactive UI,
  including sudo verification, disk-space pre-check (when `psutil` is
  available), and history recording.
- Packages that do not exist in the current APT cache are skipped with
  a warning.
- Use `--dry-run` to preview without installing. Use `--yes` for
  unattended automation.

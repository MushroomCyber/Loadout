# Kali Tools Manager

Terminal UI and automation helpers for browsing, installing, and exporting Kali Linux tools. 

## Features
- Rich- or basic-mode terminal interface with keyboard navigation, search, categories, utilities menu, and status widgets.
- Tool discovery via kali.org scraper plus meta-package scans with request throttling and caching.
- Installation/uninstallation helpers with sudo verification, dependency display, disk checks, notifications, and dpkg/apt caching.
- Export/import tooling lists, dpkg backups, local repo configuration, and a utilities menu exposed in both UI modes.
- Modular code layout (`kalitools.manager`, `kalitools.ui`, `kalitools.cli`, etc.) with reusable data model and configuration helpers.

## Requirements
Kali Tools Manager targets **Kali Linux and other Debian-based security distributions**. 

Install runtime dependencies inside your Kali shell:

```bash
python3 -m pip install -r requirements.txt
```

> `notify2` is optional and only needed for desktop notifications on Linux.

## Usage
Launch the TUI from a Kali terminal (rich mode will be used automatically on compatible terminals; basic mode remains available for limited consoles):

```bash
python3 kalitools.py --mode auto
```

Inspect command-line options:

```bash
python3 -m kalitools --help
```

## Utilities Explained

- **dpkg backups** – captures the output of `dpkg --get-selections` into a timestamped text file under your home directory. This snapshot lets you recreate the current package state later (`dpkg --set-selections` + `apt-get dselect-upgrade`). Use it before large upgrade sessions or when migrating to fresh Kali installs.
- **Local repo configuration** – prompts for an absolute path that contains a mirrored Debian repository (for example, a USB drive or LAN share). The manager writes that path to `~/.kali_tools_local_repo.txt` so install/uninstall helpers can temporarily point `apt`/`dpkg` commands at the offline mirror, reducing bandwidth use during live engagements.

## Project Layout
```
kalitools.py                # thin launcher that delegates to kalitools.cli
kalitools/                  # package with modular components
  __init__.py               # shared console/logger + logging config helper
  __main__.py               # enables python -m kalitools
  cli.py                    # argparse wiring + entrypoint
  manager.py                # discovery/install/cache/business logic
  ui.py                     # TUI/basics mode implementation
  config.py / constants.py / model.py / notifications.py  # supporting modules
requirements.txt            # runtime dependencies
README.md                   # this file
```

## Additional Documentation



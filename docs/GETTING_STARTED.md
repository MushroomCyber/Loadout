# Getting Started

## 1. Clone or Download

```bash
git clone https://github.com/MushroomCyber/Kali-Tools-Manager.git
cd Kali-Tools-Manager
```

If you downloaded a ZIP archive, extract it and `cd` into the resulting
directory.

## 2. Install Python 3.10+

Confirm the version:

```bash
python3 --version
```

If the version is older than 3.10, install a current Python build:

```bash
sudo apt install python3 python3-venv python3-pip pipx
```

## 3. Install Kali Tools Manager

The fastest path puts `kalitools` on your `$PATH` via `pipx`:

```bash
pipx install .
kalitools --help
```

For development, use an editable venv with the optional extras:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e '.[notifications,disk,dev]'
```

Optional extras:

| extra           | purpose                                         |
| --------------- | ----------------------------------------------- |
| `notifications` | desktop toasts via `notify2`                    |
| `disk`          | free-disk-space pre-check via `psutil`          |
| `tui`           | full-screen Textual UI (Phase 5)                |
| `dev`           | pytest + ruff for contributors                  |

## 4. Launch the Application

Rich mode is used automatically on capable terminals; basic mode is used as a
fallback.

```bash
kalitools --mode auto
```

or from a source checkout without installing:

```bash
python3 -m kalitools --help
python3 kalitools.py            # legacy launcher
```

## 5. Next Steps

- Review [`README.md`](../README.md) for the feature summary.
- Browse [`kalitools/ui.py`](../kalitools/ui.py) for the interactive key
  bindings.
- See [`SECURITY.md`](../SECURITY.md) before enabling the local-repo feature.
- Explore the in-app **Utilities** menu (`Y` key) for:
  - **dpkg backups** – writes `dpkg --get-selections` to a timestamped file
    so you can restore package selections later.
  - **Local repo configuration** – records the path to an offline / air-
    gapped repository mirror so installs can source packages without
    Internet access. The path is validated and written atomically.

```bash
kalitools --mode auto
```

or from a source checkout without installing:

```bash
python3 -m kalitools --help
python3 kalitools.py            # legacy launcher
```

## 5. Next Steps

- Review [`README.md`](../README.md) for the feature summary.
- Browse [`kalitools/ui.py`](../kalitools/ui.py) for the interactive key
  bindings.
- See [`SECURITY.md`](../SECURITY.md) before enabling the local-repo feature.
- Explore the in-app **Utilities** menu (`Y` key) for:
  - **dpkg backups** – writes `dpkg --get-selections` to a timestamped file
    so you can restore package selections later.
  - **Local repo configuration** – records the path to an offline / air-
    gapped repository mirror so installs can source packages without
    Internet access. The path is validated and written atomically.


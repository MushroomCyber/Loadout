"""XDG-compliant path resolution and one-shot migration from the legacy layout.

Loadout keeps exactly three things on disk:

* ``$XDG_CONFIG_HOME/loadout/config.toml`` -- user settings.
* ``$XDG_STATE_HOME/loadout/state.db``     -- installs, history, stars, provenance.
* ``$XDG_DATA_HOME/loadout/catalog.db``    -- the compiled tool catalog.

Everything else (the six ``~/.kali_tools_*`` files the previous release
scattered across ``$HOME``) is folded into those on first run. See
:func:`migrate_legacy_state`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "loadout"

#: Files written by kalitools <= 0.3.x that we absorb and then leave alone.
LEGACY_FILES = {
    "cache": ".kali_tools_cache.json",
    "local_repo": ".kali_tools_local_repo.txt",
    "overrides": ".kali_tools_overrides.json",
    "meta_hints": ".kali_tools_meta_hints.json",
    "settings": ".kali_tools_settings.json",
}

_MIGRATION_MARKER = "migrated-from-kalitools"


def _xdg(var: str, fallback: str | Path) -> Path:
    raw = os.environ.get(var)
    base = Path(raw) if raw else Path.home() / fallback
    return base / APP_NAME


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", Path(".local") / "state")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", Path(".local") / "share")


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")


def config_file() -> Path:
    return config_dir() / "config.toml"


def state_db() -> Path:
    return state_dir() / "state.db"


def catalog_db() -> Path:
    """User-writable catalog location.

    The bundled read-only catalog lives inside the installed package; refreshed
    catalogs are written here so ``pipx upgrade`` can never discard them and a
    read-only install prefix can never block a refresh.
    """
    return data_dir() / "catalog.db"


def bundled_catalog() -> Path:
    """Read-only catalog shipped inside the wheel."""
    return Path(__file__).resolve().parent / "data" / "catalog.db"


def user_loadouts_dir() -> Path:
    return config_dir() / "loadouts"


def ensure_dirs() -> None:
    for path in (config_dir(), state_dir(), data_dir(), cache_dir(), user_loadouts_dir()):
        path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------


@dataclass
class MigrationReport:
    ran: bool = False
    settings: dict[str, object] | None = None
    local_repo: str | None = None
    overrides: dict[str, dict[str, str]] | None = None
    installed: list[str] | None = None
    moved_state_db: bool = False

    @property
    def anything_found(self) -> bool:
        return bool(
            self.settings or self.local_repo or self.overrides
            or self.installed or self.moved_state_db
        )


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def migration_marker() -> Path:
    return state_dir() / _MIGRATION_MARKER


def needs_migration() -> bool:
    if migration_marker().exists():
        return False
    home = Path.home()
    if any((home / name).exists() for name in LEGACY_FILES.values()):
        return True
    return (home / ".local" / "state" / "kalitools" / "state.db").exists()


def migrate_legacy_state(*, dry_run: bool = False) -> MigrationReport:
    """Absorb the kalitools layout into the loadout one. Safe to call twice.

    Legacy files are read, never deleted -- if the user rolls back to the old
    release it still works. A marker file makes this a no-op on later runs.
    """
    report = MigrationReport()
    if migration_marker().exists():
        return report

    home = Path.home()
    ensure_dirs()

    settings = _read_json(home / LEGACY_FILES["settings"])
    if isinstance(settings, dict):
        report.settings = settings

    repo_file = home / LEGACY_FILES["local_repo"]
    if repo_file.exists():
        try:
            text = repo_file.read_text(encoding="utf-8").strip()
            if text:
                report.local_repo = text
        except OSError:
            pass

    overrides = _read_json(home / LEGACY_FILES["overrides"])
    if isinstance(overrides, dict):
        report.overrides = {
            str(k): v for k, v in overrides.items() if isinstance(v, dict)
        }

    cache = _read_json(home / LEGACY_FILES["cache"])
    if isinstance(cache, dict):
        report.installed = sorted(k for k, v in cache.items() if v)

    legacy_db = home / ".local" / "state" / "kalitools" / "state.db"
    target_db = state_db()
    if legacy_db.exists() and not target_db.exists():
        if not dry_run:
            try:
                target_db.parent.mkdir(parents=True, exist_ok=True)
                target_db.write_bytes(legacy_db.read_bytes())
                report.moved_state_db = True
            except OSError:
                report.moved_state_db = False
        else:
            report.moved_state_db = True

    report.ran = True
    if not dry_run:
        migration_marker().write_text(
            "kalitools state absorbed; legacy files left in place\n", encoding="utf-8"
        )
    return report

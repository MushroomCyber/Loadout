"""XDG-compliant path resolution.

Loadout keeps exactly three things on disk:

* ``$XDG_CONFIG_HOME/loadout/config.toml`` -- user settings.
* ``$XDG_STATE_HOME/loadout/state.db``     -- installs, history, stars, provenance.
* ``$XDG_DATA_HOME/loadout/catalog.db``    -- the compiled tool catalog.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "loadout"


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

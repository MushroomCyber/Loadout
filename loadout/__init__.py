"""Loadout -- pick your kit, install it anywhere, prove what you used.

Formerly Kali Tools Manager. The rename came with the provider layer: a tool is
now described once and installed by whichever backend the machine actually has
(apt, brew, pipx, go, cargo, a verified GitHub release, a container).

Nothing heavy is imported here. The previous release pulled the whole manager --
and with it rich, requests and beautifulsoup4 -- into every ``import kalitools``,
including ``--help``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

__version__ = "1.0.0.dev0"

__all__ = [
    "__version__",
    "configure_logging",
    "env_flag",
    "get_console",
    "logger",
]

logger = logging.getLogger("loadout")

#: Legacy variables still honoured, with a warning, for one major version.
_LEGACY_ENV = {
    "LOADOUT_OFFLINE": "KALITOOLS_OFFLINE",
    "LOADOUT_NO_EMOJI": "KALITOOLS_NO_EMOJI",
    "LOADOUT_THEME": "KALITOOLS_THEME",
    "LOADOUT_LOG_FILE": "KALITOOLS_LOG_FILE",
}

_warned_legacy: set[str] = set()


def env(name: str, default: str = "") -> str:
    """Read a ``LOADOUT_*`` variable, falling back to its ``KALITOOLS_*`` twin."""
    value = os.environ.get(name)
    if value is not None:
        return value
    legacy_name = _LEGACY_ENV.get(name)
    if legacy_name:
        legacy_value = os.environ.get(legacy_name)
        if legacy_value is not None:
            if legacy_name not in _warned_legacy:
                _warned_legacy.add(legacy_name)
                logger.warning(
                    "%s is deprecated; use %s instead", legacy_name, name
                )
            return legacy_value
    return default


def env_flag(name: str) -> bool:
    return env(name).strip().lower() not in ("", "0", "false", "no")


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------

_console = None


def get_console(*, force_new: bool = False):
    """The shared Rich console, created on first use.

    Honours ``NO_COLOR`` and ``FORCE_COLOR`` -- both conventions this audience
    relies on in CI.
    """
    global _console
    if _console is not None and not force_new:
        return _console

    from rich.console import Console

    from .theme import get_theme

    no_color = bool(os.environ.get("NO_COLOR"))
    force_color = bool(os.environ.get("FORCE_COLOR"))
    kwargs: dict[str, object] = {"theme": get_theme(env("LOADOUT_THEME", "default"))}
    if no_color:
        kwargs["no_color"] = True
    if force_color:
        kwargs["force_terminal"] = True

    _console = Console(**kwargs)  # type: ignore[arg-type]
    return _console


def _reconfigure_stdout() -> None:
    """Ask for UTF-8 output where the platform allows it.

    Python 3.7+ can retarget the encoding of an existing text stream. On a
    Windows console defaulting to cp1252 this turns a hard UnicodeEncodeError
    mid-render into correct output; where it is not possible the renderer falls
    back to ASCII glyphs instead.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def configure_console(*, theme: str = "default", no_emoji: bool = False):
    """Rebuild the console with an explicit theme once the CLI has parsed flags."""
    global _console
    from rich.console import Console

    from .theme import get_theme

    _reconfigure_stdout()
    kwargs: dict[str, object] = {"theme": get_theme(theme)}
    if os.environ.get("NO_COLOR"):
        kwargs["no_color"] = True
    if os.environ.get("FORCE_COLOR"):
        kwargs["force_terminal"] = True
    _console = Console(**kwargs)  # type: ignore[arg-type]
    if no_emoji:
        os.environ["LOADOUT_NO_EMOJI"] = "1"
    return _console


def use_emoji() -> bool:
    return not env_flag("LOADOUT_NO_EMOJI")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def configure_logging(level: str = "WARNING", *, log_file: str | None = None) -> None:
    """Configure package logging. Idempotent.

    Defaults to WARNING, not INFO: a CLI whose normal operation prints log lines
    to stderr is unusable in a pipeline.
    """
    root = logging.getLogger("loadout")
    root.setLevel(level.upper())
    formatter = logging.Formatter("%(levelname)-7s %(name)s: %(message)s")

    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    ):
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)

    target = log_file or env("LOADOUT_LOG_FILE") or None
    if target:
        path = Path(target).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        already = any(
            isinstance(h, logging.FileHandler)
            and Path(h.baseFilename).resolve() == path.resolve()
            for h in root.handlers
        )
        if not already:
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
            )
            root.addHandler(file_handler)

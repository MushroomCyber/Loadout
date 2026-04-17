"""Kali Tools Manager package."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from rich.console import Console

from .theme import get_theme, strip_emojis

__all__ = [
    "console",
    "logger",
    "configure_logging",
    "configure_console",
    "NO_EMOJI",
]

__version__ = "0.3.0"

# Runtime-toggled by `configure_console`. Defaults follow env for library users
# that import the package before the CLI parses flags.
NO_EMOJI: bool = bool(os.environ.get("KALITOOLS_NO_EMOJI"))


class _EmojiAwareConsole(Console):
    """Rich ``Console`` subclass that strips emoji glyphs when requested."""

    def __init__(self, *args, no_emoji: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._no_emoji = no_emoji

    def print(self, *objects, **kwargs):  # type: ignore[override]
        if self._no_emoji and objects:
            objects = tuple(
                strip_emojis(o) if isinstance(o, str) else o for o in objects
            )
        return super().print(*objects, **kwargs)


# The console singleton is re-bound by `configure_console` when the CLI parses
# `--theme` / `--no-emoji`. All modules still get a live reference because they
# `from . import console` (module attribute lookup is done at print time).
console: Console = _EmojiAwareConsole(
    theme=get_theme(os.environ.get("KALITOOLS_THEME", "default")),
    no_emoji=NO_EMOJI,
)
logger = logging.getLogger("kalitools")


def configure_logging(level: str = "INFO", *, log_file: str | None = None) -> None:
    """Configure package-wide logging (idempotent).

    Args:
        level: stdlib logging level name (``DEBUG``/``INFO``/...).
        log_file: optional path to append log records to. The directory is
            created if missing.
    """
    lvl = level.upper()
    root_logger = logging.getLogger()
    root_logger.setLevel(lvl)

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    has_stream = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root_logger.handlers
    )
    if not has_stream:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root_logger.addHandler(stream)

    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        already = any(
            isinstance(h, logging.FileHandler)
            and Path(h.baseFilename).resolve() == path.resolve()
            for h in root_logger.handlers
        )
        if not already:
            fh = logging.FileHandler(path, encoding="utf-8")
            fh.setFormatter(fmt)
            root_logger.addHandler(fh)


def configure_console(*, theme: str = "default", no_emoji: bool = False) -> Console:
    """Update the package-wide ``console`` in-place.

    Mutates the existing singleton so modules that did ``from . import
    console`` before CLI parsing still observe the new theme / emoji policy.
    """
    global NO_EMOJI
    NO_EMOJI = bool(no_emoji or os.environ.get("KALITOOLS_NO_EMOJI"))
    console._no_emoji = NO_EMOJI  # type: ignore[attr-defined]
    try:
        console.push_theme(get_theme(theme))
    except Exception:  # pragma: no cover
        pass
    return console


try:  # Re-export for callers/tests that expect package-level access
    from .manager import KaliToolsManager  # noqa: E402

    __all__.append("KaliToolsManager")
except Exception:  # pragma: no cover - avoid failing during partial installs
    KaliToolsManager = None  # type: ignore

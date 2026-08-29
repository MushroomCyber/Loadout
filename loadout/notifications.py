"""Desktop notifications with no third-party dependency.

The previous release depended on ``notify2``, which has had no release since
2017 and pulls in ``dbus-python`` -- a package that needs a compiler and system
headers and frequently fails to build. Shelling out to the desktop's own
notifier removes the dependency entirely and works on more machines.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess

logger = logging.getLogger("loadout.notifications")

_APP_NAME = "Loadout"


def available() -> bool:
    return _notifier() is not None


def _notifier() -> list[str] | None:
    """Return the argv prefix for this desktop, or None."""
    system = platform.system()
    if system == "Linux":
        if shutil.which("notify-send"):
            return ["notify-send", "--app-name", _APP_NAME]
        if shutil.which("kdialog"):
            return ["kdialog", "--title", _APP_NAME, "--passivepopup"]
        return None
    if system == "Darwin" and shutil.which("osascript"):
        return ["osascript", "-e"]
    return None


def notify(title: str, message: str, *, urgency: str = "normal") -> bool:
    """Best-effort desktop notification. Never raises, never blocks for long."""
    prefix = _notifier()
    if prefix is None:
        return False

    try:
        if prefix[0] == "notify-send":
            argv = [*prefix, "--urgency", urgency, "--", title, message]
        elif prefix[0] == "kdialog":
            argv = [*prefix, f"{title}: {message}", "5"]
        else:  # osascript
            safe_title = title.replace('"', "'")
            safe_message = message.replace('"', "'")
            argv = [
                *prefix,
                f'display notification "{safe_message}" with title "{safe_title}"',
            ]
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, capture_output=True, timeout=5, check=False
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("notification failed: %s", exc)
        return False

"""Desktop notification helpers (optional dependency)."""

from __future__ import annotations

from . import logger

try:
    import notify2  # type: ignore
    NOTIFICATIONS_AVAILABLE = True
    _NOTIFY_INITIALIZED = False
except ImportError:  # pragma: no cover - optional dependency
    notify2 = None  # type: ignore
    NOTIFICATIONS_AVAILABLE = False
    _NOTIFY_INITIALIZED = False


def init_notifications_app() -> None:
    """Initialise the desktop notification backend if available."""
    global _NOTIFY_INITIALIZED
    if not NOTIFICATIONS_AVAILABLE or _NOTIFY_INITIALIZED:
        return
    try:  # pragma: no cover - depends on desktop stack
        notify2.init("Kali Tools Manager")
        _NOTIFY_INITIALIZED = True
        logger.debug("notify2 initialised successfully")
    except Exception as exc:
        logger.warning("Could not initialise notify2: %s", exc)
        _NOTIFY_INITIALIZED = False


def notifications_ready() -> bool:
    """Return True if desktop notifications can be sent."""
    init_notifications_app()
    return NOTIFICATIONS_AVAILABLE and _NOTIFY_INITIALIZED


def send_notification(title: str, message: str) -> None:
    """Best-effort desktop notification."""
    if not notifications_ready():
        return
    try:  # pragma: no cover - UI nicety
        notify = notify2.Notification(title, message)
        notify.show()
    except Exception as exc:
        logger.debug("notify2 failed to show notification: %s", exc)

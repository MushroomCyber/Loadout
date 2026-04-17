"""Thin wrapper around :mod:`kalitools.state` for operation history."""

from __future__ import annotations

from typing import Any

from .state import get_state_db


def record_install(package: str, success: bool, detail: str = "") -> None:
    get_state_db().record("install", package, success=success, detail=detail)


def record_uninstall(package: str, success: bool, detail: str = "") -> None:
    get_state_db().record("uninstall", package, success=success, detail=detail)


def record_launch(package: str, command: str = "") -> None:
    db = get_state_db()
    db.mark_used(package)
    db.record("launch", package, success=True, detail=command)


def recent(limit: int = 50, package: str | None = None) -> list[dict[str, Any]]:
    return get_state_db().history(package=package, limit=limit)


def clear(before: str | None = None) -> int:
    return get_state_db().clear_history(before=before)

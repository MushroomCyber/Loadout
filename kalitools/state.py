"""Runtime state for Kali Tools Manager.

The catalog ([kalitools/data/tools_merged.json](data/tools_merged.json)) is
treated as a static, regenerable asset. Everything that is user- or
host-specific lives in a sqlite database under
``~/.local/state/kalitools/state.db`` so that:

* Catalog regeneration (Phase 3.5) never clobbers user state.
* History, stars, and last-used timestamps survive upgrades.
* Multiple installs on one host share a single source of truth.

The schema is intentionally minimal; callers should not depend on the
internal representation. All writes are executed inside short
transactions and all reads return plain Python dicts / lists.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import logger

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS tool_state (
    name        TEXT PRIMARY KEY,
    installed   INTEGER NOT NULL DEFAULT 0,
    last_used   TEXT,
    starred     INTEGER NOT NULL DEFAULT 0,
    user_notes  TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    action     TEXT NOT NULL,
    package    TEXT NOT NULL,
    success    INTEGER NOT NULL DEFAULT 1,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS ix_history_package ON history(package);
CREATE INDEX IF NOT EXISTS ix_history_ts      ON history(ts);
"""

_CURRENT_SCHEMA_VERSION = 1


def default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "kalitools"


def default_db_path() -> Path:
    return default_state_dir() / "state.db"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateDB:
    """Thin wrapper around sqlite3 for user state."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ----- connection helpers -------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        try:
            with self._tx() as conn:
                conn.executescript(_SCHEMA)
                conn.execute(
                    "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(_CURRENT_SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:  # pragma: no cover
            logger.warning("state db init failed: %s", exc)

    # ----- tool state ---------------------------------------------------------
    def set_installed(self, name: str, installed: bool) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO tool_state(name, installed)
                   VALUES(?, ?)
                   ON CONFLICT(name) DO UPDATE SET installed=excluded.installed""",
                (name, int(bool(installed))),
            )

    def mark_used(self, name: str) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO tool_state(name, last_used)
                   VALUES(?, ?)
                   ON CONFLICT(name) DO UPDATE SET last_used=excluded.last_used""",
                (name, _utcnow()),
            )

    def set_starred(self, name: str, starred: bool) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO tool_state(name, starred)
                   VALUES(?, ?)
                   ON CONFLICT(name) DO UPDATE SET starred=excluded.starred""",
                (name, int(bool(starred))),
            )

    def get_state(self, name: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT name, installed, last_used, starred, user_notes FROM tool_state WHERE name=?",
                (name,),
            ).fetchone()
        return dict(row) if row else None

    def all_state(self) -> dict[str, dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT name, installed, last_used, starred, user_notes FROM tool_state"
            ).fetchall()
        return {row["name"]: dict(row) for row in rows}

    # ----- history ------------------------------------------------------------
    def record(self, action: str, package: str, success: bool = True, detail: str = "") -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO history(ts, action, package, success, detail) VALUES(?,?,?,?,?)",
                (_utcnow(), action, package, int(bool(success)), detail or ""),
            )

    def history(
        self,
        *,
        package: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT id, ts, action, package, success, detail FROM history"
        params: list[Any] = []
        if package:
            sql += " WHERE package=?"
            params.append(package)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._tx() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def clear_history(self, *, before: str | None = None) -> int:
        with self._tx() as conn:
            if before:
                cur = conn.execute("DELETE FROM history WHERE ts < ?", (before,))
            else:
                cur = conn.execute("DELETE FROM history")
            return cur.rowcount or 0

    # ----- bulk ops -----------------------------------------------------------
    def bulk_set_installed(self, installed_names: Iterable[str]) -> None:
        names = [(n,) for n in installed_names]
        with self._tx() as conn:
            conn.execute("UPDATE tool_state SET installed=0")
            conn.executemany(
                """INSERT INTO tool_state(name, installed) VALUES(?, 1)
                   ON CONFLICT(name) DO UPDATE SET installed=1""",
                names,
            )

    def prune_unknown(self, known_names: Iterable[str]) -> int:
        """Delete rows whose package name is not in *known_names*.

        Call after a catalog refresh so orphaned state (for packages that
        no longer exist in the catalog) doesn't accumulate. Returns the
        number of rows deleted.
        """
        known = set(known_names)
        if not known:
            return 0
        placeholders = ",".join("?" * len(known))
        with self._tx() as conn:
            cur = conn.execute(
                f"DELETE FROM tool_state WHERE name NOT IN ({placeholders}) "
                "AND starred=0 AND (user_notes IS NULL OR user_notes='')",
                tuple(known),
            )
            return cur.rowcount or 0

    def star_list(self) -> list[str]:
        """Return the names of starred tools (alphabetical)."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT name FROM tool_state WHERE starred=1 ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]


_singleton: StateDB | None = None


def get_state_db() -> StateDB:
    """Return the process-wide state database singleton."""
    global _singleton
    if _singleton is None:
        _singleton = StateDB()
    return _singleton

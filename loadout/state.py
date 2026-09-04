"""Per-machine state: what is installed, what happened, what you starred.

The catalog is a regenerable asset; everything user- or host-specific lives
here so a catalog rebuild can never clobber it.

``tool_state`` records which *provider* installed a tool and *what version*,
which is what makes ``loadout report`` able to state exactly what was used
during an engagement. :meth:`StateDB.prune_unknown` stages known ids in a temp
table rather than binding one SQL variable per id, so it stays correct past
SQLite's per-statement variable limit on a large catalog.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("loadout.state")

SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS tool_state (
    tool_id       TEXT PRIMARY KEY,
    installed     INTEGER NOT NULL DEFAULT 0,
    provider      TEXT NOT NULL DEFAULT '',
    version       TEXT NOT NULL DEFAULT '',
    last_used     TEXT,
    starred       INTEGER NOT NULL DEFAULT 0,
    notes         TEXT,
    verify_method TEXT NOT NULL DEFAULT '',
    verify_ok     INTEGER NOT NULL DEFAULT 0,
    installed_at  TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    action   TEXT NOT NULL,
    tool_id  TEXT NOT NULL,
    success  INTEGER NOT NULL DEFAULT 1,
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS ix_history_tool ON history(tool_id);
CREATE INDEX IF NOT EXISTS ix_history_ts   ON history(ts);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateDB:
    def __init__(self, path: Path | None = None) -> None:
        from .paths import state_db

        self.path = Path(path) if path else state_db()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # -- plumbing ----------------------------------------------------------

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
        with self._tx() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns a pre-existing state.db predates.

        This file holds a machine's install history, stars and notes --
        `CREATE TABLE IF NOT EXISTS` leaves an existing table's columns
        alone, so a schema bump has to reach it with `ALTER TABLE` instead
        of a rebuild, the way the regenerable catalog.db can afford to.
        """
        existing = {row[1] for row in conn.execute("PRAGMA table_info(tool_state)")}
        if "verify_method" not in existing:
            conn.execute("ALTER TABLE tool_state ADD COLUMN verify_method TEXT NOT NULL DEFAULT ''")
        if "verify_ok" not in existing:
            conn.execute("ALTER TABLE tool_state ADD COLUMN verify_ok INTEGER NOT NULL DEFAULT 0")
        if "installed_at" not in existing:
            conn.execute("ALTER TABLE tool_state ADD COLUMN installed_at TEXT")
            # Backfilled rather than left blank: history already holds when
            # each install happened, and a machine that has been managed for
            # a year should not read as though every tool arrived today.
            conn.execute(
                """UPDATE tool_state SET installed_at = (
                       SELECT MAX(ts) FROM history
                       WHERE history.tool_id = tool_state.tool_id
                         AND history.action = 'install'
                         AND history.success = 1
                   )"""
            )

    # -- tool state --------------------------------------------------------

    def set_installed(
        self,
        tool_id: str,
        installed: bool,
        *,
        provider: str = "",
        version: str = "",
        verify_method: str = "",
        verify_ok: bool = False,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO tool_state(tool_id, installed, provider, version,
                                           verify_method, verify_ok, installed_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(tool_id) DO UPDATE SET
                       installed     = excluded.installed,
                       provider      = CASE WHEN excluded.provider != ''
                                        THEN excluded.provider ELSE tool_state.provider END,
                       version       = CASE WHEN excluded.version != ''
                                        THEN excluded.version ELSE tool_state.version END,
                       verify_method = excluded.verify_method,
                       verify_ok     = excluded.verify_ok,
                       -- A removal must not stamp a fresh install date on a
                       -- row it is about to mark uninstalled.
                       installed_at  = CASE WHEN excluded.installed = 1
                                        THEN excluded.installed_at
                                        ELSE tool_state.installed_at END""",
                (
                    tool_id,
                    int(bool(installed)),
                    provider,
                    version,
                    verify_method,
                    int(bool(verify_ok)),
                    _utcnow() if installed else None,
                ),
            )

    def mark_used(self, tool_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO tool_state(tool_id, last_used) VALUES(?,?)
                   ON CONFLICT(tool_id) DO UPDATE SET last_used=excluded.last_used""",
                (tool_id, _utcnow()),
            )

    def set_starred(self, tool_id: str, starred: bool) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO tool_state(tool_id, starred) VALUES(?,?)
                   ON CONFLICT(tool_id) DO UPDATE SET starred=excluded.starred""",
                (tool_id, int(bool(starred))),
            )

    def get(self, tool_id: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM tool_state WHERE tool_id=?", (tool_id,)
            ).fetchone()
        return dict(row) if row else None

    def all_state(self) -> dict[str, dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute("SELECT * FROM tool_state").fetchall()
        return {row["tool_id"]: dict(row) for row in rows}

    def installed_ids(self) -> set[str]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT tool_id FROM tool_state WHERE installed=1"
            ).fetchall()
        return {row["tool_id"] for row in rows}

    def starred_ids(self) -> list[str]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT tool_id FROM tool_state WHERE starred=1 ORDER BY tool_id"
            ).fetchall()
        return [row["tool_id"] for row in rows]

    def sync_installed(self, installed: Iterable[str], *, provider: str = "") -> int:
        """Reconcile state with what a provider actually reports. Returns changes."""
        names = sorted({n for n in installed if n})
        with self._tx() as conn:
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS _sync(tool_id TEXT PRIMARY KEY)")
            conn.execute("DELETE FROM _sync")
            conn.executemany("INSERT OR IGNORE INTO _sync VALUES (?)", [(n,) for n in names])
            where = "WHERE provider = ?" if provider else ""
            args = (provider,) if provider else ()
            cleared = conn.execute(
                f"UPDATE tool_state SET installed=0 "  # noqa: S608 - fixed fragment
                f"WHERE installed=1 AND tool_id NOT IN (SELECT tool_id FROM _sync) "
                f"{'AND provider = ?' if provider else ''}",
                args,
            ).rowcount or 0
            marked = conn.execute(
                """INSERT INTO tool_state(tool_id, installed, provider)
                   SELECT tool_id, 1, ? FROM _sync WHERE true
                   ON CONFLICT(tool_id) DO UPDATE SET installed=1""",
                (provider,),
            ).rowcount or 0
            conn.execute("DROP TABLE IF EXISTS _sync")
            _ = where
        return cleared + marked

    def prune_unknown(self, known_ids: Iterable[str]) -> int:
        """Drop rows for tools no longer in the catalog, keeping anything the
        user invested in (stars, notes).

        Staged through a temp table: binding one variable per id blew SQLite's
        32,766-variable limit as soon as the catalog covered the full APT set.
        """
        known = sorted({k for k in known_ids if k})
        if not known:
            return 0
        with self._tx() as conn:
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS _known(tool_id TEXT PRIMARY KEY)")
            conn.execute("DELETE FROM _known")
            conn.executemany("INSERT OR IGNORE INTO _known VALUES (?)", [(k,) for k in known])
            cursor = conn.execute(
                """DELETE FROM tool_state
                   WHERE tool_id NOT IN (SELECT tool_id FROM _known)
                     AND starred = 0
                     AND (notes IS NULL OR notes = '')"""
            )
            removed = cursor.rowcount or 0
            conn.execute("DROP TABLE IF EXISTS _known")
        return removed

    # -- history -----------------------------------------------------------

    def record(
        self, action: str, tool_id: str, *, success: bool = True, detail: str = ""
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO history(ts, action, tool_id, success, detail) VALUES(?,?,?,?,?)",
                (_utcnow(), action, tool_id, int(bool(success)), detail or ""),
            )

    def history(
        self,
        *,
        tool_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        actions: Iterable[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT id, ts, action, tool_id, success, detail FROM history"
        clauses: list[str] = []
        params: list[Any] = []
        if tool_id:
            clauses.append("tool_id = ?")
            params.append(tool_id)
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        if until:
            clauses.append("ts <= ?")
            params.append(until)
        action_list = [a for a in (actions or []) if a]
        if action_list:
            clauses.append(f"action IN ({','.join('?' * len(action_list))})")
            params.extend(action_list)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC"
        if limit and limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._tx() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def clear_history(self, *, before: str | None = None) -> int:
        with self._tx() as conn:
            if before:
                cursor = conn.execute("DELETE FROM history WHERE ts < ?", (before,))
            else:
                cursor = conn.execute("DELETE FROM history")
            return cursor.rowcount or 0


_singleton: StateDB | None = None


def get_state_db(path: Path | None = None) -> StateDB:
    global _singleton
    if _singleton is None or path is not None:
        _singleton = StateDB(path)
    return _singleton


def reset_state_db() -> None:
    """Drop the singleton -- used by tests."""
    global _singleton
    _singleton = None

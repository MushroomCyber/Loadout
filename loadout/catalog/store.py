"""The compiled catalog: SQLite with an FTS5 index.

Replaces the previous 284 KB ``tools_merged.json`` that was parsed in full on
every invocation. The store is opened read-only, queried with real filters, and
shipped as a build artifact so it can be versioned independently of the app.

One search implementation lives here. The CLI, the TUI and any future front-end
all call :meth:`CatalogStore.search` -- the previous release had three separate
implementations that ranked the same query differently.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ..errors import CatalogError, CatalogMissing
from ..model import Tool

logger = logging.getLogger("loadout.catalog")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS tools (
    id               TEXT PRIMARY KEY,
    summary          TEXT NOT NULL DEFAULT '',
    primary_category TEXT NOT NULL DEFAULT 'other',
    doc              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facets (
    tool_id TEXT NOT NULL,
    kind    TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (tool_id, kind, value)
);
CREATE INDEX IF NOT EXISTS ix_facets_lookup ON facets(kind, value);
CREATE INDEX IF NOT EXISTS ix_tools_category ON tools(primary_category);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS tools_fts
USING fts5(tool_id UNINDEXED, blob, tokenize='unicode61 remove_diacritics 2');
"""

#: Facet kinds that :meth:`CatalogStore.search` knows how to filter on.
FACET_KINDS = ("category", "tag", "phase", "provider", "binary")


@dataclass(frozen=True)
class CatalogInfo:
    schema: int
    generated_at: str
    source: str
    tool_count: int
    revision: str = ""


def fts5_available(conn: sqlite3.Connection | None = None) -> bool:
    own = conn is None
    conn = conn or sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
        conn.execute("DROP TABLE _probe")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        if own:
            conn.close()


class CatalogStore:
    """Read access to a compiled catalog."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise CatalogMissing()
        self._conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row
        self._has_fts = self._detect_fts()

    def _detect_fts(self) -> bool:
        try:
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name='tools_fts'"
            ).fetchone()
            return row is not None
        except sqlite3.DatabaseError:
            return False

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CatalogStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- metadata ----------------------------------------------------------

    def info(self) -> CatalogInfo:
        meta = {
            row["key"]: row["value"]
            for row in self._conn.execute("SELECT key, value FROM meta")
        }
        return CatalogInfo(
            schema=int(meta.get("schema_version", 0)),
            generated_at=meta.get("generated_at", "unknown"),
            source=meta.get("source", "unknown"),
            revision=meta.get("revision", ""),
            tool_count=self.count(),
        )

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0])

    # -- lookup ------------------------------------------------------------

    def get(self, tool_id: str) -> Tool | None:
        row = self._conn.execute(
            "SELECT doc FROM tools WHERE id = ?", (tool_id.strip().lower(),)
        ).fetchone()
        if row is None:
            return None
        return Tool.from_dict(json.loads(row["doc"]))

    def get_many(self, tool_ids: Sequence[str]) -> list[Tool]:
        """Fetch several tools without one query per id, and without blowing
        SQLite's variable limit on a large request."""
        wanted = [t.strip().lower() for t in tool_ids if t and t.strip()]
        if not wanted:
            return []
        found: dict[str, Tool] = {}
        for chunk in _chunked(wanted, 500):
            placeholders = ",".join("?" * len(chunk))
            for row in self._conn.execute(
                f"SELECT id, doc FROM tools WHERE id IN ({placeholders})", chunk
            ):
                found[row["id"]] = Tool.from_dict(json.loads(row["doc"]))
        return [found[t] for t in wanted if t in found]

    def iter_all(self) -> Iterator[Tool]:
        for row in self._conn.execute("SELECT doc FROM tools ORDER BY id"):
            yield Tool.from_dict(json.loads(row["doc"]))

    def ids(self) -> list[str]:
        return [r["id"] for r in self._conn.execute("SELECT id FROM tools ORDER BY id")]

    # -- facets ------------------------------------------------------------

    def facet_values(self, kind: str, *, with_counts: bool = False) -> list[tuple[str, int]]:
        if kind not in FACET_KINDS:
            raise CatalogError(f"unknown facet kind: {kind!r}")
        rows = self._conn.execute(
            "SELECT value, COUNT(*) AS n FROM facets WHERE kind = ? "
            "GROUP BY value ORDER BY n DESC, value ASC",
            (kind,),
        ).fetchall()
        if with_counts:
            return [(r["value"], r["n"]) for r in rows]
        return [(r["value"], r["n"]) for r in rows]

    # -- search ------------------------------------------------------------

    def search(
        self,
        query: str = "",
        *,
        categories: Sequence[str] = (),
        tags: Sequence[str] = (),
        phases: Sequence[str] = (),
        providers: Sequence[str] = (),
        limit: int = 0,
    ) -> list[Tool]:
        """Full-text search with facet filters, ranked by FTS relevance.

        An empty *query* returns everything matching the filters, ordered by id.
        """
        clauses: list[str] = []
        params: list[object] = []

        for kind, values in (
            ("category", categories),
            ("tag", tags),
            ("phase", phases),
            ("provider", providers),
        ):
            cleaned = [str(v).strip().lower() for v in values if str(v).strip()]
            if not cleaned:
                continue
            placeholders = ",".join("?" * len(cleaned))
            # Only the placeholder count is interpolated; every value below
            # is bound. Same for the fragments in _like_search.
            clauses.append(
                f"t.id IN (SELECT tool_id FROM facets WHERE kind = ? "
                f"AND value IN ({placeholders}))"
            )
            params.append(kind)
            params.extend(cleaned)

        query = (query or "").strip()
        if query and self._has_fts:
            match = _to_fts_query(query)
            sql = (
                "SELECT t.doc AS doc FROM tools_fts f "
                "JOIN tools t ON t.id = f.tool_id "
                "WHERE tools_fts MATCH ? "
            )
            head_params: list[object] = [match]
            if clauses:
                sql += " AND " + " AND ".join(clauses)
                head_params.extend(params)
            sql += " ORDER BY bm25(tools_fts, 1.0, 4.0), t.id"
            try:
                rows = self._conn.execute(sql, head_params).fetchall()
            except sqlite3.OperationalError as exc:
                logger.debug("FTS query failed (%s); falling back to LIKE", exc)
                rows = self._like_search(query, clauses, params)
        elif query:
            rows = self._like_search(query, clauses, params)
        else:
            sql = "SELECT doc FROM tools t"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY t.id"
            rows = self._conn.execute(sql, params).fetchall()

        tools = [Tool.from_dict(json.loads(r["doc"])) for r in rows]
        if limit and limit > 0:
            tools = tools[:limit]
        return tools

    def _like_search(
        self, query: str, clauses: list[str], params: list[object]
    ) -> list[sqlite3.Row]:
        like = f"%{query.lower()}%"
        sql = (
            "SELECT t.doc AS doc FROM tools t "
            "WHERE (LOWER(t.id) LIKE ? OR LOWER(t.summary) LIKE ?)"
        )
        args: list[object] = [like, like]
        if clauses:
            sql += " AND " + " AND ".join(clauses)
            args.extend(params)
        # Exact-prefix hits first, then everything else.
        sql += " ORDER BY CASE WHEN LOWER(t.id) = ? THEN 0 "
        sql += "WHEN LOWER(t.id) LIKE ? THEN 1 ELSE 2 END, t.id"
        args.extend([query.lower(), f"{query.lower()}%"])
        return self._conn.execute(sql, args).fetchall()

    def suggest(self, tool_id: str, limit: int = 5) -> list[str]:
        """Nearest ids for a typo, used by the "did you mean" error path."""
        needle = tool_id.strip().lower()
        if not needle:
            return []
        rows = self._conn.execute(
            "SELECT id FROM tools WHERE id LIKE ? ORDER BY LENGTH(id), id LIMIT ?",
            (f"%{needle[:12]}%", limit),
        ).fetchall()
        if rows:
            return [r["id"] for r in rows]
        prefix = needle[: max(2, len(needle) // 2)]
        return [
            r["id"]
            for r in self._conn.execute(
                "SELECT id FROM tools WHERE id LIKE ? ORDER BY id LIMIT ?",
                (f"{prefix}%", limit),
            )
        ]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def build_catalog(
    path: Path,
    tools: Iterable[Tool],
    *,
    source: str = "unknown",
    generated_at: str = "",
    revision: str = "",
) -> int:
    """Compile *tools* into a fresh catalog at *path*. Returns the tool count.

    Written to a temporary sibling then moved into place, so a failed build can
    never leave a half-written catalog where a working one used to be.
    """
    from datetime import datetime, timezone

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".building")
    if tmp.exists():
        tmp.unlink()

    generated_at = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    count = 0

    with closing(sqlite3.connect(tmp)) as conn:
        conn.executescript(_SCHEMA)
        use_fts = fts5_available(conn)
        if use_fts:
            conn.executescript(_FTS_SCHEMA)
        else:
            logger.warning("SQLite built without FTS5; search will use LIKE matching")

        rows: list[tuple[str, str, str, str]] = []
        facet_rows: list[tuple[str, str, str]] = []
        fts_rows: list[tuple[str, str]] = []

        for tool in tools:
            doc = json.dumps(tool.to_dict(), separators=(",", ":"), sort_keys=True)
            rows.append((tool.id, tool.summary, tool.category, doc))
            for value in tool.categories:
                facet_rows.append((tool.id, "category", value))
            for value in tool.tags:
                facet_rows.append((tool.id, "tag", value))
            for value in tool.phases:
                facet_rows.append((tool.id, "phase", value))
            for value in tool.providers:
                facet_rows.append((tool.id, "provider", value))
            for value in tool.binaries:
                facet_rows.append((tool.id, "binary", value.lower()))
            fts_rows.append((tool.id, tool.search_blob()))
            count += 1

        conn.executemany("INSERT OR REPLACE INTO tools VALUES (?,?,?,?)", rows)
        conn.executemany("INSERT OR IGNORE INTO facets VALUES (?,?,?)", facet_rows)
        if use_fts:
            conn.executemany("INSERT INTO tools_fts (tool_id, blob) VALUES (?,?)", fts_rows)

        conn.executemany(
            "INSERT OR REPLACE INTO meta VALUES (?,?)",
            [
                ("schema_version", str(SCHEMA_VERSION)),
                ("generated_at", generated_at),
                ("source", source),
                ("revision", revision),
                ("fts", "5" if use_fts else "none"),
            ],
        )
        conn.commit()
        conn.execute("VACUUM")

    tmp.replace(path)
    logger.info("built catalog with %d tools at %s", count, path)
    return count


def open_catalog(explicit: Path | None = None) -> CatalogStore:
    """Open the user's catalog, falling back to the one bundled in the wheel.

    Resolution order matters: a catalog the user refreshed must win over the
    stale one shipped with the release.
    """
    from ..paths import bundled_catalog, catalog_db

    candidates = [explicit] if explicit else [catalog_db(), bundled_catalog()]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return CatalogStore(Path(candidate))
    raise CatalogMissing()


def _to_fts_query(raw: str) -> str:
    """Turn user input into a safe FTS5 MATCH expression.

    Every term is quoted, so FTS operators a user types (``AND``, ``*``, ``"``)
    are treated as literal text rather than syntax that could error out.
    """
    terms = [t for t in raw.replace('"', " ").split() if t]
    if not terms:
        return '""'
    return " AND ".join(f'"{t}"*' for t in terms)


def _chunked(items: Sequence[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])

"""state.db migration and the verification columns it carries.

state.db is a machine's install history, stars and notes -- unlike the
regenerable catalog.db it can never just be rebuilt on a schema bump, so a
new column has to reach an existing file via ALTER TABLE.
"""

from __future__ import annotations

import sqlite3

from loadout.state import StateDB


def _make_v2_db(path) -> None:
    """A state.db as it looked before the verify_method/verify_ok columns."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """CREATE TABLE tool_state (
                tool_id    TEXT PRIMARY KEY,
                installed  INTEGER NOT NULL DEFAULT 0,
                provider   TEXT NOT NULL DEFAULT '',
                version    TEXT NOT NULL DEFAULT '',
                last_used  TEXT,
                starred    INTEGER NOT NULL DEFAULT 0,
                notes      TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO tool_state(tool_id, installed, starred, notes) "
            "VALUES ('nmap', 1, 1, 'engagement notes')"
        )
        conn.commit()
    finally:
        conn.close()


class TestMigration:
    def test_an_old_state_db_gains_the_verify_columns_without_losing_data(self, tmp_path):
        path = tmp_path / "state.db"
        _make_v2_db(path)

        db = StateDB(path)

        row = db.get("nmap")
        assert row is not None
        assert row["installed"] == 1
        assert row["starred"] == 1
        assert row["notes"] == "engagement notes"
        assert row["verify_method"] == ""
        assert row["verify_ok"] == 0

    def test_migration_is_idempotent(self, tmp_path):
        path = tmp_path / "state.db"
        _make_v2_db(path)
        StateDB(path)
        # Reopening an already-migrated db must not raise "duplicate column".
        db = StateDB(path)
        assert db.get("nmap") is not None


class TestVerifyColumns:
    def test_set_installed_persists_verification(self, tmp_path):
        db = StateDB(tmp_path / "state.db")
        db.set_installed(
            "velociraptor", True, provider="github", verify_method="signature", verify_ok=True
        )
        row = db.get("velociraptor")
        assert row["verify_method"] == "signature"
        assert row["verify_ok"] == 1

    def test_set_installed_without_verification_defaults_blank(self, tmp_path):
        db = StateDB(tmp_path / "state.db")
        db.set_installed("nmap", True, provider="apt")
        row = db.get("nmap")
        assert row["verify_method"] == ""
        assert row["verify_ok"] == 0

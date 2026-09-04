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
            "velociraptor", True, provider="github", verification=("signature", True)
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


class TestVerificationSurvivesReconciliation:
    """`ctx.installed()` rewrites every row from provider inventory, and runs
    on nearly every command. It knows what is installed and nothing about how
    it was checked, so it must leave the verification columns alone -- when it
    did not, a `loadout list` erased what the install had just recorded and
    the detail pane went blank within seconds of a verified install.
    """

    def test_a_write_with_no_verification_leaves_the_record_standing(self, tmp_path):
        from loadout.state import StateDB

        db = StateDB(tmp_path / "state.db")
        db.set_installed(
            "velociraptor", True, provider="github", verification=("gpg", True)
        )
        db.set_installed("velociraptor", True, provider="github")

        row = db.get("velociraptor")
        assert row is not None
        assert row["verify_method"] == "gpg"
        assert row["verify_ok"] == 1

    def test_a_reinstall_that_checked_nothing_clears_the_old_result(self, tmp_path):
        """The other half of the same distinction: apt has no verification
        step of ours, and a tool reinstalled through it is no longer the
        binary a signature vouched for."""
        from loadout.state import StateDB

        db = StateDB(tmp_path / "state.db")
        db.set_installed(
            "velociraptor", True, provider="github", verification=("gpg", True)
        )
        db.set_installed("velociraptor", True, provider="apt", verification=("", False))

        row = db.get("velociraptor")
        assert row is not None
        assert row["verify_method"] == ""

    def test_reconciliation_does_not_restamp_the_install_date(self, tmp_path):
        """"installed today" for every tool on the box is what restamping on
        every command looks like from the detail pane."""
        import time

        from loadout.state import StateDB

        db = StateDB(tmp_path / "state.db")
        db.set_installed("nmap", True, provider="apt", verification=("", False))
        first = db.get("nmap")["installed_at"]
        assert first

        time.sleep(1.1)  # the column has second resolution
        db.set_installed("nmap", True, provider="apt")
        assert db.get("nmap")["installed_at"] == first

        db.set_installed("nmap", False)
        assert db.get("nmap")["installed_at"] == first

    def test_installing_again_after_a_removal_does_restamp(self, tmp_path):
        import time

        from loadout.state import StateDB

        db = StateDB(tmp_path / "state.db")
        db.set_installed("nmap", True, provider="apt", verification=("", False))
        first = db.get("nmap")["installed_at"]

        db.set_installed("nmap", False, verification=("", False))
        time.sleep(1.1)
        db.set_installed("nmap", True, provider="apt", verification=("", False))

        assert db.get("nmap")["installed_at"] != first

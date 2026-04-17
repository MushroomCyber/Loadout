import pytest

from kalitools.state import StateDB


@pytest.fixture
def db(tmp_path):
    return StateDB(path=tmp_path / "state.db")


def test_set_installed_round_trip(db):
    db.set_installed("nmap", True)
    row = db.get_state("nmap")
    assert row is not None
    assert row["installed"] == 1


def test_history_record_and_query(db):
    db.record("install", "nmap", success=True, detail="ok")
    db.record("uninstall", "nmap", success=False, detail="held")
    rows = db.history(limit=10)
    assert len(rows) == 2
    assert {r["action"] for r in rows} == {"install", "uninstall"}
    assert db.history(package="nmap", limit=10) == rows


def test_clear_history(db):
    for i in range(3):
        db.record("install", f"pkg{i}")
    assert db.clear_history() == 3
    assert db.history() == []


def test_bulk_set_installed_resets_old_state(db):
    db.set_installed("nmap", True)
    db.set_installed("hydra", True)
    db.bulk_set_installed(["nmap"])
    assert db.get_state("nmap")["installed"] == 1
    assert db.get_state("hydra")["installed"] == 0


def test_starred_and_used(db):
    db.set_starred("nmap", True)
    db.mark_used("nmap")
    row = db.get_state("nmap")
    assert row["starred"] == 1
    assert row["last_used"] is not None

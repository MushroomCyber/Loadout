"""loadout.selfupdate against real git repos in tmp_path.

Real `git` rather than mocked subprocess calls: this module's entire job is
getting the fast-forward/dirty/diverged distinction right, and a mock that
returns "success" for the wrong git invocation would hide exactly the class
of bug that matters here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loadout import selfupdate

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0,
    reason="git is not available",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=None,
    )
    assert result.returncode == 0, result.stderr
    return result


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--quiet", "--initial-branch=main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "pyproject.toml").write_text("[project]\nname = 'loadout'\n", encoding="utf-8")
    (path / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "--quiet", "-m", "initial")
    return path


@pytest.fixture
def remote(tmp_path) -> Path:
    return _init_repo(tmp_path / "remote")


@pytest.fixture
def local(tmp_path, remote) -> Path:
    dest = tmp_path / "local"
    subprocess.run(
        ["git", "clone", "--quiet", str(remote), str(dest)], check=True, capture_output=True
    )
    _git(dest, "config", "user.email", "test@example.com")
    _git(dest, "config", "user.name", "Test")
    return dest


class TestFindRepoRoot:
    def test_finds_the_checkout_containing_pyproject_and_git(self, local):
        nested = local / "sub" / "dir"
        nested.mkdir(parents=True)
        assert selfupdate.find_repo_root(nested) == local

    def test_returns_none_outside_any_checkout(self, tmp_path):
        bare = tmp_path / "not_a_checkout"
        bare.mkdir()
        assert selfupdate.find_repo_root(bare) is None


class TestCheckUpdate:
    def test_reports_up_to_date_right_after_clone(self, local):
        status = selfupdate.check_update(local)
        assert not status.error
        assert status.up_to_date
        assert status.behind == 0
        assert status.ahead == 0
        assert not status.dirty

    def test_detects_being_behind(self, remote, local):
        (remote / "code.py").write_text("x = 2\n", encoding="utf-8")
        _git(remote, "commit", "-am", "bump")

        status = selfupdate.check_update(local)
        assert status.behind == 1
        assert status.ahead == 0
        assert status.can_update

    def test_detects_a_dirty_working_tree(self, local):
        (local / "code.py").write_text("uncommitted\n", encoding="utf-8")
        status = selfupdate.check_update(local)
        assert status.dirty
        assert not status.can_update

    def test_detects_local_commits_the_remote_lacks(self, local):
        (local / "code.py").write_text("local only\n", encoding="utf-8")
        _git(local, "commit", "-am", "local work")
        status = selfupdate.check_update(local)
        assert status.ahead == 1
        assert not status.can_update

    def test_no_remote_is_reported_as_an_error_not_a_crash(self, tmp_path):
        solo = _init_repo(tmp_path / "solo")
        status = selfupdate.check_update(solo)
        assert status.error
        assert not status.up_to_date


class TestApplyUpdate:
    def test_fast_forwards_a_clean_behind_checkout(self, remote, local):
        (remote / "code.py").write_text("x = 2\n", encoding="utf-8")
        _git(remote, "commit", "-am", "bump")

        before = selfupdate.check_update(local)
        result = selfupdate.apply_update(local, before)

        assert result.ok
        assert result.old_commit == before.current_commit
        assert result.new_commit == before.remote_commit
        assert (local / "code.py").read_text(encoding="utf-8") == "x = 2\n"

    def test_flags_when_pyproject_toml_changed(self, remote, local):
        (remote / "pyproject.toml").write_text(
            "[project]\nname = 'loadout'\nversion = '2'\n", encoding="utf-8"
        )
        _git(remote, "commit", "-am", "bump deps")

        result = selfupdate.apply_update(local)
        assert result.ok
        assert result.deps_changed

    def test_a_code_only_change_does_not_flag_deps(self, remote, local):
        (remote / "code.py").write_text("x = 2\n", encoding="utf-8")
        _git(remote, "commit", "-am", "bump")

        result = selfupdate.apply_update(local)
        assert result.ok
        assert not result.deps_changed

    def test_refuses_a_dirty_checkout_without_touching_it(self, remote, local):
        (remote / "code.py").write_text("x = 2\n", encoding="utf-8")
        _git(remote, "commit", "-am", "bump")
        (local / "code.py").write_text("my uncommitted work\n", encoding="utf-8")

        result = selfupdate.apply_update(local)

        assert not result.ok
        assert "overwritten" in result.error
        assert (local / "code.py").read_text(encoding="utf-8") == "my uncommitted work\n"

    def test_refuses_a_diverged_checkout(self, remote, local):
        (remote / "code.py").write_text("remote change\n", encoding="utf-8")
        _git(remote, "commit", "-am", "remote work")
        (local / "code.py").write_text("local change\n", encoding="utf-8")
        _git(local, "commit", "-am", "local work")

        result = selfupdate.apply_update(local)

        assert not result.ok
        assert "resolve with git" in result.error

    def test_already_up_to_date_is_a_no_op_success(self, local):
        result = selfupdate.apply_update(local)
        assert result.ok
        assert result.old_commit == result.new_commit

    def test_merges_the_sha_it_was_shown_even_if_remote_moved_since(self, remote, local):
        """apply_update takes a previously-fetched UpdateStatus and must land
        on exactly that commit -- not silently re-fetch and pull in commits
        the caller never confirmed."""
        (remote / "code.py").write_text("x = 2\n", encoding="utf-8")
        _git(remote, "commit", "-am", "first bump")
        status = selfupdate.check_update(local)
        confirmed_commit = status.remote_commit

        (remote / "code.py").write_text("x = 3\n", encoding="utf-8")
        _git(remote, "commit", "-am", "second bump, after confirmation")

        result = selfupdate.apply_update(local, status)
        assert result.ok
        assert result.new_commit == confirmed_commit
        assert (local / "code.py").read_text(encoding="utf-8") == "x = 2\n"

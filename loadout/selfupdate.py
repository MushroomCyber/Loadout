"""Update Loadout itself, from the git checkout it is running from.

There is no released binary or PyPI package to fetch here -- `pip install -e`
or `pipx install --editable .` are the only documented install paths (see
README), so the "package" *is* the git working tree the interpreter is
already importing from. That makes `git fetch` + a fast-forward-only merge
the correct mechanism, not a smaller version of the checksum/signature
machinery :mod:`loadout.providers.github` uses for third-party tools -- there
is no separate release artifact here to check a signature over.

"Securely" means narrow, not clever:

* only ever runs a fixed, literal `git` argv -- nothing built from fetched
  content is ever passed to a shell or exec'd;
* refuses a dirty working tree rather than silently discarding local edits
  (this checkout may be the developer's own working copy, not just an
  install);
* refuses anything but a fast-forward merge, so it can never rewrite or drop
  a commit history the user doesn't already have;
* merges the exact commit SHA it fetched and showed the caller, not
  whatever the remote branch happens to point at by the time the user
  confirms -- what was shown is what gets applied;
* never elevates privilege and never runs pip, a hook, or any other
  post-pull step on the caller's behalf.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_GIT_TIMEOUT = 30
_FETCH_TIMEOUT = 60
_REMOTE_NAME = "origin"


@dataclass
class UpdateStatus:
    repo_root: Path
    branch: str
    remote_url: str
    current_commit: str
    remote_commit: str
    ahead: int = 0
    behind: int = 0
    dirty: bool = False
    error: str = ""

    @property
    def up_to_date(self) -> bool:
        return not self.error and self.behind == 0

    @property
    def can_update(self) -> bool:
        return not self.error and self.behind > 0 and self.ahead == 0 and not self.dirty

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "remote_url": self.remote_url,
            "current_commit": self.current_commit,
            "remote_commit": self.remote_commit,
            "ahead": self.ahead,
            "behind": self.behind,
            "dirty": self.dirty,
            "up_to_date": self.up_to_date,
            "can_update": self.can_update,
            "error": self.error,
        }


@dataclass
class UpdateResult:
    ok: bool
    old_commit: str = ""
    new_commit: str = ""
    deps_changed: bool = False
    error: str = ""


def find_repo_root(start: Path | None = None) -> Path | None:
    """The git checkout Loadout itself is running from, if any.

    An editable install points the package straight at the checkout, so
    walking up from this file finds it. A wheel/sdist install has no `.git`
    anywhere above it -- there is nothing here to update, and the caller
    should say so rather than pretend.
    """
    here = start or Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    return None


def _run_git(
    repo_root: Path, *args: str, timeout: int = _GIT_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """Run one git subcommand, by absolute path.

    Resolved through `shutil.which` rather than spawned as bare "git": this
    is the one code path that rewrites the files the next `loadout` run
    executes, so it should not also be the one that inherits whatever PATH
    happens to resolve first.
    """
    git = shutil.which("git")
    if git is None:
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=127, stdout="", stderr="git: command not found"
        )
    return subprocess.run(  # noqa: S603 - absolute path, fixed literal argv
        [git, *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def check_update(repo_root: Path) -> UpdateStatus:
    """Fetch the tracking branch and report how this checkout compares.

    The only network call in this module. Everything after it -- diverged,
    dirty, up to date -- is decided from what was just fetched, not
    re-queried, so a slow caller can't end up confirming one state and
    applying another.
    """
    if shutil.which("git") is None:
        return UpdateStatus(
            repo_root=repo_root,
            branch="",
            remote_url="",
            current_commit="",
            remote_commit="",
            error="git is not installed, so this checkout cannot be updated",
        )

    branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"
    remote_url = _run_git(repo_root, "remote", "get-url", _REMOTE_NAME).stdout.strip()
    current_commit = _run_git(repo_root, "rev-parse", "HEAD").stdout.strip()

    def _fail(message: str) -> UpdateStatus:
        return UpdateStatus(
            repo_root=repo_root,
            branch=branch,
            remote_url=remote_url,
            current_commit=current_commit,
            remote_commit="",
            error=message,
        )

    if not remote_url:
        return _fail(f"no '{_REMOTE_NAME}' remote configured in {repo_root}")
    if not current_commit:
        return _fail("could not read the current commit")
    if branch == "HEAD":
        return _fail("detached HEAD -- not on a branch, update manually with git")

    fetch = _run_git(repo_root, "fetch", "--quiet", _REMOTE_NAME, branch, timeout=_FETCH_TIMEOUT)
    if fetch.returncode != 0:
        return _fail(f"git fetch failed: {fetch.stderr.strip()[:200]}")

    remote_ref = f"{_REMOTE_NAME}/{branch}"
    remote_commit = _run_git(repo_root, "rev-parse", remote_ref).stdout.strip()
    if not remote_commit:
        return _fail(f"{remote_ref} does not exist -- is {branch} pushed to {_REMOTE_NAME}?")

    dirty = bool(_run_git(repo_root, "status", "--porcelain").stdout.strip())

    counts = _run_git(
        repo_root, "rev-list", "--left-right", "--count", f"{current_commit}...{remote_commit}"
    ).stdout.split()
    ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)

    return UpdateStatus(
        repo_root=repo_root,
        branch=branch,
        remote_url=remote_url,
        current_commit=current_commit,
        remote_commit=remote_commit,
        ahead=ahead,
        behind=behind,
        dirty=dirty,
    )


def apply_update(repo_root: Path, status: UpdateStatus | None = None) -> UpdateResult:
    """Fast-forward this checkout to the commit `check_update` already fetched.

    Deliberately does nothing else: no `pip install`, no running anything the
    new commit might contain. That keeps the trust boundary at "pulled code
    the user will see run the next time they start loadout", the same as
    running `git pull` themselves would.
    """
    if status is None:
        status = check_update(repo_root)
    if status.error:
        return UpdateResult(ok=False, error=status.error)
    if status.dirty:
        return UpdateResult(
            ok=False, error="local changes would be overwritten -- commit or stash them first"
        )
    if status.ahead:
        return UpdateResult(
            ok=False,
            error="this checkout has local commits the remote doesn't -- resolve with git",
        )
    if status.behind == 0:
        return UpdateResult(ok=True, old_commit=status.current_commit, new_commit=status.current_commit)

    before = _dependency_snapshot(repo_root)
    merge = _run_git(repo_root, "merge", "--ff-only", status.remote_commit)
    if merge.returncode != 0:
        return UpdateResult(ok=False, error=f"git merge --ff-only failed: {merge.stderr.strip()[:200]}")
    after = _dependency_snapshot(repo_root)

    return UpdateResult(
        ok=True,
        old_commit=status.current_commit,
        new_commit=status.remote_commit,
        deps_changed=before != after,
    )


def _dependency_snapshot(repo_root: Path) -> str:
    try:
        return (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return ""

"""The README is the first thing anyone reads, and it drifts silently.

These tests do not check prose. They check the claims that go stale on their
own: commands that were renamed, key bindings that moved, and internal links
to files that were deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md")


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def documented_commands() -> set[tuple[str, str]]:
    """Every `loadout <cmd> [<sub>]` invocation shown in the README."""
    found = set()
    for match in re.finditer(
        r"^\s*loadout\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?", read("README.md"), re.M
    ):
        found.add((match.group(1), match.group(2) or ""))
    return found


def subcommands(parser) -> dict:
    """The subparser map, not the first action that happens to have choices --
    `--theme` also has choices and would silently stand in for it."""
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def test_readme_documents_only_real_subcommands():
    from loadout.ui.cli import build_parser

    top = subcommands(build_parser())
    assert top, "could not introspect the parser's subcommands"

    unknown = {name for name, _ in documented_commands() if name not in top}
    assert not unknown, f"README documents commands that do not exist: {sorted(unknown)}"


def test_readme_documents_only_real_nested_subcommands():
    from loadout.ui.cli import build_parser

    top = subcommands(build_parser())

    bad = []
    for name, sub in documented_commands():
        if not sub or name not in top:
            continue
        nested = subcommands(top[name])
        if nested and sub not in nested:
            bad.append(f"{name} {sub}")
    assert not bad, f"README documents subcommands that do not exist: {sorted(bad)}"


def test_readme_key_table_matches_the_real_bindings():
    """A key table is the part of a README nobody re-reads after changing a
    binding, so it is the part most likely to be wrong."""
    textual = pytest.importorskip("textual")  # noqa: F841
    from loadout.ui.tui.app import LoadoutBrowser

    real = {b.key for b in LoadoutBrowser.BINDINGS}
    # ctrl+p is Textual's own palette binding, not one of ours.
    real.add("ctrl+p")
    # The README spells keys the way Textual's own footer does.
    aliases = {"escape": "esc"}
    real |= {aliases[key] for key in real & aliases.keys()}

    section = read("README.md").split("### Keys", 1)[1].split("###", 1)[0]
    documented = set(re.findall(r"\|\s*`([a-z0-9+]+)`\s*\|", section))
    missing = documented - real
    assert not missing, f"README documents bindings the app does not have: {sorted(missing)}"


def test_internal_doc_links_resolve():
    broken = []
    for name in DOCS:
        text = read(name)
        for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (ROOT / target.split("#")[0]).exists():
                broken.append(f"{name}: [{label}]({target})")
    assert not broken, f"broken internal links: {broken}"


def test_readme_does_not_resurrect_the_old_name():
    """This is a replacement, not a fork with a compatibility story."""
    for name in ("README.md", "CONTRIBUTING.md", "docs/CATALOG.md"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        assert "kalitools" not in text, f"{name} still refers to kalitools"
        assert "kali tools manager" not in text, f"{name} still refers to the old name"


# ---------------------------------------------------------------------------
# What the public repository publishes
# ---------------------------------------------------------------------------


def tracked_files() -> list[str]:
    """Every path git would publish. Skipped where git is unavailable."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
        pytest.skip("git is not available")
    if result.returncode != 0 or not result.stdout.strip():  # pragma: no cover
        pytest.skip("not a git checkout")
    return result.stdout.splitlines()


def test_development_notes_are_not_published():
    """TODO.md is working notes on the author's machine. The repository is
    public; the public backlog is the issue tracker."""
    assert "TODO.md" not in tracked_files()


def test_no_local_or_generated_files_are_published():
    """A committed virtualenv, cache or coverage file is how a local path --
    and sometimes a local secret -- reaches a public repository."""
    import fnmatch

    forbidden = (
        ".venv/*", "venv/*", "*.pyc", "__pycache__/*", ".env", "*.log",
        ".coverage", "htmlcov/*", ".pytest_cache/*", ".ruff_cache/*",
        ".mypy_cache/*", "*.egg-info/*", ".cache/*", "*.swp", ".DS_Store",
        "*.pem", "*.key", "id_rsa*", "*.sqlite", "*.sqlite3",
    )
    published = tracked_files()
    offenders = [
        path
        for path in published
        for pattern in forbidden
        if fnmatch.fnmatch(path, pattern)
    ]
    assert offenders == [], f"these would be published: {offenders}"


def test_the_only_published_database_is_the_compiled_catalog():
    """state.db holds one machine's install history, stars and provenance. It
    lives under XDG_STATE_HOME precisely so it can never be committed, but a
    stray copy inside the tree would be published without anyone noticing."""
    databases = [p for p in tracked_files() if p.endswith(".db")]
    assert databases == ["loadout/data/catalog.db"], databases


class TestVersionAgreement:
    """The version is written in two files. A release tag is a third claim
    about it, and the release workflow refuses a mismatch -- but finding that
    out at tag time means the tag is already pushed."""

    def _root(self):
        return Path(__file__).resolve().parent.parent

    def test_pyproject_and_the_package_agree(self):
        # Read with a regex rather than `tomllib`: this project supports 3.10,
        # where that module does not exist. The release workflow pins 3.12 and
        # parses it properly there.
        root = self._root()
        pyproject = re.search(
            r'^version\s*=\s*"([^"]+)"',
            (root / "pyproject.toml").read_text(encoding="utf-8"),
            re.MULTILINE,
        ).group(1)
        source = re.search(
            r'__version__\s*=\s*"([^"]+)"',
            (root / "loadout" / "__init__.py").read_text(encoding="utf-8"),
        ).group(1)
        assert pyproject == source

    def test_the_release_workflow_checks_the_tag_against_both(self):
        """Whatever this test asserts locally, the tag itself is only checked
        in CI -- so the check has to exist there."""
        workflow = (self._root() / ".github" / "workflows" / "release.yml")
        assert workflow.is_file()
        text = workflow.read_text(encoding="utf-8")
        assert "GITHUB_REF_NAME" in text
        assert "pyproject.toml" in text
        assert "__version__" in text



class TestTheNumbersTheReadmeQuotes:
    """Counts are the part of a README that rots without anyone noticing.

    `github 15` sat in the provider table while the catalog had 17 routes,
    which is exactly the kind of claim a reader has no way to doubt. These
    fail loudly and name the replacement, so the fix is one line.
    """

    def _catalog(self):
        from loadout.catalog.store import CatalogStore

        return CatalogStore(ROOT / "loadout" / "data" / "catalog.db")

    def test_the_tool_count_is_the_catalog_size(self):
        with self._catalog() as store:
            total = store.count()
        readme = read("README.md")
        assert f"catalog-{total}%20tools" in readme, f"badge should say {total}"
        assert f"**{total}**" in readme or f"{total} " in readme

    def test_the_provider_table_matches_the_compiled_catalog(self):
        with self._catalog() as store:
            counts = dict(store.facet_values("provider"))

        readme = read("README.md")
        marker = "**Providers with catalog coverage today:**"
        assert marker in readme, "the provider coverage line is gone"
        # The claim wraps across source lines; the paragraph is the unit.
        start = readme.index(marker)
        line = readme[start : readme.index(chr(10) * 2, start)]
        quoted = {
            name: int(number)
            for name, number in re.findall(r"`([a-z]+)` (\d+)", line)
        }
        assert quoted == counts, (
            f"README says {quoted}, catalog says {counts} -- "
            "update the provider coverage line"
        )

    def test_the_quoted_catalog_coverage_is_what_the_catalog_holds(self):
        with self._catalog() as store:
            tools = list(store.iter_all())
        binaries = sum(1 for t in tools if t.binaries)
        verify = sum(1 for t in tools if t.verify)

        readme = read("README.md")
        assert f"**{binaries} of {len(tools)}**" in readme, (
            f"README should say {binaries} of {len(tools)} entries name a binary"
        )
        assert f"**{verify}**" in readme, (
            f"README should say {verify} entries carry a verify: command"
        )

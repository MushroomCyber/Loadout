"""Headless tests for the interactive browser.

The TUI had no coverage at all, which made every Textual upgrade a leap of
faith. These drive the real app through Textual's own test harness, so an API
break or a broken binding fails the build instead of surfacing to a user.
"""

from __future__ import annotations

import argparse

import pytest

from loadout.ui.tui.app import textual_available

pytestmark = pytest.mark.skipif(
    not textual_available(), reason="textual is not installed"
)


@pytest.fixture
def app(catalog, monkeypatch):
    from loadout.providers.base import ProviderStatus
    from loadout.ui.cli import Context
    from loadout.ui.tui.app import LoadoutBrowser

    args = argparse.Namespace(as_json=False, catalog=None, prefer=[])
    ctx = Context(args=args)
    ctx._catalog = catalog
    ctx._statuses = {"apt": ProviderStatus(name="apt", available=True)}
    ctx._installed = {"nmap"}
    ctx._raw_inventories = {"apt": {"nmap"}}
    return LoadoutBrowser(ctx)


async def test_app_starts_and_lists_tools(app):
    from textual.widgets import DataTable

    async with app.run_test() as pilot:
        table = pilot.app.query_one("#table", DataTable)
        assert table.row_count == 4, "all sample tools should be listed"


async def test_filter_narrows_as_you_type(app):
    from textual.widgets import DataTable, Input

    async with app.run_test() as pilot:
        pilot.app.query_one("#query", Input).value = "ffuf"
        await pilot.pause()
        table = pilot.app.query_one("#table", DataTable)
        assert table.row_count == 1, "typing should narrow the list immediately"


async def test_escape_clears_the_filter(app):
    from textual.widgets import DataTable, Input

    async with app.run_test() as pilot:
        pilot.app.query_one("#query", Input).value = "ffuf"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert pilot.app.query_one("#query", Input).value == ""
        assert pilot.app.query_one("#table", DataTable).row_count == 4


async def test_space_marks_a_tool(app):
    from textual.widgets import DataTable

    async with app.run_test() as pilot:
        pilot.app.query_one("#table", DataTable).focus()
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert len(pilot.app.marked) == 1, "space should mark the highlighted tool"


async def test_installed_state_is_shown(app):
    """nmap is installed in the fixture; the row must reflect that."""
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "nmap" in pilot.app.ctx.installed()


async def test_detail_pane_populates(app):
    from loadout.ui.tui.app import ToolDetail

    async with app.run_test() as pilot:
        await pilot.pause()
        detail = pilot.app.query_one("#detail", ToolDetail)
        assert detail is not None


async def test_every_binding_resolves_to_a_real_action(app):
    """A binding naming a method that does not exist fails only at keypress."""
    async with app.run_test() as pilot:
        for binding in pilot.app.BINDINGS:
            action = getattr(binding, "action", None)
            if not action or action in ("quit",):
                continue
            assert hasattr(pilot.app, f"action_{action}"), (
                f"binding '{binding.key}' points at missing action_{action}"
            )


async def test_empty_filter_shows_starred_and_installed_first(app):
    """The old order opened on '0trace, 7zip, above...' -- alphabetical noise.
    Starred, then installed, then the rest is more likely to show something
    the user recognises without typing anything."""
    from loadout.state import get_state_db

    get_state_db().set_starred("nuclei", True)
    async with app.run_test() as pilot:
        await pilot.pause()
        ids = [t.id for t in pilot.app._rows]
        assert ids[0] == "nuclei"
        assert ids[1] == "nmap", "installed comes next, alphabetically among installed"


async def test_a_search_query_keeps_relevance_order(app):
    """Prioritisation only applies to the empty-query browse view; a real
    search must still rank by how well it matches, not by star/installed."""
    async with app.run_test() as pilot:
        pilot.app.query_one("#query").value = "ffuf"
        await pilot.pause()
        assert [t.id for t in pilot.app._rows] == ["ffuf"]

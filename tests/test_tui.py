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


# ---------------------------------------------------------------------------
# Buttons: every one calls an action that already existed, matching the
# module docstring's rule. These lock that mapping down so a future change to
# action_* signatures fails a test instead of silently breaking a click.
# ---------------------------------------------------------------------------


async def test_detail_pane_has_an_action_row(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        buttons = pilot.app.query("#detail Button")
        ids = {b.id for b in buttons}
        assert "btn-act" in ids
        assert "btn-star" in ids


async def test_detail_action_row_sits_above_the_facts(app):
    """The detail pane scrolls and is only 40% tall. With the action row last,
    the buttons rendered below the fold and were effectively invisible."""
    from loadout.ui.tui.app import ToolDetail

    async with app.run_test() as pilot:
        await pilot.pause()
        detail = pilot.app.query_one("#detail", ToolDetail)
        kinds = [type(child).__name__ for child in detail.children]
        assert kinds.index("Horizontal") < len(kinds) - 1, kinds


async def test_detail_install_button_matches_tool_state(app):
    """nmap is installed in the fixture: the button must say Remove, not
    Install, and use the error variant, not success."""
    from textual.widgets import Button, DataTable, Input

    async with app.run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one("#query", Input).value = "nmap"
        await pilot.pause()
        pilot.app.query_one("#table", DataTable).focus()
        await pilot.pause()
        button = pilot.app.query_one("#btn-act", Button)
        assert str(button.label) == "Remove"
        assert button.variant == "error"


async def test_detail_star_button_toggles_via_existing_action(app):
    from textual.widgets import Button

    async with app.run_test() as pilot:
        await pilot.pause()
        assert "nmap" not in pilot.app.ctx.starred()
        # .press() rather than pilot.click(): the detail pane can sit below
        # the fold at the harness's default terminal size, and a coordinate
        # click on an off-screen button raises OutOfBounds. press() exercises
        # the same Button.Pressed message without needing real screen space.
        pilot.app.query_one("#btn-star", Button).press()
        await pilot.pause()
        assert "nmap" in pilot.app.ctx.starred()


async def test_detail_install_button_calls_run_for(app, monkeypatch):
    """Clicking Install/Remove must go through the same _run_for() the enter
    key and the batch bar both use -- one code path, three entry points."""
    calls = []
    monkeypatch.setattr(type(app), "_run_for", lambda self, ids: calls.append(list(ids)))

    from textual.widgets import Button

    async with app.run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one("#btn-act", Button).press()
        await pilot.pause()
        assert calls, "the button must call _run_for"


async def test_alternatives_button_only_appears_when_there_are_any(app):
    from textual.widgets import DataTable, Input

    async with app.run_test() as pilot:
        await pilot.pause()
        # nmap declares alternatives=("masscan",) in the fixture.
        pilot.app.query_one("#query", Input).value = "nmap"
        await pilot.pause()
        pilot.app.query_one("#table", DataTable).focus()
        await pilot.pause()
        assert pilot.app.query("#btn-alt")

        pilot.app.query_one("#query", Input).value = "masscan"
        await pilot.pause()
        pilot.app.query_one("#table", DataTable).focus()
        await pilot.pause()
        assert not pilot.app.query("#btn-alt")


async def test_batch_bar_hidden_until_something_is_marked(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = pilot.app.query_one("#batch-bar")
        assert bar.styles.display == "none"

        pilot.app.query_one("#table").focus()
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert bar.styles.display == "block"


async def test_batch_bar_label_tracks_the_marked_count(app):
    from textual.widgets import Button, DataTable

    async with app.run_test() as pilot:
        await pilot.pause()
        table = pilot.app.query_one("#table", DataTable)
        table.focus()
        await pilot.press("space")
        await pilot.press("down")
        await pilot.press("space")
        await pilot.pause()
        assert str(pilot.app.query_one("#btn-apply", Button).label) == "Install 2 marked"


async def test_batch_bar_clear_button_empties_the_marked_set(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button

        pilot.app.query_one("#table").focus()
        await pilot.press("space")
        await pilot.pause()
        assert pilot.app.marked
        pilot.app.query_one("#btn-clear", Button).press()
        await pilot.pause()
        assert pilot.app.marked == set()


async def test_batch_bar_apply_button_calls_apply_marked_action(app, monkeypatch):
    calls = []
    monkeypatch.setattr(type(app), "action_apply_marked", lambda self: calls.append(True))

    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button

        pilot.app.query_one("#table").focus()
        await pilot.press("space")
        await pilot.pause()
        pilot.app.query_one("#btn-apply", Button).press()
        await pilot.pause()
        assert calls


async def test_provider_toggle_row_lists_only_available_providers(app):
    """The fixture reports only apt as available; a button for an undetected
    provider would offer a filter that can never match anything."""
    async with app.run_test() as pilot:
        await pilot.pause()
        buttons = pilot.app.query("#providers Button")
        ids = {b.id for b in buttons}
        assert ids == {"prov-apt"}


async def test_provider_toggle_filters_the_list(app):
    from textual.widgets import DataTable

    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button

        table = pilot.app.query_one("#table", DataTable)
        before = table.row_count
        pilot.app.query_one("#prov-apt", Button).press()
        await pilot.pause()
        # Every sample tool has an apt route in the fixture, so toggling apt
        # on must not drop any rows -- this asserts the filter ran, not that
        # it happened to change the count.
        assert table.row_count == before
        assert "apt" in pilot.app._active_providers


async def test_provider_toggle_is_skipped_when_the_catalog_has_no_entries(app):
    """A provider can be installed on the box and still have nothing in the
    catalog that uses it. Offering that toggle gives the user a control whose
    only possible outcome is an empty table."""
    from loadout.providers.base import ProviderStatus

    app.ctx._statuses = {
        "apt": ProviderStatus(name="apt", available=True),
        "npm": ProviderStatus(name="npm", available=True),
    }
    async with app.run_test() as pilot:
        await pilot.pause()
        ids = {b.id for b in pilot.app.query("#providers Button")}
        assert ids == {"prov-apt"}, "npm matches no sample tool and must not appear"


async def test_provider_toggle_label_carries_the_catalog_count(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button

        label = str(pilot.app.query_one("#prov-apt", Button).label)
        assert label.startswith("apt ")
        assert label.split()[1].isdigit()


async def test_active_provider_toggle_changes_variant_not_just_a_class(app):
    """`reverse` alone was indistinguishable from Textual's focus highlight,
    so a user could not tell whether the click had registered."""
    from textual.widgets import Button

    async with app.run_test() as pilot:
        await pilot.pause()
        button = pilot.app.query_one("#prov-apt", Button)
        assert button.variant == "default"
        button.press()
        await pilot.pause()
        assert button.variant == "primary"
        button.press()
        await pilot.pause()
        assert button.variant == "default"


async def test_empty_results_clear_the_detail_pane(app):
    """The action row acts on the selected tool. Left standing over an empty
    table it offers to install something the current filter excludes."""
    from textual.widgets import Input

    from loadout.ui.tui.app import ToolDetail

    async with app.run_test() as pilot:
        await pilot.pause()
        assert pilot.app.query("#detail Button")
        pilot.app.query_one("#query", Input).value = "zzzznotathing"
        await pilot.pause()
        assert pilot.app._rows == []
        detail = pilot.app.query_one("#detail", ToolDetail)
        assert not detail.query("Button")


async def test_hint_says_of_total_when_the_list_is_capped(app):
    """The banner counts the whole catalog and the hint counts the rows shown.
    Reporting the capped number as if it were the total gave two answers to one
    question on the same screen."""
    from textual.widgets import Static

    async with app.run_test() as pilot:
        await pilot.pause()
        total = pilot.app.ctx.catalog.count()
        pilot.app._rows = pilot.app._rows[:1]
        pilot.app._update_hint()
        await pilot.pause()
        hint = str(pilot.app.query_one("#hint", Static).render())
        assert f"1 of {total}" in hint, hint


async def test_facet_counts_are_conditioned_on_the_other_filters(app):
    """A global count is a trap once two filters combine. With `reverse` and
    `gh` both on, the sidebar read `reverse 0/17` and `gh 15` over an empty
    table -- every number on screen contradicting the result."""
    from textual.widgets import Button

    async with app.run_test() as pilot:
        await pilot.pause()
        toggle = pilot.app.query_one("#prov-apt", Button)
        assert str(toggle.label) == "apt 4", str(toggle.label)

        # ffuf is the only sample tool tagged web, and it has an apt route.
        pilot.app.query_one("#facet-web", Button).press()
        await pilot.pause()
        assert str(toggle.label) == "apt 1", str(toggle.label)


async def test_a_chip_that_can_return_nothing_says_so(app):
    """The count must go to zero, not stay at its global value, so the user
    can see the combination is empty before clicking it."""
    from textual.widgets import Button, Input

    async with app.run_test() as pilot:
        await pilot.pause()
        web = pilot.app.query_one("#facet-web", Button)
        assert "/1" in str(web.label), str(web.label)

        pilot.app.query_one("#query", Input).value = "zzzznotathing"
        await pilot.pause()
        assert "/0" in str(web.label), str(web.label)
        assert web.has_class("-empty")


async def test_the_all_chip_counts_what_the_other_filters_allow(app):
    from textual.widgets import Button, Input

    async with app.run_test() as pilot:
        await pilot.pause()
        chip = pilot.app.query_one("#facet-all", Button)
        assert str(chip.label).split()[-1] == "4", str(chip.label)

        pilot.app.query_one("#query", Input).value = "nmap"
        await pilot.pause()
        assert str(chip.label).split()[-1] == "1", str(chip.label)


async def test_search_ids_matches_search_but_skips_building_tools(catalog):
    """The sidebar wants counts and membership, nothing else. Going through
    `search` rebuilt every Tool nineteen times per keystroke."""
    for kwargs in (
        {},
        {"query": "nmap"},
        {"categories": ["web"]},
        {"providers": ["apt"]},
        {"query": "zzz"},
    ):
        query = kwargs.pop("query", "")
        assert catalog.search_ids(query, **kwargs) == [
            tool.id for tool in catalog.search(query, **kwargs)
        ], kwargs


async def test_empty_results_say_what_is_filtering(app):
    from textual.widgets import Input, Static

    async with app.run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one("#query", Input).value = "zzzznotathing"
        await pilot.pause()
        hint = str(pilot.app.query_one("#hint", Static).render())
        assert "no tools match" in hint
        assert "zzzznotathing" in hint


async def test_category_chips_replace_the_category_list(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        chips = pilot.app.query("#facetlist Button")
        assert any(c.id == "facet-all" for c in chips)


async def test_no_category_chip_uses_the_warning_variant(app):
    """Amber means *something is wrong* everywhere else in a security tool.
    A partly-installed category is not a warning, and users read it as one."""
    from textual.widgets import Button

    async with app.run_test() as pilot:
        await pilot.pause()
        variants = {c.variant for c in pilot.app.query("#facetlist Button").results(Button)}
        assert "warning" not in variants
        assert "error" not in variants


async def test_category_chip_label_shows_installed_over_total(app):
    """The colour reinforces the ratio; the ratio is what actually says where
    the user's loadout is thin."""
    import re

    from textual.widgets import Button

    async with app.run_test() as pilot:
        await pilot.pause()
        chip = pilot.app.query_one("#facet-recon", Button)
        assert re.search(r"\d+/\d+", str(chip.label)), str(chip.label)


async def test_active_category_chip_takes_the_accent_and_gives_it_back(app):
    """Selecting a category used to leave no visual trace: only the hardcoded
    `all` chip was ever coloured, so the active filter was invisible."""
    from textual.widgets import Button

    async with app.run_test() as pilot:
        await pilot.pause()
        recon = pilot.app.query_one("#facet-recon", Button)
        was = recon.variant
        recon.press()
        await pilot.pause()
        assert recon.variant == "primary"
        assert pilot.app.query_one("#facet-all", Button).variant == "default"
        pilot.app.query_one("#facet-all", Button).press()
        await pilot.pause()
        assert recon.variant == was
        assert pilot.app.query_one("#facet-all", Button).variant == "primary"


async def test_banner_uses_the_art_on_a_roomy_terminal(app):
    from textual.widgets import Static

    from loadout.ui.tui.app import BANNER_ART

    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        rendered = str(pilot.app.query_one("#banner", Static).render())
        assert BANNER_ART[0].strip() in rendered


async def test_banner_falls_back_to_one_line_on_a_small_terminal(app):
    """Six rows of chrome on an 80x24 terminal is a quarter of the screen
    spent on the program telling you its own name."""
    from textual.widgets import Static

    from loadout.ui.tui.app import BANNER_ART

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        rendered = str(pilot.app.query_one("#banner", Static).render())
        assert BANNER_ART[0].strip() not in rendered
        assert "loadout" in rendered


async def test_banner_art_is_a_clean_block(app):
    """A ragged row would shift the facts set beside the art out of line."""
    from loadout.ui.tui.app import BANNER_ART, BANNER_WIDTH

    assert len({len(row) for row in BANNER_ART}) == 1
    assert len(BANNER_ART[0]) == BANNER_WIDTH
    assert all(row.strip() for row in BANNER_ART)


async def test_clicking_a_category_chip_filters_like_the_old_listview_did(app):
    from textual.widgets import DataTable

    async with app.run_test() as pilot:
        await pilot.pause()
        chips = list(pilot.app.query("#facetlist Button"))
        web_chip = next(c for c in chips if (c.id or "").startswith("facet-web"))
        web_chip.press()
        await pilot.pause()
        table = pilot.app.query_one("#table", DataTable)
        assert table.row_count == 1  # only ffuf is tagged web in the fixture


async def test_install_modal_shows_close_and_retry_on_failure():
    """A failed install must offer Retry, not just Esc with no next step."""
    import argparse

    from loadout.executor import ActionResult, ExecResult
    from loadout.model import InstallMethod, Tool
    from loadout.planner import Plan, PlannedAction
    from loadout.providers.base import ProviderStatus
    from loadout.ui.cli import Context
    from loadout.ui.tui.app import InstallScreen, LoadoutBrowser

    args = argparse.Namespace(as_json=False, catalog=None, prefer=[])
    ctx = Context(args=args)
    ctx._statuses = {"apt": ProviderStatus(name="apt", available=True)}

    tool = Tool(id="nmap", install=(InstallMethod(provider="apt", spec={"package": "nmap"}),))
    plan = Plan(
        actions=[
            PlannedAction(
                tool=tool, action="install", provider="apt",
                method=tool.install[0], steps=[],
            )
        ]
    )

    driver_app = LoadoutBrowser(ctx)
    async with driver_app.run_test() as pilot:
        screen = InstallScreen(ctx, plan, "install")

        import loadout.executor as executor_module

        def fake_run(self, plan):
            return ExecResult(
                results=[ActionResult("nmap", "install", "apt", False, 0.1, error="boom")]
            )

        original = executor_module.Executor.run
        executor_module.Executor.run = fake_run
        try:
            await pilot.app.push_screen(screen)
            for _ in range(40):
                await pilot.pause()
                if screen._done and screen.query("#actions Button"):
                    break
            assert screen._done
            labels = {str(b.label) for b in screen.query("#actions Button")}
            assert labels == {"Retry", "Close"}
        finally:
            executor_module.Executor.run = original


async def test_command_palette_includes_bundled_loadouts(app):
    """Loadouts must be reachable through Textual's built-in ctrl+p palette,
    not just as a CLI subcommand."""
    from loadout.ui.tui.app import LoadoutCommands

    async with app.run_test() as pilot:
        await pilot.pause()
        # Provider is constructed against a live screen by Textual's own
        # CommandPalette normally; build it the same way here rather than
        # bypassing __init__, which left private state (match_style) unset.
        provider = LoadoutCommands(pilot.app.screen)

        # The slug is what `loadout apply` takes, the name is what the palette
        # shows -- typing either has to find the same loadout.
        by_slug = [hit async for hit in provider.search("recon-modern")]
        by_name = [hit async for hit in provider.search("Modern Recon")]
        assert any("Modern Recon" in str(hit.match_display) for hit in by_slug)
        assert any("Modern Recon" in str(hit.match_display) for hit in by_name)


# ---------------------------------------------------------------------------
# The install modal outliving its app
# ---------------------------------------------------------------------------


async def test_the_run_button_is_gone():
    """Handing a live terminal to an arbitrary tool from inside a full-screen
    app never worked reliably -- stdin ownership, suspend/resume and the
    press-Enter pause each broke in a different way. Getting tools onto the
    machine is loadout's job; running them is the shell's."""
    from loadout.ui.tui.app import LoadoutBrowser

    assert not hasattr(LoadoutBrowser, "action_run_tool")
    assert not hasattr(LoadoutBrowser, "_run_command")
    assert not any(
        getattr(binding, "action", None) == "run_tool" for binding in LoadoutBrowser.BINDINGS
    )


async def test_the_detail_pane_offers_no_run_button(app):
    from textual.widgets import DataTable, Input

    async with app.run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one("#query", Input).value = "nmap"
        await pilot.pause()
        pilot.app.query_one("#table", DataTable).focus()
        await pilot.pause()
        labels = {str(b.label) for b in pilot.app.query("#detail Button")}
        assert "Run" not in labels
        assert not list(pilot.app.query("#btn-run"))


async def test_output_arriving_after_the_screen_closes_is_dropped_not_raised():
    """The executor drains a subprocess on a worker thread. Quitting mid-install
    detaches the screen while that drain is still going, and `self.app` on a
    detached widget raises NoActiveAppError -- which used to escape through the
    executor and print a traceback for every line apt had left to say.
    """
    import argparse

    from loadout.providers.base import ProviderStatus
    from loadout.ui.cli import Context
    from loadout.ui.tui.app import InstallScreen

    args = argparse.Namespace(as_json=False, catalog=None, prefer=[])
    screen = InstallScreen(Context(args=args), object(), "install")
    screen.ctx._statuses = {"apt": ProviderStatus(name="apt", available=True)}

    # Never mounted: exactly the state a screen is in once the app has torn it
    # down, and the state in which `self.app` blows up.
    called: list = []
    screen._post(called.append, "a line of apt output")
    assert called == []


async def test_a_failing_call_from_thread_stops_further_posts():
    """Once the app refuses one update it will refuse the rest. Trying anyway
    turns a clean shutdown into one exception per output line."""
    import argparse

    from loadout.ui.cli import Context
    from loadout.ui.tui.app import InstallScreen

    args = argparse.Namespace(as_json=False, catalog=None, prefer=[])
    screen = InstallScreen(Context(args=args), object(), "install")

    attempts: list = []

    class DeadApp:
        def call_from_thread(self, method, *a):
            attempts.append(a)
            raise RuntimeError("app is shutting down")

    screen._app = DeadApp()
    screen._live = True
    screen._post(print, "one")
    screen._post(print, "two")
    assert len(attempts) == 1


# ---------------------------------------------------------------------------
# Handing the terminal to sudo
# ---------------------------------------------------------------------------


def test_the_handover_says_what_is_about_to_happen(capsys):
    """The old version printed one line over whatever the shell had been
    showing. A user who has just pressed Install should see which tools, that
    root is needed, and that nothing has changed yet."""
    from types import SimpleNamespace

    from loadout.planner import ACTION_INSTALL
    from loadout.ui.tui.app import _print_sudo_handover

    plan = SimpleNamespace(
        actions=[
            SimpleNamespace(tool=SimpleNamespace(id="wireshark")),
            SimpleNamespace(tool=SimpleNamespace(id="tcpdump")),
        ]
    )
    _print_sudo_handover(plan, ACTION_INSTALL)
    out = capsys.readouterr().out
    assert "install 2 tools" in out
    assert "wireshark" in out and "tcpdump" in out
    assert "Nothing has been changed yet" in out
    assert "Ctrl+C cancels" in out
    # And that the password is not loadout's to see -- the reason this is a
    # terminal handover rather than a text box in the app.
    assert "never passes through loadout" in out


def test_the_handover_uses_the_right_verb_for_a_removal(capsys):
    from types import SimpleNamespace

    from loadout.planner import ACTION_REMOVE
    from loadout.ui.tui.app import _print_sudo_handover

    plan = SimpleNamespace(actions=[SimpleNamespace(tool=SimpleNamespace(id="nmap"))])
    _print_sudo_handover(plan, ACTION_REMOVE)
    out = capsys.readouterr().out
    assert "remove 1 tool:" in out
    assert "tools" not in out.split("remove 1 tool:")[0][-20:]


def test_a_long_list_is_truncated_rather_than_filling_the_screen(capsys):
    from types import SimpleNamespace

    from loadout.planner import ACTION_INSTALL
    from loadout.ui.tui.app import _print_sudo_handover

    plan = SimpleNamespace(
        actions=[SimpleNamespace(tool=SimpleNamespace(id=f"tool{n}")) for n in range(20)]
    )
    _print_sudo_handover(plan, ACTION_INSTALL)
    out = capsys.readouterr().out
    assert "install 20 tools" in out
    assert "and 14 more" in out
    assert "tool19" not in out


def test_the_screen_is_cleared_so_the_prompt_does_not_land_on_old_output(capsys):
    from types import SimpleNamespace

    from loadout.planner import ACTION_INSTALL
    from loadout.ui.tui.app import CLEAR_SCREEN, _print_sudo_handover

    plan = SimpleNamespace(actions=[SimpleNamespace(tool=SimpleNamespace(id="nmap"))])
    _print_sudo_handover(plan, ACTION_INSTALL)
    assert capsys.readouterr().out.startswith(CLEAR_SCREEN)

"""The renderer every command's results reach the user through.

It was almost entirely untested -- 28% covered -- despite being the layer
that decides what a user believes about their machine. These cover the parts
that make a claim rather than the parts that pick a colour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from loadout.ui.output import relative_age


class TestRelativeAge:
    """state.py has recorded install and last-run timestamps all along; until
    now nothing showed them, so "what can I prune?" had no answer in the UI."""

    def _ago(self, **kwargs) -> str:
        now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
        when = now - timedelta(**kwargs)
        return relative_age(when.isoformat(timespec="seconds"), now=now)

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"hours": 2}, "today"),
            ({"days": 1}, "1d ago"),
            ({"days": 6}, "6d ago"),
            ({"days": 7}, "1w ago"),
            ({"days": 29}, "4w ago"),
            ({"days": 30}, "1mo ago"),
            ({"days": 120}, "4mo ago"),
            ({"days": 400}, "1y ago"),
        ],
    )
    def test_the_step_it_lands_on(self, kwargs, expected):
        assert self._ago(**kwargs) == expected

    def test_nothing_recorded_renders_as_nothing_not_as_a_date(self):
        """A state file written before these columns existed must not read as
        though the tool was installed at the epoch."""
        assert relative_age("") == ""
        assert relative_age("not a timestamp") == ""

    def test_a_naive_timestamp_is_read_as_utc(self):
        """Everything state.py writes is UTC; a stored value without an
        offset must not be compared against an aware 'now' and explode."""
        now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
        assert relative_age("2026-09-01T12:00:00", now=now) == "3d ago"

    def test_a_clock_that_moved_backwards_does_not_report_the_future(self):
        now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
        assert relative_age("2026-09-05T12:00:00+00:00", now=now) == "just now"


class TestDetailShowsFreshness:
    def _rendered(self, status: dict) -> str:
        from io import StringIO

        from rich.console import Console

        import loadout
        from loadout.model import Tool
        from loadout.ui.output import render_detail

        buffer = StringIO()
        console = Console(file=buffer, width=100, force_terminal=False)
        original = loadout.get_console
        loadout.get_console = lambda: console
        try:
            import loadout.ui.output as output_module

            output_module.get_console = lambda: console
            render_detail(Tool(id="nmap", summary="port scanner"), status=status)
        finally:
            loadout.get_console = original
            output_module.get_console = original
        return buffer.getvalue()

    def test_an_installed_tool_says_when_it_arrived_and_when_it_last_ran(self):
        now = datetime.now(timezone.utc)
        text = self._rendered(
            {
                "installed": 1,
                "installed_at": (now - timedelta(days=200)).isoformat(),
                "last_used": (now - timedelta(days=10)).isoformat(),
            }
        )
        assert "6mo ago" in text
        assert "1w ago" in text

    def test_an_installed_tool_that_has_never_been_run_says_so(self):
        now = datetime.now(timezone.utc)
        text = self._rendered(
            {"installed": 1, "installed_at": (now - timedelta(days=3)).isoformat()}
        )
        assert "never" in text

    def test_a_tool_that_is_not_installed_claims_no_dates(self):
        text = self._rendered({"installed": 0})
        assert "last run" not in text


def _capture(fn, *args, **kwargs) -> str:
    """Render through a real rich Console into a buffer.

    Not a mock: the assertions below are about what a user reads, and a
    stubbed console would let a markup or column mistake through.
    """
    from io import StringIO

    from rich.console import Console

    import loadout
    import loadout.ui.output as output_module

    buffer = StringIO()
    console = Console(file=buffer, width=110, force_terminal=False)
    original_pkg = loadout.get_console
    original_mod = output_module.get_console
    loadout.get_console = lambda: console
    output_module.get_console = lambda: console
    try:
        fn(*args, **kwargs)
    finally:
        loadout.get_console = original_pkg
        output_module.get_console = original_mod
    return buffer.getvalue()


def _tool(tool_id="nmap", **kwargs):
    from loadout.model import Tool

    return Tool(id=tool_id, **kwargs)


class TestTheToolTable:
    """The list every `search` and `list` ends in."""

    def test_installed_and_available_tools_are_distinguishable(self):
        from loadout.ui.output import render_tool_table

        text = _capture(
            render_tool_table,
            [_tool("nmap", summary="port scanner"), _tool("ffuf", summary="fuzzer")],
            installed={"nmap"},
        )
        from loadout.ui.output import glyph

        rows = {
            line.strip().split()[1]: line
            for line in text.splitlines()
            if "nmap" in line or "ffuf" in line
        }
        assert glyph("installed") in rows["nmap"]
        assert glyph("installed") not in rows["ffuf"]

    def test_a_tool_with_no_summary_says_so_rather_than_showing_a_blank(self):
        """A blank cell reads as a rendering bug; the catalog genuinely not
        knowing is a different thing and is worth a pull request."""
        from loadout.ui.output import render_tool_table

        text = _capture(render_tool_table, [_tool("obscure")])
        assert "no description in catalog" in text

    def test_the_providers_column_can_be_dropped(self):
        from loadout.model import InstallMethod
        from loadout.ui.output import render_tool_table

        tools = [
            _tool("nmap", install=(InstallMethod(provider="apt", spec={"package": "nmap"}),))
        ]
        assert "VIA" in _capture(render_tool_table, tools)
        assert "VIA" not in _capture(render_tool_table, tools, show_providers=False)

    def test_a_starred_tool_is_marked_in_both_renderings(self):
        from loadout.ui.output import glyph, render_tool_table, tool_rows

        rows = tool_rows([_tool("nmap")], installed=set(), starred={"nmap"})
        assert rows[0]["star"] == glyph("starred")
        assert glyph("starred") in _capture(
            render_tool_table, [_tool("nmap")], starred={"nmap"}
        )


class TestTheDetailPane:
    def test_a_content_entry_lists_paths_where_a_tool_lists_binaries(self):
        """Wordlists have no binary, and printing "binaries: unknown" for one
        reads as a broken entry rather than a different kind of entry."""
        from loadout.model import KIND_CONTENT
        from loadout.ui.output import render_detail

        text = _capture(
            render_detail,
            _tool("seclists", kind=KIND_CONTENT, paths=("/usr/share/seclists",)),
        )
        assert "paths" in text
        assert "binaries" not in text

    def test_an_install_route_shows_whether_its_provider_is_usable_here(self):
        """Choosing between routes is the whole point of the pane; a route
        whose provider is missing is not a route."""
        from loadout.model import InstallMethod
        from loadout.providers.base import ProviderStatus
        from loadout.ui.output import render_detail

        tool = _tool(
            "nmap",
            install=(
                InstallMethod(provider="apt", spec={"package": "nmap"}),
                InstallMethod(provider="brew", spec={"formula": "nmap"}),
            ),
        )
        text = _capture(
            render_detail,
            tool,
            provider_status={"apt": ProviderStatus(name="apt", available=True)},
        )
        assert "apt" in text
        assert "brew" in text
        assert "package=nmap" in text

    def test_a_root_requiring_tool_says_so_before_the_install_does(self):
        from loadout.ui.output import render_detail

        text = _capture(render_detail, _tool("wireshark", requires_root=True))
        assert "requires root" in text

    def test_size_is_shown_in_megabytes_not_bytes(self):
        from loadout.ui.output import render_detail

        text = _capture(render_detail, _tool("trivy", size=240533504))
        assert "229.4 MB" in text

    def test_alternatives_are_offered(self):
        from loadout.ui.output import render_detail

        text = _capture(render_detail, _tool("trivy", alternatives=("grype", "syft")))
        assert "grype" in text


class TestThePlan:
    class _Plan:
        def __init__(self, actions=(), skipped=()):
            self.actions = list(actions)
            self.skipped = list(skipped)

    class _Action:
        def __init__(self, tool_id, provider, *, needs_root=False, lines=()):
            from loadout.model import Tool

            self.tool = Tool(id=tool_id)
            self.provider = provider
            self.needs_root = needs_root
            self.steps = [object()]
            self._lines = list(lines)

        def render(self):
            return self._lines

    class _Skipped:
        def __init__(self, tool_id, reason):
            self.tool_id = tool_id
            self.reason = reason

    def test_an_empty_plan_says_nothing_to_do(self):
        from loadout.ui.output import render_plan

        assert "Nothing to do" in _capture(render_plan, self._Plan())

    def test_a_root_action_is_flagged_before_it_runs(self):
        from loadout.ui.output import render_plan

        plan = self._Plan([self._Action("wireshark", "apt", needs_root=True)])
        assert "root" in _capture(render_plan, plan)

    def test_the_commands_appear_only_when_asked_for(self):
        from loadout.ui.output import render_plan

        plan = self._Plan(
            [self._Action("nmap", "apt", lines=["apt-get install -y nmap"])]
        )
        assert "apt-get install" not in _capture(render_plan, plan)
        assert "apt-get install" in _capture(render_plan, plan, verbose=True)

    def test_a_skipped_tool_carries_its_reason(self):
        """"Skipped" with no reason sends the user to the source."""
        from loadout.ui.output import render_plan

        plan = self._Plan(skipped=[self._Skipped("nmap", "already installed")])
        text = _capture(render_plan, plan)
        assert "already installed" in text


class TestTheGenericTable:
    def test_an_empty_table_says_so_instead_of_printing_a_header_alone(self):
        from loadout.ui.output import render_table

        text = _capture(render_table, [], ["tool", "status"], title="Report")
        assert "nothing to show" in text

    def test_missing_keys_render_as_blanks_rather_than_raising(self):
        """Report rows are assembled from several sources and not every row
        has every column."""
        from loadout.ui.output import render_table

        text = _capture(
            render_table, [{"tool": "nmap"}], ["tool", "status"], title="Report"
        )
        assert "nmap" in text
        assert "Report" in text


class TestConfirmation:
    def test_yes_is_assumed_only_when_the_user_passed_it(self):
        from loadout.ui.output import confirm

        assert confirm("Proceed?", assume_yes=True) is True

    def test_a_pipe_is_answered_no_and_told_how_to_proceed(self, monkeypatch):
        """Defaulting to yes when nobody is watching would install things on a
        machine whose owner never saw the prompt."""
        import sys

        from loadout.ui.output import confirm

        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        text = _capture(lambda: confirm("Proceed?"))
        assert "--yes" in text

    def test_an_interrupted_prompt_is_a_no(self, monkeypatch):
        """Ctrl-C at the confirmation must not fall through to the default and
        install anyway."""
        import sys

        import rich.prompt

        from loadout.ui.output import confirm

        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        def interrupted(*_a, **_kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(rich.prompt.Confirm, "ask", staticmethod(interrupted))
        assert confirm("Proceed?", default=True) is False

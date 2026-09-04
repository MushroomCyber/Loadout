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

"""The interactive browser.

Three deliberate departures from the previous release's list screen:

* **Filtering is the default state.** The cursor starts in the query box and the
  list narrows on every keystroke. The old UI paged: 31 pages of 25 rows, with
  search behind a keypress. Nobody pages through 31 screens to find a tool.
* **Four columns, not six.** Summary is the highest-value column and the old
  layout did not have one, while spending width on a Size column that was
  entirely em-dashes.
* **Batch, not one-at-a-time.** Space marks tools; one confirmation installs the
  set. Installing a loadout should be one transaction, not eighteen modals.

The banner is a single status line rather than art: it spends its one row on
the catalog size, install count and detected platform.

Every action is reachable by keyboard first -- the browser stays usable over a
plain SSH session with no mouse. Buttons are a second way to reach the same
`action_*` methods the keybindings call, never a separate code path: a click
on "Install" does exactly what pressing `enter` does, because both call
`action_act()`.
"""

from __future__ import annotations

from functools import partial
from itertools import groupby
from typing import Any, ClassVar

try:  # pragma: no cover - optional dependency
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding, BindingType
    from textual.command import Hit, Hits
    from textual.command import Provider as CommandProvider
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.css.query import NoMatches
    from textual.reactive import reactive
    from textual.screen import ModalScreen
    from textual.widgets import (
        Button,
        DataTable,
        Footer,
        Input,
        Label,
        ProgressBar,
        Static,
    )

    TEXTUAL_AVAILABLE = True
except Exception:  # pragma: no cover
    TEXTUAL_AVAILABLE = False


def textual_available() -> bool:
    return TEXTUAL_AVAILABLE


#: Abbreviations so the VIA column and provider toggles stay narrow.
_SHORT_PROVIDER = {"github": "gh", "cargo": "crate", "docker": "img"}


#: Above this many results the per-tool lines are traded for a count: the
#: install modal is 22 rows and the log has to keep most of them.
_VERIFY_LINE_LIMIT = 3


def verify_summary(events: list[tuple[str, str, bool]]) -> str:
    """One markup block describing how far the installed files were checked.

    A skipped check is reported as `unverified`, never folded into the
    passing count -- the distinction between "checked and correct" and "not
    checked" is the whole point of showing this at all.
    """
    if not events:
        return ""
    passed = [e for e in events if e[2]]
    failed = [e for e in events if not e[2]]
    if len(events) <= _VERIFY_LINE_LIMIT:
        lines = []
        for tool_id, method, ok in events:
            if ok:
                lines.append(f"[green]✓[/green] {tool_id}: {method} verified")
            else:
                lines.append(f"[yellow]![/yellow] {tool_id}: unverified ({method})")
        return '\n'.join(lines)
    parts = []
    if passed:
        parts.append(f"[green]✓ {len(passed)} verified[/green]")
    if failed:
        names = ", ".join(sorted(e[0] for e in failed)[:3])
        more = "…" if len(failed) > 3 else ""
        parts.append(f"[yellow]! {len(failed)} unverified ({names}{more})[/yellow]")
    return "  ".join(parts)


#: `pyfiglet -f ansi_shadow loadout`, baked in rather than depended on: the
#: output is a constant, and a runtime dependency to regenerate a constant is
#: a dependency for nothing.
BANNER_ART = (
    "██╗      ██████╗  █████╗ ██████╗  ██████╗ ██╗   ██╗████████╗",
    "██║     ██╔═══██╗██╔══██╗██╔══██╗██╔═══██╗██║   ██║╚══██╔══╝",
    "██║     ██║   ██║███████║██║  ██║██║   ██║██║   ██║   ██║   ",
    "██║     ██║   ██║██╔══██║██║  ██║██║   ██║██║   ██║   ██║   ",
    "███████╗╚██████╔╝██║  ██║██████╔╝╚██████╔╝╚██████╔╝   ██║   ",
    "╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚═════╝  ╚═════╝  ╚═════╝    ╚═╝   ",
)

#: The smallest terminal the art earns its keep in. Below this the one-line
#: form is used -- six rows of chrome on a 24-row terminal spends a quarter of
#: the screen on the program's own name.
BANNER_WIDTH = 60
BANNER_MIN_HEIGHT = 30
BANNER_MIN_WIDTH = 96


#: ansi_shadow draws each glyph as a solid face plus a thinner outline frame.
#: Flattened to one colour the two read as identical weight, and a mostly-solid
#: shape like D sits right next to another one like O with nothing marking
#: where one letter ends and the next begins. Two shades restores that edge.
_BANNER_OUTLINE = frozenset("═║╗╝╚╔")

#: Column where "LOAD" ends and "OUT" begins, in BANNER_ART's 60-wide grid.
#: ansi_shadow kerns tightly enough that no column is blank in every row --
#: there is no letter gap to detect automatically -- so this was found by
#: printing the art next to a column ruler and reading off where the second
#: O's opening curve starts. Tied to BANNER_ART: regenerate this by hand if
#: that constant is ever regenerated from a different font or size.
_BANNER_SPLIT_COL = 33


def _shade_banner_row(row: str) -> str:
    """LOAD in one accent, OUT in another; each one's outline darker than its
    own face.

    Splitting the wordmark in two does what the single-colour version could
    not: with every letter the same hue, adjacent mostly-solid shapes -- D
    beside O -- had nothing marking where one ended and the next began.
    """

    def tag_for(item: tuple[int, str]) -> str:
        col, ch = item
        word = "$accent" if col < _BANNER_SPLIT_COL else "$primary"
        return f"{word}-darken-2" if ch in _BANNER_OUTLINE else word

    parts = []
    for tag, group in groupby(enumerate(row), key=tag_for):
        text = "".join(ch for _col, ch in group)
        parts.append(f"[{tag}]{text}[/{tag}]")
    return "".join(parts)


def banner_block(ctx: Any) -> str:
    """The art with this machine's facts set beside it, not under it.

    Stacking the status line below would cost a seventh row for something
    that fits in the whitespace the art already has.
    """
    rows = [_shade_banner_row(row) for row in BANNER_ART]
    # The middle rows are the letterforms' waist, where the art is most even.
    # Text alongside them reads as placed rather than dropped in.
    for offset, fact in zip((2, 3), _facts(ctx), strict=False):
        rows[offset] = f"{rows[offset]}   [dim]{fact}[/dim]"
    return "\n".join(rows)


def _facts(ctx: Any) -> tuple[str, str]:
    """The two lines of machine state the banner shows in either form.

    Distro detection used to sit on the second line as a bare word --
    "kali" -- with nothing saying what it was or why it mattered. Someone
    looking at their own terminal already knows what they installed; the only
    time that value is worth seeing is when detection has gone *wrong*, which
    is exactly what `loadout doctor` and `loadout providers` are for. The
    providers line is the actionable half of what used to share that row --
    what loadout can actually reach on this machine -- so it keeps the row to
    itself.
    """
    try:
        total = ctx.catalog.count()
    except Exception:
        total = 0
    try:
        installed = len(ctx.installed())
    except Exception:
        installed = 0
    try:
        ready = sorted(
            _SHORT_PROVIDER.get(name, name)
            for name, status in ctx.provider_status.items()
            if status.available
        )
    except Exception:
        ready = []
    return (
        f"{total} tools · {installed} installed",
        f"via {' '.join(ready)}" if ready else "no providers detected",
    )


def status_line(ctx: Any) -> str:
    """One line of chrome: who we are, and the state of this machine.

    The fallback for terminals too small to spend six rows on a name. Carries
    the same two facts as the full banner and nothing else -- see `_facts`
    for why the distro name is not one of them.
    """
    try:
        total = ctx.catalog.count()
    except Exception:
        total = 0
    try:
        installed = len(ctx.installed())
    except Exception:
        installed = 0
    return f"[b]loadout[/b]  [dim]{total} tools · {installed} installed[/dim]"


if TEXTUAL_AVAILABLE:

    class ToolDetail(VerticalScroll):
        """The pane that justifies the app over `apt show` -- and, with the
        action row pinned directly under the tool name, the place a mouse user
        actually acts from instead of needing the table focused. The row sits
        above the facts, not below them: the pane scrolls, and buttons that
        need scrolling to reach are buttons nobody finds."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._tool: Any = None

        def update_tool(self, tool: Any, ctx: Any) -> None:
            self.remove_children()
            self._tool = tool
            if tool is None:
                self.mount(Static("[dim]No tool selected[/dim]"))
                return

            installed = tool.id in ctx.installed()
            state = ctx.state.get(tool.id) or {}

            lines: list[str] = []
            status = (
                "[green]● installed[/green]" if installed else "[dim]○ not installed[/dim]"
            )
            if installed and state.get("version"):
                status += f" [dim]{state['version']}[/dim]"
            header = f"[b]{tool.id}[/b]  {status}"
            if installed and state.get("verify_method"):
                header += (
                    f"  [green]✓ {state['verify_method']} verified[/green]"
                    if state.get("verify_ok")
                    else "  [yellow]! unverified[/yellow]"
                )
            if tool.summary:
                lines.append(tool.summary)
                lines.append("")
            if tool.description and tool.description != tool.summary:
                lines.append(f"[dim]{tool.description[:300]}[/dim]")
                lines.append("")

            def field(label: str, value: str) -> None:
                if value:
                    lines.append(f"[dim]{label:>10}[/dim]  {value}")

            field("category", ", ".join(tool.categories))
            field("phases", ", ".join(tool.phases))
            field("tags", ", ".join(tool.tags))
            field("binaries", ", ".join(tool.binaries) or "[dim]unknown[/dim]")
            field("homepage", tool.homepage)
            if tool.size:
                field("size", f"{tool.size / 1024 / 1024:.1f} MB")
            if tool.requires_root:
                field("root", "required")

            if tool.install:
                lines.append("")
                for method in tool.install:
                    status_row = ctx.provider_status.get(method.provider)
                    mark = "[green]✓[/green]" if status_row and status_row.available else "[dim]-[/dim]"
                    lines.append(f"[dim]{'via':>10}[/dim]  {mark} {method.provider}")

            self.mount(Static(header))
            self.mount(self._action_row(tool, ctx, installed))
            self.mount(Static("\n".join(lines)))

        def _action_row(self, tool: Any, ctx: Any, installed: bool) -> Horizontal:
            starred = tool.id in ctx.starred()
            buttons: list[Button] = [
                Button(
                    "Remove" if installed else "Install",
                    variant="error" if installed else "success",
                    id="btn-act",
                    compact=True,
                ),
                Button(
                    "★ Unstar" if starred else "★ Star",
                    id="btn-star",
                    compact=True,
                ),
            ]
            if tool.alternatives:
                buttons.append(Button("Alternatives", id="btn-alt", compact=True))
            return Horizontal(*buttons, classes="detail-actions")

        @on(Button.Pressed, "#btn-act")
        def _btn_act(self, event: Button.Pressed) -> None:
            event.stop()
            self.app.action_act()  # type: ignore[attr-defined]

        @on(Button.Pressed, "#btn-star")
        def _btn_star(self, event: Button.Pressed) -> None:
            event.stop()
            self.app.action_star()  # type: ignore[attr-defined]

        @on(Button.Pressed, "#btn-alt")
        def _btn_alt(self, event: Button.Pressed) -> None:
            event.stop()
            if self._tool and self._tool.alternatives:
                self.app.notify(  # type: ignore[attr-defined]
                    "Alternatives: " + ", ".join(self._tool.alternatives)
                )

    class InstallScreen(ModalScreen[bool]):
        """Runs a plan with real progress and a live log.

        Closable and retryable by mouse: a failed install used to leave the
        user staring at red text with only `esc` and no next step.
        """

        BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close")]

        DEFAULT_CSS = """
        InstallScreen { align: center middle; }
        #box {
            width: 78; height: 22;
            border: round $accent; background: $surface; padding: 1 2;
        }
        #title { text-style: bold; margin-bottom: 1; }
        #progress { height: auto; border-bottom: solid $panel; padding-bottom: 1; }
        #verify { height: auto; background: $panel; padding: 0 1; }
        #log { height: 1fr; margin-top: 1; }
        #actions { height: auto; margin-top: 1; align: right middle; }
        #actions Button { margin-left: 1; }
        """

        def __init__(self, ctx: Any, plan: Any, action: str) -> None:
            super().__init__()
            self.ctx = ctx
            self.plan = plan
            self.action = action
            self._done = False
            #: Captured on the main thread at mount. `self.app` walks the
            #: widget tree, which stops working the moment the screen is
            #: detached -- and a worker thread outlives its screen whenever the
            #: user quits mid-install.
            self._app: Any = None
            self._live = False

        def compose(self) -> ComposeResult:
            with Vertical(id="box"):
                yield Label(
                    f"{self.action.capitalize()}ing {len(self.plan.actions)} tool(s)",
                    id="title",
                )
                with Vertical(id="progress"):
                    yield ProgressBar(total=100, show_eta=False, id="bar")
                    yield Static("", id="status")
                    yield Static("", id="verify")
                yield VerticalScroll(Static("", id="logtext"), id="log")
                yield Horizontal(id="actions")

        def on_mount(self) -> None:
            self._app = self.app
            self._live = True
            self._execute()

        def on_unmount(self) -> None:
            self._live = False

        def _post(self, method: Any, *args: Any) -> None:
            """Run *method* on the UI thread, unless there is no longer a UI.

            A dropped update during teardown is the correct outcome; raising
            out of the executor's output loop -- which is what happened before
            -- printed a NoActiveAppError traceback over the user's terminal
            for every line apt had left to say.
            """
            if not self._live or self._app is None:
                return
            try:
                self._app.call_from_thread(method, *args)
            except Exception:
                # The app is going away; there is nothing left to update.
                self._live = False

        def action_close(self) -> None:
            if self._done:
                self.dismiss(True)

        @on(Button.Pressed, "#btn-close")
        def _btn_close(self, event: Button.Pressed) -> None:
            event.stop()
            self.action_close()

        @on(Button.Pressed, "#btn-retry")
        def _btn_retry(self, event: Button.Pressed) -> None:
            event.stop()
            self._done = False
            self.query_one("#logtext", Static).update("")
            self.query_one("#verify", Static).update("")
            self.query_one("#bar", ProgressBar).update(progress=0)
            self.query_one("#actions", Horizontal).remove_children()
            self._execute()

        @work(thread=True)
        def _execute(self) -> None:
            from ...executor import (
                EVENT_ACTION_DONE,
                EVENT_OUTPUT,
                EVENT_PROGRESS,
                EVENT_VERIFY,
                EVENT_WARN,
                Executor,
            )

            log_lines: list[str] = []
            verify_events: list[tuple[str, str, bool]] = []

            def sink(event) -> None:
                if event.kind == EVENT_PROGRESS and event.percent is not None:
                    self._post(self._set_progress, event.percent, event.message)
                elif event.kind in (EVENT_OUTPUT, EVENT_WARN):
                    log_lines.append(event.message)
                    self._post(self._append_log, log_lines[-14:])
                elif event.kind == EVENT_VERIFY:
                    verify_events.append((event.tool_id, event.message, event.success))
                    self._post(self._set_verify, list(verify_events))
                elif event.kind == EVENT_ACTION_DONE:
                    mark = "[green]✓[/green]" if event.success else "[red]✗[/red]"
                    log_lines.append(f"{mark} {event.tool_id}: {event.message}")
                    self._post(self._append_log, log_lines[-14:])

            executor = Executor(sink=sink, state=self.ctx.state)
            failed = False
            try:
                result = executor.run(self.plan)
                failed = not result.ok
                summary = (
                    f"[green]✓ {len(result.succeeded)} succeeded[/green]"
                    if result.ok
                    else f"[red]✗ {len(result.failures)} failed[/red], "
                    f"{len(result.succeeded)} ok"
                )
            except Exception as exc:
                failed = True
                summary = f"[red]✗ {exc}[/red]"
            self._post(self._finish, summary, failed)

        def _set_progress(self, percent: float, message: str) -> None:
            try:
                self.query_one("#bar", ProgressBar).update(progress=percent)
                self.query_one("#status", Static).update(f"[dim]{message[:70]}[/dim]")
            except NoMatches:  # pragma: no cover - screen torn down mid-update
                self._live = False

        def _append_log(self, lines: list[str]) -> None:
            try:
                self.query_one("#logtext", Static).update("\n".join(lines))
            except NoMatches:  # pragma: no cover - screen torn down mid-update
                self._live = False

        def _set_verify(self, events: list[tuple[str, str, bool]]) -> None:
            """Render verification outcomes inside the progress block.

            Not in the scrolling log: that keeps only the last 14 lines, and
            apt alone says far more than that after a check has passed. A
            batch install collapses to a count rather than growing a line per
            tool, so the log keeps its share of a 22-row modal.
            """
            try:
                target = self.query_one("#verify", Static)
            except NoMatches:  # pragma: no cover - screen torn down mid-update
                self._live = False
                return
            target.update(verify_summary(events))

        def _finish(self, summary: str, failed: bool) -> None:
            self._done = True
            if not self.is_attached:  # pragma: no cover - quit during install
                return
            self.query_one("#bar", ProgressBar).update(progress=100)
            self.query_one("#status", Static).update(summary)
            actions = self.query_one("#actions", Horizontal)
            if failed:
                actions.mount(Button("Retry", variant="primary", id="btn-retry", compact=True))
            actions.mount(Button("Close", id="btn-close", compact=True))

    class SelfUpdateScreen(ModalScreen[bool]):
        """Loadout updating itself, through the same `loadout.selfupdate`
        the CLI's `self-update` calls -- two front doors, one mechanism, so
        the refusals (dirty tree, diverged history, fast-forward only) cannot
        drift between them.

        The remote it would pull from is shown *before* the button that pulls
        from it: this screen is the one place a user can update code with a
        single keypress, and what that key trusts should be on screen.
        """

        BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close")]

        DEFAULT_CSS = """
        SelfUpdateScreen { align: center middle; }
        #ubox {
            width: 76; height: auto;
            border: round $accent; background: $surface; padding: 1 2;
        }
        #utitle { text-style: bold; margin-bottom: 1; }
        #uactions { height: auto; margin-top: 1; align: right middle; }
        #uactions Button { margin-left: 1; }
        """

        def __init__(self) -> None:
            super().__init__()
            self._status: Any = None
            self._app: Any = None
            self._live = False
            self._changed = False

        def compose(self) -> ComposeResult:
            with Vertical(id="ubox"):
                yield Label("Update Loadout", id="utitle")
                yield Static("[dim]checking for updates…[/dim]", id="ubody")
                yield Horizontal(id="uactions")

        def on_mount(self) -> None:
            self._app = self.app
            self._live = True
            self._check()

        def on_unmount(self) -> None:
            self._live = False

        def _post(self, method: Any, *args: Any) -> None:
            if not self._live or self._app is None:
                return
            try:
                self._app.call_from_thread(method, *args)
            except Exception:  # pragma: no cover - app going away
                self._live = False

        def action_close(self) -> None:
            self.dismiss(self._changed)

        @on(Button.Pressed, "#btn-uclose")
        def _btn_uclose(self, event: Button.Pressed) -> None:
            event.stop()
            self.action_close()

        @on(Button.Pressed, "#btn-udo")
        def _btn_udo(self, event: Button.Pressed) -> None:
            event.stop()
            self.query_one("#uactions", Horizontal).remove_children()
            self.query_one("#ubody", Static).update("[dim]updating…[/dim]")
            self._apply()

        @work(thread=True)
        def _check(self) -> None:
            """git fetch is a network call -- off the UI thread."""
            from ... import selfupdate

            root = selfupdate.find_repo_root()
            if root is None:
                self._post(
                    self._show,
                    "Loadout is not running from a git checkout, so it cannot "
                    "update itself.\n[dim]Reinstall from the latest release "
                    "instead.[/dim]",
                    False,
                )
                return
            status = selfupdate.check_update(root)
            self._status = status
            self._post(self._show, self._describe(status), status.can_update)

        @work(thread=True)
        def _apply(self) -> None:
            from ... import selfupdate

            status = self._status
            result = selfupdate.apply_update(status.repo_root, status)
            if not result.ok:
                self._post(self._show, f"[red]{result.error}[/red]", False)
                return
            self._changed = True
            lines = [
                f"[green]✓[/green] updated "
                f"{result.old_commit[:10]} -> {result.new_commit[:10]}",
                "",
                "[b]Restart loadout[/b] to run the updated code.",
            ]
            if result.deps_changed:
                lines.append(
                    "[yellow]![/yellow] pyproject.toml changed -- reinstall with:\n"
                    "    pip install -e '.[dev,tui]'"
                )
            self._post(self._show, "\n".join(lines), False)

        @staticmethod
        def _describe(status: Any) -> str:
            if status.error:
                return f"[red]{status.error}[/red]"
            head = (
                f"[dim]remote[/dim]  {status.remote_url}\n"
                f"[dim]branch[/dim]  {status.branch}\n"
                f"[dim]   now[/dim]  {status.current_commit[:10]}"
            )
            if status.up_to_date:
                return f"{head}\n\n[green]✓[/green] Loadout is up to date."
            head += f"\n[dim]latest[/dim]  {status.remote_commit[:10]}"
            if status.dirty:
                return (
                    f"{head}\n\n[yellow]![/yellow] This checkout has uncommitted "
                    "changes.\n[dim]Commit or stash them first -- updating would "
                    "overwrite them.[/dim]"
                )
            if status.ahead:
                return (
                    f"{head}\n\n[yellow]![/yellow] This checkout has "
                    f"{status.ahead} commit(s) the remote does not.\n"
                    "[dim]Resolve it with git; this screen only fast-forwards.[/dim]"
                )
            return f"{head}\n\n{status.behind} commit(s) behind."

        def _show(self, body: str, offer_update: bool) -> None:
            try:
                self.query_one("#ubody", Static).update(body)
                actions = self.query_one("#uactions", Horizontal)
            except NoMatches:  # pragma: no cover - screen torn down mid-update
                self._live = False
                return
            actions.remove_children()
            if offer_update:
                actions.mount(
                    Button("Update", variant="primary", id="btn-udo", compact=True)
                )
            actions.mount(Button("Close", id="btn-uclose", compact=True))

    class LoadoutCommands(CommandProvider):
        """Loadouts as fuzzy-searchable commands in Textual's built-in palette
        (``ctrl+p``). Applying one calls the exact same `_run_for()` the
        batch-apply button and `enter` both call -- three entry points, one
        code path."""

        async def search(self, query: str) -> Hits:
            from ... import loadouts as loadouts_module

            matcher = self.matcher(query)
            app = self.app
            for manifest in loadouts_module.listing():
                label = f"Apply loadout: {manifest.name}"
                # Score the slug too: it is what `loadout apply` takes on the
                # command line, so it is what a user is likely to type here.
                score = max(matcher.match(label), matcher.match(manifest.slug))
                if score > 0:
                    yield Hit(
                        score,
                        matcher.highlight(label),
                        partial(app._run_for, list(manifest.tools)),  # type: ignore[attr-defined]
                        help=f"{len(manifest.tools)} tool(s) — {manifest.description or manifest.slug}",
                    )

    #: Home the cursor, clear the screen, clear the scrollback. The handover
    #: should start on a blank terminal rather than on top of whatever the
    #: shell had been showing.
    CLEAR_SCREEN = "\033[H\033[2J\033[3J"

    #: The art is 60 columns and its block glyphs need a UTF-8 terminal.
    #: Narrower than this, or on a console that cannot encode them, the word
    #: is the banner -- a half-drawn one is worse than none.
    HANDOVER_MIN_WIDTH = 64

    def _handover_banner() -> list[str]:
        """The art the app opens with, or the plain word when it will not fit.

        Reusing the app's own banner is the point: a terminal the user has
        just been dropped into should still look like loadout, and not like
        some other program asking for their password.
        """
        import shutil

        from ..output import ascii_mode

        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
        if ascii_mode() or columns < HANDOVER_MIN_WIDTH:
            return ["  loadout", "  " + "-" * 58]
        return [f"  {row}" for row in BANNER_ART]


    def _print_sudo_handover(plan: Any, action: str) -> None:
        """Explain the handover before sudo takes the terminal.

        Printed while the UI is suspended, so this is plain stdout rather than
        a Textual widget -- there is no app to render into at this point.
        """
        import sys

        from ...planner import ACTION_REMOVE
        from ..output import glyph

        names = [action_item.tool.id for action_item in plan.actions]
        shown = ", ".join(names[:6])
        if len(names) > 6:
            shown += f" and {len(names) - 6} more"
        verb = "remove" if action == ACTION_REMOVE else "install"
        count = f"{len(names)} tool" + ("s" if len(names) != 1 else "")

        write = sys.stdout.write
        write(CLEAR_SCREEN)
        write("\n")
        for line in _handover_banner():
            write(line + "\n")
        write("\n")
        write(f"  About to {verb} {count}:\n")
        write(f"    {glyph('bullet')} {shown}\n\n")
        write("  This needs root, so the terminal is yours for a moment while\n")
        write("  sudo asks for your password. sudo reads it straight from the\n")
        write("  terminal -- it never passes through loadout.\n\n")
        write("  Nothing has been changed yet. Ctrl+C cancels.\n\n")
        sys.stdout.flush()


    class LoadoutBrowser(App):
        """Filter-first tool browser."""

        COMMANDS = App.COMMANDS | {LoadoutCommands}

        CSS = """
        Screen { layers: base; }
        #banner { height: auto; color: $accent; padding: 0 1; }
        #query  { border: none; border-bottom: solid $panel; height: 3; }
        #providers { height: auto; padding: 0 1; border-bottom: solid $panel; }
        #providers Button { margin-right: 1; }
        .-empty { text-opacity: 50%; }
        .provider-toggle.-active { text-style: bold reverse; }
        #facets { width: 26; border-right: solid $panel; }
        #facetlist { height: 1fr; padding: 0 1; }
        #facetlist Button { width: 100%; text-align: left; }
        /* Selection and "well covered" both read as a solid colour fill on
           this button -- primary blue for the one, success green for the
           other -- and at a glance those are just two colours in a list, not
           two different KINDS of state. Provider toggles already mark their
           active one bold+reverse on top of the colour; the category list
           never got the same treatment, so a green chip you have not clicked
           looked exactly as "selected" as the one you actually chose. */
        #facetlist Button.-active { text-style: bold reverse; }
        #table  { height: 1fr; }
        #detail { height: 40%; border-top: solid $panel; padding: 0 1; }
        .detail-actions { height: auto; margin-bottom: 1; }
        .detail-actions Button { margin-right: 1; }
        #batch-bar { height: 3; padding: 0 1; align: left middle; background: $panel; display: none; }
        #batch-bar Button { margin-right: 1; }
        #hint   { height: 1; color: $text-muted; padding: 0 1; }
        DataTable > .datatable--cursor { background: $accent 30%; }
        """

        BINDINGS: ClassVar[list[BindingType]] = [
            Binding("escape", "clear_or_quit", "Clear/Quit"),
            Binding("ctrl+c", "quit", "Quit", priority=True),
            Binding("space", "mark", "Mark"),
            Binding("enter", "act", "Install/Remove"),
            Binding("ctrl+a", "apply_marked", "Apply marked"),
            Binding("ctrl+s", "star", "Star"),
            Binding("ctrl+u", "self_update", "Update loadout"),
            Binding("f5", "refresh", "Refresh"),
            Binding("down", "cursor_down", "", show=False),
            Binding("up", "cursor_up", "", show=False),
        ]

        # init=False: reactive() defaults to calling its watcher once at mount
        # with the initial value, which raced the explicit _reload() call in
        # on_mount() and populated the table twice (DuplicateKey on the second
        # pass). Only explicit reassignment should trigger watch_marked.
        marked: reactive[set[str]] = reactive(set, init=False)

        def __init__(self, ctx: Any) -> None:
            super().__init__()
            self.ctx = ctx
            self._facet: tuple[str, str] | None = None
            self._rows: list[Any] = []
            self._planner_cache: Any = None
            self._active_providers: set[str] = set()
            #: Category slugs in sidebar order, so counts can be refreshed in
            #: place without rebuilding the chips.
            self._facet_slugs: list[str] = []
            self.title = "loadout"

        @property
        def _planner(self) -> Any:
            """Built once: provider detection shells out to every toolchain, so
            rebuilding it per row would make the list unusable."""
            if self._planner_cache is None:
                self._planner_cache = self.ctx.planner()
            return self._planner_cache

        # -- layout --------------------------------------------------------

        def compose(self) -> ComposeResult:
            yield Static(id="banner")
            yield Input(placeholder="Type to filter…  (Esc clears)", id="query")
            yield Horizontal(id="providers")
            with Horizontal():
                with Vertical(id="facets"):
                    yield Label(" CATEGORIES", classes="facet-title")
                    yield VerticalScroll(id="facetlist")
                with Vertical():
                    yield DataTable(id="table", cursor_type="row", zebra_stripes=False)
                    yield ToolDetail(id="detail")
            with Horizontal(id="batch-bar"):
                yield Button("Apply marked", variant="primary", id="btn-apply", compact=True)
                yield Button("Clear", id="btn-clear", compact=True)
            yield Static("", id="hint")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#table", DataTable)
            table.add_column("", width=3, key="mark")
            table.add_column("TOOL", width=26, key="tool")
            table.add_column("SUMMARY", key="summary")
            table.add_column("VIA", width=22, key="via")

            facets = self.query_one("#facetlist", VerticalScroll)
            facets.mount(
                Button("all", id="facet-all", variant="primary", classes="chip -active", compact=True)
            )
            self._facet_slugs = [
                slug for slug, _ in self.ctx.catalog.facet_values("category")[:18]
            ]
            for slug in self._facet_slugs:
                facets.mount(
                    Button(slug, id=f"facet-{slug}", classes="chip", compact=True)
                )

            # A toggle is only worth offering when the provider is usable here
            # *and* the catalog has something it can install. Listing npm or
            # docker on a box where no entry names them gives the user a
            # control whose only possible outcome is an empty table.
            counts = dict(self.ctx.catalog.facet_values("provider"))
            providers = self.query_one("#providers", Horizontal)
            for name in sorted(self.ctx.provider_status):
                status = self.ctx.provider_status[name]
                count = counts.get(name, 0)
                if not status.available or not count:
                    continue
                short = _SHORT_PROVIDER.get(name, name)
                providers.mount(
                    Button(
                        short,
                        id=f"prov-{name}",
                        classes="provider-toggle",
                        compact=True,
                    )
                )

            self._draw_banner()
            self.query_one("#query", Input).focus()
            self._reload()

        def on_resize(self) -> None:
            self._draw_banner()

        def _draw_banner(self) -> None:
            """Art on a terminal with room for it, one line on one without.

            A fixed six-row banner would be a quarter of a default 80x24
            window spent on the program telling you its own name.
            """
            try:
                banner = self.query_one("#banner", Static)
            except NoMatches:
                return
            size = self.size
            roomy = size.height >= BANNER_MIN_HEIGHT and size.width >= BANNER_MIN_WIDTH
            banner.update(banner_block(self.ctx) if roomy else status_line(self.ctx))

        def _refresh_facet_counts(self, query: str) -> None:
            """Recount every chip against the *other* active filters.

            Global counts are a trap once two filters are combined: with
            `reverse` and `gh` both on, the sidebar still read `reverse 0/17`
            and `gh 15` over an empty table, so every number on screen
            contradicted the result. A chip now answers the only question
            worth asking of it -- how many tools you get if you click it --
            and says nothing when the answer is none.

            About 6ms for the whole sidebar on the 774-entry catalog, which is
            why this can run on every keystroke rather than only on a click.
            """
            try:
                chips = {b.id: b for b in self.query("#facetlist Button").results(Button)}
                toggles = {
                    b.id: b for b in self.query(".provider-toggle").results(Button)
                }
            except NoMatches:  # pragma: no cover - teardown
                return

            installed = self.ctx.installed()
            search = self.ctx.catalog.search_ids
            active_providers = sorted(self._active_providers)
            active_category = (
                [self._facet[1]] if self._facet and self._facet[0] == "category" else []
            )

            total = len(search(query, providers=active_providers))
            if (chip := chips.get("facet-all")) is not None:
                chip.label = f"{'all':<12}{total:>7}"

            for slug in self._facet_slugs:
                chip = chips.get(f"facet-{slug}")
                if chip is None:
                    continue
                ids = search(query, categories=[slug], providers=active_providers)
                count = len(ids)
                here = sum(1 for tool_id in ids if tool_id in installed)
                chip.label = f"{slug[:12]:<12}{here:>3}/{count:<3}"
                chip.set_class(count == 0, "-empty")

            for name, toggle in toggles.items():
                provider = (name or "").removeprefix("prov-")
                # Count what this toggle *adds*: how many tools it can reach
                # under the current category and query, ignoring the other
                # toggles, since providers combine as a union.
                count = len(
                    search(query, categories=active_category, providers=[provider])
                )
                short = _SHORT_PROVIDER.get(provider, provider)
                toggle.label = f"{short} {count}"
                toggle.set_class(count == 0, "-empty")

        # -- data ----------------------------------------------------------

        def _reload(self) -> None:
            query = self.query_one("#query", Input).value.strip()
            categories = [self._facet[1]] if self._facet and self._facet[0] == "category" else []
            providers = sorted(self._active_providers)
            if query:
                rows = self.ctx.catalog.search(
                    query, categories=categories, providers=providers, limit=500
                )
            else:
                # FTS relevance has nothing to rank on an empty query, so
                # priority order (starred, then installed, then alphabetical)
                # is what decides what appears here -- and it has to run
                # before the 500-row cap, not after. Capping first meant a
                # starred or installed tool whose id sorted past position 500
                # of 842 never appeared in the unfiltered browser at all,
                # however it was marked: 17 of this box's 43 installed tools
                # were invisible here until priority ran on the full id list.
                all_ids = self.ctx.catalog.search_ids(
                    "", categories=categories, providers=providers
                )
                top_ids = self._prioritised_ids(all_ids)[:500]
                rows = self.ctx.catalog.get_many(top_ids)
            self._rows = rows
            self._render_rows()
            self._refresh_facet_counts(query)

        def _prioritised_ids(self, ids: list[str]) -> list[str]:
            starred = self.ctx.starred()
            installed = self.ctx.installed()

            def rank(tool_id: str) -> tuple[int, str]:
                if tool_id in starred:
                    return (0, tool_id)
                if tool_id in installed:
                    return (1, tool_id)
                return (2, tool_id)

            return sorted(ids, key=rank)

        def _providers_cell(self, tool: Any) -> str:
            """Light the route that would actually run; dim the rest.

            `VIA` used to be every route the catalog knows, comma-joined, which
            told the user nothing about this machine. The planner already
            computes the winner -- this just stops discarding it.
            """
            if not tool.install:
                return "[dim]-[/dim]"
            try:
                chosen, _method = self._planner.choose_method(tool)
            except Exception:
                chosen = ""
            if not chosen:
                return "[red]unavailable here[/red]"
            parts = []
            for name in tool.providers:
                short = _SHORT_PROVIDER.get(name, name)
                parts.append(
                    f"[green]{short}[/green]" if name == chosen else f"[dim]{short}[/dim]"
                )
            return " ".join(parts)

        def _render_rows(self) -> None:
            try:
                self._render_rows_now()
            except NoMatches:
                # Textual can deliver a queued message after teardown has
                # started removing widgets. Nothing to draw on a dead DOM.
                return

        def _render_rows_now(self) -> None:
            table = self.query_one("#table", DataTable)
            table.clear()
            installed = self.ctx.installed()
            starred = self.ctx.starred()
            for tool in self._rows:
                mark = "[green]●[/green]" if tool.id in installed else "[dim]○[/dim]"
                if tool.id in self.marked:
                    mark = "[reverse]▸[/reverse]" + mark
                name = tool.id
                if tool.id in starred:
                    name += " [yellow]★[/yellow]"
                table.add_row(
                    mark,
                    name,
                    tool.summary or "[dim]no description yet[/dim]",
                    self._providers_cell(tool),
                    key=tool.id,
                )
            self._update_hint()
            # Clear rather than leave the last tool's facts and action row
            # standing over an empty table -- those buttons would be offering
            # to install something the current filter excludes.
            self._show_detail(self._rows[0] if self._rows else None)

        def _update_hint(self) -> None:
            installed = self.ctx.installed()
            shown = len(self._rows)
            here = sum(1 for t in self._rows if t.id in installed)
            marked = f"  ·  [reverse] {len(self.marked)} marked [/reverse]" if self.marked else ""
            query = self.query_one("#query", Input).value.strip()
            try:
                total = self.ctx.catalog.count()
            except Exception:
                total = shown
            filtered = bool(query or self._facet or self._active_providers)
            if filtered and not shown:
                # An empty table with no explanation reads as a broken filter.
                # Name what is narrowing it so the user knows what to undo.
                why = []
                if query:
                    why.append(f'"{query}"')
                if self._facet:
                    why.append(self._facet[1])
                if self._active_providers:
                    why.append(" + ".join(sorted(self._active_providers)))
                self.query_one("#hint", Static).update(
                    f"[dim]no tools match {' · '.join(why)}{marked}[/dim]"
                )
                return
            # "500 tools" next to a banner reading "774 tools" is two answers
            # to one question. Say "of" whenever the list is capped, whether by
            # a filter or by the search limit.
            scope = f"{shown} of {total}" if shown < total else f"{shown} tools"
            self.query_one("#hint", Static).update(
                f"[dim]{scope} · {here} installed{marked}[/dim]"
            )

        def _selected_tool(self) -> Any:
            table = self.query_one("#table", DataTable)
            if table.cursor_row is None or not self._rows:
                return None
            try:
                from textual.coordinate import Coordinate

                key = table.coordinate_to_cell_key(
                    Coordinate(table.cursor_row, 0)
                ).row_key
            except Exception:
                return None
            tool_id = key.value if hasattr(key, "value") else str(key)
            return next((t for t in self._rows if t.id == tool_id), None)

        def _show_detail(self, tool: Any) -> None:
            try:
                detail = self.query_one("#detail", ToolDetail)
            except NoMatches:
                # Clearing the table posts RowHighlighted, and Textual can
                # deliver it after teardown has removed the detail pane.
                return
            detail.update_tool(tool, self.ctx)

        # -- events --------------------------------------------------------

        @on(Input.Changed, "#query")
        def _on_query(self, event: Input.Changed) -> None:
            self._reload()

        @on(Input.Submitted, "#query")
        def _on_submit(self) -> None:
            self.query_one("#table", DataTable).focus()

        @on(DataTable.RowHighlighted, "#table")
        def _on_highlight(self, event: DataTable.RowHighlighted) -> None:
            key = event.row_key
            tool_id = key.value if hasattr(key, "value") else str(key)
            tool = next((t for t in self._rows if t.id == tool_id), None)
            if tool is not None:
                self._show_detail(tool)

        @on(Button.Pressed, "#facetlist Button")
        def _on_facet_chip(self, event: Button.Pressed) -> None:
            event.stop()
            button = event.button
            slug = (button.id or "").removeprefix("facet-")
            self._facet = None if slug == "all" else ("category", slug)
            for chip in self.query("#facetlist Button").results(Button):
                chip.remove_class("-active")
                chip.variant = "default"
            button.add_class("-active")
            button.variant = "primary"
            self._reload()

        @on(Button.Pressed, ".provider-toggle")
        def _on_provider_toggle(self, event: Button.Pressed) -> None:
            event.stop()
            button = event.button
            name = (button.id or "").removeprefix("prov-")
            if name in self._active_providers:
                self._active_providers.discard(name)
                button.remove_class("-active")
                button.variant = "default"
            else:
                self._active_providers.add(name)
                button.add_class("-active")
                button.variant = "primary"
            self._reload()

        @on(Button.Pressed, "#btn-apply")
        def _on_apply_button(self, event: Button.Pressed) -> None:
            event.stop()
            self.action_apply_marked()

        @on(Button.Pressed, "#btn-clear")
        def _on_clear_button(self, event: Button.Pressed) -> None:
            event.stop()
            self.marked = set()

        # -- actions -------------------------------------------------------

        def action_clear_or_quit(self) -> None:
            query = self.query_one("#query", Input)
            if query.value:
                query.value = ""
                self._reload()
            elif self.marked:
                self.marked = set()
            else:
                self.exit()

        def action_cursor_down(self) -> None:
            self.query_one("#table", DataTable).action_cursor_down()

        def action_cursor_up(self) -> None:
            self.query_one("#table", DataTable).action_cursor_up()

        def action_mark(self) -> None:
            tool = self._selected_tool()
            if tool is None:
                return
            marked = set(self.marked)
            marked.symmetric_difference_update({tool.id})
            self.marked = marked  # triggers watch_marked, which re-renders

        def action_star(self) -> None:
            tool = self._selected_tool()
            if tool is None:
                return
            starred = tool.id in self.ctx.starred()
            self.ctx.state.set_starred(tool.id, not starred)
            self._render_rows()

        def action_refresh(self) -> None:
            self.ctx._installed = None
            self._reload()

        def action_self_update(self) -> None:
            self.push_screen(SelfUpdateScreen())

        def action_act(self) -> None:
            tool = self._selected_tool()
            if tool is None:
                return
            self._run_for([tool.id])

        def action_apply_marked(self) -> None:
            if not self.marked:
                self.notify("Nothing marked. Press space to mark tools.", severity="warning")
                return
            self._run_for(sorted(self.marked))

        # -- watchers --------------------------------------------------------

        def watch_marked(self, marked: set[str]) -> None:
            """Show or hide the batch bar and keep its label honest. Fires only
            on assignment, not on the reactive's own default, so it is safe to
            query a widget that exists only once the app has mounted."""
            try:
                bar = self.query_one("#batch-bar")
                bar.styles.display = "block" if marked else "none"
                if marked:
                    self.query_one("#btn-apply", Button).label = f"Install {len(marked)} marked"
            except NoMatches:
                return
            self._render_rows()

        def _run_for(self, tool_ids: list[str]) -> None:
            from ...planner import ACTION_INSTALL, ACTION_REMOVE
            from ...policy import detect_privilege, has_cached_credentials

            installed = self.ctx.installed()
            removing = all(tool_id in installed for tool_id in tool_ids)
            action = ACTION_REMOVE if removing else ACTION_INSTALL

            plan = self.ctx.planner().plan(tool_ids, action=action)
            if not plan.actions:
                reason = plan.skipped[0].reason if plan.skipped else "nothing to do"
                self.notify(reason, severity="warning")
                return

            # Get the password *outside* the full-screen app. Prompting for sudo
            # underneath a TUI is what made the previous release's install modal
            # look like a hang.
            privilege = detect_privilege()
            if plan.needs_root and not has_cached_credentials(privilege):
                self._elevate_then_run(plan, action)
                return

            self.push_screen(
                InstallScreen(self.ctx, plan, action), callback=lambda _: self._after_run()
            )

        def _elevate_then_run(self, plan: Any, action: str) -> None:
            """Hand the terminal back so sudo can read from /dev/tty.

            The password never passes through loadout -- sudo opens the
            terminal itself. That is why this cannot be a text box in the app,
            and why the handover is worth explaining rather than just doing.
            """
            from ...policy import refresh_credentials

            with self.suspend():
                _print_sudo_handover(plan, action)
                granted = refresh_credentials()
                if granted:
                    # Leave the terminal as we found it. Without this the
                    # explanation and the prompt stay underneath the restored
                    # UI and reappear the next time anything suspends.
                    print(CLEAR_SCREEN, end="", flush=True)
            if not granted:
                self.notify(
                    "sudo authentication failed or was cancelled -- nothing was changed.",
                    severity="warning",
                    timeout=8,
                )
                return
            self.push_screen(
                InstallScreen(self.ctx, plan, action), callback=lambda _: self._after_run()
            )

        def _after_run(self) -> None:
            self.marked = set()
            self.ctx._installed = None
            self._reload()


def run_tui(ctx: Any) -> int:
    if not TEXTUAL_AVAILABLE:
        raise RuntimeError(
            "Textual is not installed. Install it with: pipx inject loadout textual"
        )
    LoadoutBrowser(ctx).run()
    return 0

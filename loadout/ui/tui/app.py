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

import subprocess
from functools import partial
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
    from textual.widgets._button import ButtonVariant

    TEXTUAL_AVAILABLE = True
except Exception:  # pragma: no cover
    TEXTUAL_AVAILABLE = False


def textual_available() -> bool:
    return TEXTUAL_AVAILABLE


#: Abbreviations so the VIA column and provider toggles stay narrow.
_SHORT_PROVIDER = {"github": "gh", "cargo": "crate", "docker": "img"}


def status_line(ctx: Any) -> str:
    """One line of chrome: who we are, and the state of this machine.

    Replaces a drawn box around whitespace that cost four of roughly forty
    rows. Everything here is information the user would otherwise have to run a
    command to learn.
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
        from ...providers import detect_distro

        where = detect_distro()
    except Exception:
        where = "unknown"
    return f"[b]loadout[/b]  [dim]{total} tools · {installed} installed · {where}[/dim]"


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
            can_run = bool(tool.primary_binary) or any(
                m.provider == "docker" for m in tool.install
            )
            if can_run:
                buttons.append(Button("Run", variant="primary", id="btn-run", compact=True))
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

        @on(Button.Pressed, "#btn-run")
        def _btn_run(self, event: Button.Pressed) -> None:
            event.stop()
            self.app.action_run_tool()  # type: ignore[attr-defined]

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
        #log { height: 1fr; border-top: solid $panel; margin-top: 1; }
        #actions { height: auto; margin-top: 1; align: right middle; }
        #actions Button { margin-left: 1; }
        """

        def __init__(self, ctx: Any, plan: Any, action: str) -> None:
            super().__init__()
            self.ctx = ctx
            self.plan = plan
            self.action = action
            self._done = False

        def compose(self) -> ComposeResult:
            with Vertical(id="box"):
                yield Label(
                    f"{self.action.capitalize()}ing {len(self.plan.actions)} tool(s)",
                    id="title",
                )
                yield ProgressBar(total=100, show_eta=False, id="bar")
                yield Static("", id="status")
                yield VerticalScroll(Static("", id="logtext"), id="log")
                yield Horizontal(id="actions")

        def on_mount(self) -> None:
            self._execute()

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
            self.query_one("#bar", ProgressBar).update(progress=0)
            self.query_one("#actions", Horizontal).remove_children()
            self._execute()

        @work(thread=True)
        def _execute(self) -> None:
            from ...executor import (
                EVENT_ACTION_DONE,
                EVENT_OUTPUT,
                EVENT_PROGRESS,
                EVENT_WARN,
                Executor,
            )

            log_lines: list[str] = []

            def sink(event) -> None:
                if event.kind == EVENT_PROGRESS and event.percent is not None:
                    self.app.call_from_thread(self._set_progress, event.percent, event.message)
                elif event.kind in (EVENT_OUTPUT, EVENT_WARN):
                    log_lines.append(event.message)
                    self.app.call_from_thread(self._append_log, log_lines[-14:])
                elif event.kind == EVENT_ACTION_DONE:
                    mark = "[green]✓[/green]" if event.success else "[red]✗[/red]"
                    log_lines.append(f"{mark} {event.tool_id}: {event.message}")
                    self.app.call_from_thread(self._append_log, log_lines[-14:])

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
            self.app.call_from_thread(self._finish, summary, failed)

        def _set_progress(self, percent: float, message: str) -> None:
            self.query_one("#bar", ProgressBar).update(progress=percent)
            self.query_one("#status", Static).update(f"[dim]{message[:70]}[/dim]")

        def _append_log(self, lines: list[str]) -> None:
            self.query_one("#logtext", Static).update("\n".join(lines))

        def _finish(self, summary: str, failed: bool) -> None:
            self._done = True
            self.query_one("#bar", ProgressBar).update(progress=100)
            self.query_one("#status", Static).update(summary)
            actions = self.query_one("#actions", Horizontal)
            if failed:
                actions.mount(Button("Retry", variant="primary", id="btn-retry", compact=True))
            actions.mount(Button("Close", id="btn-close", compact=True))

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

    class LoadoutBrowser(App):
        """Filter-first tool browser."""

        COMMANDS = App.COMMANDS | {LoadoutCommands}

        CSS = """
        Screen { layers: base; }
        #banner { height: 1; color: $accent; padding: 0 1; }
        #query  { border: none; border-bottom: solid $panel; height: 3; }
        #providers { height: auto; padding: 0 1; border-bottom: solid $panel; }
        #providers Button { margin-right: 1; }
        .provider-toggle.-active { text-style: bold reverse; }
        #facets { width: 26; border-right: solid $panel; }
        #facetlist { height: 1fr; padding: 0 1; }
        #facetlist Button { width: 100%; text-align: left; }
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
            Binding("ctrl+r", "run_tool", "Run"),
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
            yield Static(status_line(self.ctx), id="banner")
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
            installed = self.ctx.installed()
            for slug, count in self.ctx.catalog.facet_values("category")[:18]:
                variant = self._coverage_variant(slug, count, installed)
                facets.mount(
                    Button(
                        f"{slug[:14]:<14}{count:>4}",
                        id=f"facet-{slug}",
                        variant=variant,
                        classes="chip",
                        compact=True,
                    )
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
                        f"{short} {count}",
                        id=f"prov-{name}",
                        classes="provider-toggle",
                        compact=True,
                    )
                )

            self.query_one("#query", Input).focus()
            self._reload()

        def _coverage_variant(self, slug: str, count: int, installed: set[str]) -> ButtonVariant:
            """How much of a category is already installed, as a button colour.

            Cheap even at ~800 tools: one indexed facet query per category,
            done once at startup, not per frame.
            """
            if not count:
                return "default"
            here = sum(1 for t in self.ctx.catalog.search("", categories=[slug]) if t.id in installed)
            coverage = here / count
            if coverage >= 0.3:
                return "success"
            if coverage > 0:
                return "warning"
            return "default"

        # -- data ----------------------------------------------------------

        def _reload(self) -> None:
            query = self.query_one("#query", Input).value.strip()
            categories = [self._facet[1]] if self._facet and self._facet[0] == "category" else []
            providers = sorted(self._active_providers)
            rows = self.ctx.catalog.search(
                query, categories=categories, providers=providers, limit=500
            )
            if not query:
                # FTS relevance has nothing to rank on an empty query, so the
                # store falls back to alphabetical -- which opens the browser
                # on "0trace, 7zip, above...". Put what the user is more
                # likely to want looking at first instead.
                rows = self._prioritised(rows)
            self._rows = rows
            self._render_rows()

        def _prioritised(self, rows: list[Any]) -> list[Any]:
            starred = self.ctx.starred()
            installed = self.ctx.installed()

            def rank(tool: Any) -> tuple[int, str]:
                if tool.id in starred:
                    return (0, tool.id)
                if tool.id in installed:
                    return (1, tool.id)
                return (2, tool.id)

            return sorted(rows, key=rank)

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
            scope = f"{shown} of {total}" if filtered else f"{shown} tools"
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
            for chip in self.query("#facetlist Button"):
                chip.remove_class("-active")
            button.add_class("-active")
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

        def action_run_tool(self) -> None:
            """Run the selected tool's binary directly, outside any provider
            install/remove flow -- for a tool that is already on PATH."""
            tool = self._selected_tool()
            if tool is None:
                return
            binary = tool.primary_binary
            if not binary:
                self.notify(f"{tool.id} has no known binary in the catalog.", severity="warning")
                return

            import shutil as _shutil

            if not _shutil.which(binary):
                self.notify(f"{binary} is not installed.", severity="warning")
                return

            from ...policy import validate_argv

            try:
                argv = validate_argv([binary])
            except Exception as exc:
                self.notify(str(exc), severity="error")
                return

            with self.suspend():
                print(f"\n$ {' '.join(argv)}\n")
                subprocess.run(argv, check=False)  # noqa: S603
                input("\nPress Enter to return to loadout...")
            self.ctx.state.mark_used(tool.id)
            self.ctx.state.record("run", tool.id)

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
            from ...policy import refresh_credentials

            with self.suspend():
                print("\nloadout needs sudo to change installed packages.")
                granted = refresh_credentials()
            if not granted:
                self.notify("sudo authentication failed", severity="error")
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

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

The banner is three lines and only appears when the terminal is tall enough.
"""

from __future__ import annotations

from typing import Any, ClassVar

try:  # pragma: no cover - optional dependency
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding, BindingType
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.reactive import reactive
    from textual.screen import ModalScreen
    from textual.widgets import (
        DataTable,
        Footer,
        Input,
        Label,
        ListItem,
        ListView,
        ProgressBar,
        Static,
    )

    TEXTUAL_AVAILABLE = True
except Exception:  # pragma: no cover
    TEXTUAL_AVAILABLE = False


def textual_available() -> bool:
    return TEXTUAL_AVAILABLE


WORDMARK = """
┌──────────────────────┐
│   [ LOADOUT ]        │
└──────────────────────┘
""".strip("\n")


if TEXTUAL_AVAILABLE:

    class ToolDetail(VerticalScroll):
        """The pane that justifies the app over `apt show`."""

        def update_tool(self, tool: Any, ctx: Any) -> None:
            self.remove_children()
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
            lines.append(f"[b]{tool.id}[/b]  {status}")
            lines.append("")
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

            if tool.alternatives:
                lines.append("")
                field("see also", ", ".join(tool.alternatives))

            self.mount(Static("\n".join(lines)))

    class InstallScreen(ModalScreen[bool]):
        """Runs a plan with real progress and a live log."""

        BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close")]

        DEFAULT_CSS = """
        InstallScreen { align: center middle; }
        #box {
            width: 78; height: 22;
            border: round $accent; background: $surface; padding: 1 2;
        }
        #title { text-style: bold; margin-bottom: 1; }
        #log { height: 1fr; border-top: solid $panel; margin-top: 1; }
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

        def on_mount(self) -> None:
            self._execute()

        def action_close(self) -> None:
            if self._done:
                self.dismiss(True)

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
            try:
                result = executor.run(self.plan)
                summary = (
                    f"[green]✓ {len(result.succeeded)} succeeded[/green]"
                    if result.ok
                    else f"[red]✗ {len(result.failures)} failed[/red], "
                    f"{len(result.succeeded)} ok"
                )
            except Exception as exc:
                summary = f"[red]✗ {exc}[/red]"
            self.app.call_from_thread(self._finish, summary)

        def _set_progress(self, percent: float, message: str) -> None:
            self.query_one("#bar", ProgressBar).update(progress=percent)
            self.query_one("#status", Static).update(f"[dim]{message[:70]}[/dim]")

        def _append_log(self, lines: list[str]) -> None:
            self.query_one("#logtext", Static).update("\n".join(lines))

        def _finish(self, summary: str) -> None:
            self._done = True
            self.query_one("#bar", ProgressBar).update(progress=100)
            self.query_one("#status", Static).update(
                f"{summary}\n[dim]Press Esc to close[/dim]"
            )

    class LoadoutBrowser(App):
        """Filter-first tool browser."""

        CSS = """
        Screen { layers: base; }
        #banner {
            height: auto;
            min-height: 4;
            color: $warning;
            text-style: bold;
            padding: 0 1;
            content-align: left middle;
        }
        #query  { border: none; border-bottom: solid $panel; height: 3; }
        #facets { width: 22; border-right: solid $panel; }
        #facets > ListView { height: 1fr; }
        #table  { height: 1fr; }
        #detail { height: 40%; border-top: solid $panel; padding: 0 1; }
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
            Binding("f5", "refresh", "Refresh"),
            Binding("down", "cursor_down", "", show=False),
            Binding("up", "cursor_up", "", show=False),
        ]

        marked: reactive[set[str]] = reactive(set)

        def __init__(self, ctx: Any) -> None:
            super().__init__()
            self.ctx = ctx
            self._facet: tuple[str, str] | None = None
            self._rows: list[Any] = []
            self.title = "loadout"

        # -- layout --------------------------------------------------------

        def compose(self) -> ComposeResult:
            if self.size.height >= 30:
                yield Static(WORDMARK, id="banner")
            yield Input(placeholder="Type to filter…  (Esc clears)", id="query")
            with Horizontal():
                with Vertical(id="facets"):
                    yield Label(" CATEGORIES", classes="facet-title")
                    yield ListView(id="facetlist")
                with Vertical():
                    yield DataTable(id="table", cursor_type="row", zebra_stripes=False)
                    yield ToolDetail(id="detail")
            yield Static("", id="hint")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#table", DataTable)
            table.add_column("", width=3, key="mark")
            table.add_column("TOOL", width=26, key="tool")
            table.add_column("SUMMARY", key="summary")
            table.add_column("VIA", width=22, key="via")

            facets = self.query_one("#facetlist", ListView)
            facets.append(ListItem(Label("all"), id="facet-all"))
            for slug, count in self.ctx.catalog.facet_values("category")[:18]:
                facets.append(
                    ListItem(Label(f"{slug}  [dim]{count}[/dim]"), id=f"facet-{slug}")
                )

            self.query_one("#query", Input).focus()
            self._reload()

        # -- data ----------------------------------------------------------

        def _reload(self) -> None:
            query = self.query_one("#query", Input).value.strip()
            categories = [self._facet[1]] if self._facet and self._facet[0] == "category" else []
            self._rows = self.ctx.catalog.search(query, categories=categories, limit=500)
            self._render_rows()

        def _render_rows(self) -> None:
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
                    tool.summary or "[dim]—[/dim]",
                    ", ".join(tool.providers) or "-",
                    key=tool.id,
                )
            self._update_hint()
            if self._rows:
                self._show_detail(self._rows[0])

        def _update_hint(self) -> None:
            installed = self.ctx.installed()
            shown = len(self._rows)
            here = sum(1 for t in self._rows if t.id in installed)
            marked = f"  ·  [reverse] {len(self.marked)} marked [/reverse]" if self.marked else ""
            self.query_one("#hint", Static).update(
                f"[dim]{shown} shown · {here} installed{marked}[/dim]"
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
            self.query_one("#detail", ToolDetail).update_tool(tool, self.ctx)

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

        @on(ListView.Selected, "#facetlist")
        def _on_facet(self, event: ListView.Selected) -> None:
            item_id = event.item.id or ""
            slug = item_id.removeprefix("facet-")
            self._facet = None if slug == "all" else ("category", slug)
            self._reload()

        # -- actions -------------------------------------------------------

        def action_clear_or_quit(self) -> None:
            query = self.query_one("#query", Input)
            if query.value:
                query.value = ""
                self._reload()
            elif self.marked:
                self.marked = set()
                self._render_rows()
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
            self.marked = marked
            self._render_rows()

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

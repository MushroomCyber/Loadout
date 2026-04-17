"""Minimal Textual app for Kali Tools Manager.

This is intentionally small: a single screen with a sidebar of
categories, a sortable DataTable of tools, and a right-side details
panel. More advanced behaviour (multi-select install, profile apply,
command palette actions) can be added incrementally.

The module imports ``textual`` lazily so the rest of the package still
works when the TUI extra is not installed.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

try:  # pragma: no cover - optional dependency
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Center, Horizontal, Middle, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import (
        DataTable,
        Footer,
        Header,
        Input,
        Static,
        Tree,
    )

    TEXTUAL_AVAILABLE = True
except Exception:  # pragma: no cover
    TEXTUAL_AVAILABLE = False


from ..manager import KaliToolsManager

# ---------------------------------------------------------------------------
# Cyber / Knight-Rider glyphs for the TUI progress display
# ---------------------------------------------------------------------------
_GLYPHS = "░▒▓█"
_SCANNER_W = 4


def _render_cyber_bar(pct: float, width: int = 40, tick: int = 0) -> str:
    """Return a Rich-markup string for the cyber progress bar."""
    filled = int(width * pct / 100)
    empty = width - filled

    chars = list(_GLYPHS[3] * filled + _GLYPHS[0] * empty)

    # Bouncing scanner highlight across filled region
    if filled > 1:
        cycle = max(filled - _SCANNER_W, 1) * 2
        pos = tick % cycle
        if pos >= cycle // 2:
            pos = cycle - pos - 1
        for i in range(_SCANNER_W):
            idx = pos + i
            if 0 <= idx < filled:
                chars[idx] = _GLYPHS[1] if i in (0, _SCANNER_W - 1) else _GLYPHS[2]

    parts: list[str] = []
    for idx, ch in enumerate(chars):
        if idx < filled:
            if ch == _GLYPHS[3]:
                parts.append(f"[bold cyan]{ch}[/]")
            elif ch == _GLYPHS[2]:
                parts.append(f"[bold bright_white]{ch}[/]")
            else:
                parts.append(f"[bold bright_cyan]{ch}[/]")
        else:
            parts.append(f"[dim bright_black]{ch}[/]")

    bar = "".join(parts)

    # Spinner frames
    frames = ["◜", "◠", "◝", "◞", "◡", "◟"]
    if pct >= 100:
        spinner = "[bold bright_green]◉[/]"
        pct_text = "[bold bright_green]【DONE】[/]"
    else:
        spinner = f"[bold red]{frames[tick % len(frames)]}[/]"
        pct_text = f"[bold cyan]〔{pct:5.1f}%〕[/]"

    return f"{spinner} [bold bright_black]⟨[/]{bar}[bold bright_black]⟩[/] {pct_text}"


def textual_available() -> bool:
    return TEXTUAL_AVAILABLE


if TEXTUAL_AVAILABLE:

    # ---- Modal install/uninstall screen ----------------------------------

    class _InstallModal(ModalScreen[bool]):
        """Centered overlay that runs apt install/remove with cyber progress."""

        CSS = """
        _InstallModal {
            align: center middle;
        }
        #modal-box {
            width: 64;
            height: 16;
            border: heavy $accent;
            background: $surface;
            padding: 1 2;
        }
        #modal-title {
            text-align: center;
            text-style: bold;
            color: $text;
            margin-bottom: 1;
        }
        #modal-bar {
            text-align: center;
            height: 3;
            content-align: center middle;
        }
        #modal-status {
            text-align: center;
            margin-top: 1;
        }
        """

        BINDINGS = [
            Binding("escape", "dismiss_modal", "Close", show=True),
            Binding("enter", "dismiss_modal", "Close", show=True),
        ]

        def __init__(
            self,
            manager: KaliToolsManager,
            package_name: str,
            removing: bool = False,
        ) -> None:
            super().__init__()
            self.manager = manager
            self.package_name = package_name
            self.removing = removing
            self._tick = 0
            self._done = False

        def compose(self) -> ComposeResult:
            action = "REMOVING" if self.removing else "INSTALLING"
            with Center():
                with Middle():
                    with Vertical(id="modal-box"):
                        yield Static(
                            f"[bold bright_cyan]《 {action} 》[/]\n"
                            f"[bold white]{self.package_name}[/]",
                            id="modal-title",
                        )
                        yield Static("", id="modal-bar")
                        yield Static(
                            "[dim]Waiting for apt...[/dim]",
                            id="modal-status",
                        )

        def on_mount(self) -> None:
            self._run_apt()

        def action_dismiss_modal(self) -> None:
            if self._done:
                self.dismiss(True)

        @work(thread=True)
        def _run_apt(self) -> None:
            """Run the apt subprocess in a background thread."""
            if self.removing:
                cmd = ['sudo', 'apt-get', 'remove', '-y', self.package_name]
            else:
                cmd = self.manager._build_apt_install_cmd(self.package_name)

            start = time.time()
            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except Exception as exc:
                self._post_update(0, f"[bold red]Error: {exc}[/]", done=True, success=False)
                return

            progress = 0
            line_count = 0
            for line in process.stdout:
                line_count += 1
                stripped = line.strip()

                # Parse apt stages for progress estimation
                if 'Reading package lists' in line:
                    progress = max(progress, 15)
                elif 'Building dependency tree' in line:
                    progress = max(progress, 25)
                elif 'Reading state information' in line:
                    progress = max(progress, 35)
                elif 'Need to get' in line or 'Get:' in line:
                    progress = max(progress, 45)
                elif 'Unpacking' in line or 'Selecting' in line:
                    progress = max(progress, 60)
                elif 'Removing' in line or 'Purging' in line:
                    progress = max(progress, 65)
                elif 'Setting up' in line or 'Preparing' in line:
                    progress = max(progress, 80)
                elif 'Processing triggers' in line:
                    progress = max(progress, 90)

                estimated = min(95, 5 + (line_count * 2))
                progress = max(progress, estimated)

                status_line = stripped[:55] if stripped else "Working..."
                self._post_update(progress, f"[dim]{status_line}[/dim]")

            process.wait()
            elapsed = time.time() - start
            success = process.returncode == 0

            if success:
                self._post_update(100, "", done=True, success=True)
                self._finalise(elapsed, success=True)
            else:
                action = "remove" if self.removing else "install"
                self._post_update(
                    progress,
                    f"[bold red]✗ Failed to {action} {self.package_name}[/]",
                    done=True,
                    success=False,
                )

        def _post_update(
            self, pct: float, status: str, *, done: bool = False, success: bool = False
        ) -> None:
            """Thread-safe UI update via call_from_thread."""
            self._tick += 1
            self.app.call_from_thread(self._apply_update, pct, status, done, success)

        def _apply_update(
            self, pct: float, status: str, done: bool, success: bool
        ) -> None:
            try:
                bar_widget = self.query_one("#modal-bar", Static)
                status_widget = self.query_one("#modal-status", Static)
            except Exception:
                return

            bar_widget.update(_render_cyber_bar(pct, width=50, tick=self._tick))

            if done:
                self._done = True
                if success:
                    action = "removed" if self.removing else "installed"
                    status_widget.update(
                        f"[bold bright_green]✓ {self.package_name} {action} successfully![/]\n"
                        "[dim]Press Enter or Esc to close[/dim]"
                    )
                else:
                    status_widget.update(
                        f"{status}\n[dim]Press Enter or Esc to close[/dim]"
                    )
            else:
                status_widget.update(status)

        def _finalise(self, elapsed: float, *, success: bool) -> None:
            """Update manager state after a successful operation."""
            from ..state import get_state_db

            pkg = self.package_name
            if self.removing:
                for t in self.manager.tools:
                    if t["name"] == pkg:
                        t["installed"] = False
                        t["size"] = 0
                self.manager.save_cache()
                self.manager._installed_cache = None
                try:
                    db = get_state_db()
                    db.set_installed(pkg, False)
                    db.record('uninstall', pkg, success=True,
                              detail=f'elapsed={elapsed:.1f}s')
                except Exception:
                    pass
            else:
                for t in self.manager.tools:
                    if t["name"] == pkg:
                        t["installed"] = True
                        t["size"] = self.manager.get_package_size(pkg)
                self.manager.save_cache()
                self.manager._installed_cache = None
                try:
                    db = get_state_db()
                    db.set_installed(pkg, True)
                    db.record('install', pkg, success=True,
                              detail=f'elapsed={elapsed:.1f}s')
                except Exception:
                    pass

    # ---- Widgets ---------------------------------------------------------

    class _ToolDetails(Static):
        def update_tool(self, tool: dict[str, Any] | None) -> None:
            if not tool:
                self.update("[dim]No tool selected[/dim]")
                return
            lines = [
                f"[b cyan]{tool['name']}[/b cyan]",
                "",
                f"[b]Category:[/b] {tool.get('category', 'other')}"
                f"  [b]Status:[/b] {'installed' if tool.get('installed') else 'available'}",
                "",
                tool.get("description") or "[dim]No description available.[/dim]",
            ]
            cmds = tool.get("commands") or []
            if cmds:
                lines.append("")
                lines.append(f"[b]Commands:[/b] {', '.join(cmds[:8])}")
            self.update("\n".join(lines))

    class KaliToolsTUI(App):
        """Textual front-end for :class:`KaliToolsManager`."""

        CSS = """
        Screen { layout: horizontal; }
        #sidebar { width: 28; border-right: heavy $primary; }
        #main { width: 1fr; }
        #details { height: 40%; border-top: heavy $primary; padding: 1 2; }
        DataTable { height: 60%; }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("/", "focus_search", "Search"),
            Binding("i", "install_selected", "Install/Remove"),
            Binding("r", "refresh", "Refresh"),
        ]

        def __init__(self, manager: KaliToolsManager) -> None:
            super().__init__()
            self.manager = manager
            self._current_category: str | None = None

        def compose(self) -> ComposeResult:  # type: ignore[override]
            yield Header(show_clock=False)
            with Horizontal():
                with Vertical(id="sidebar"):
                    yield Tree("Categories", id="cat_tree")
                with Vertical(id="main"):
                    yield Input(placeholder="Search tools (press /)", id="search_box")
                    yield DataTable(id="tool_table")
                    yield _ToolDetails(id="details")
            yield Footer()

        def on_mount(self) -> None:
            tree = self.query_one("#cat_tree", Tree)
            tree.root.expand()
            seen = set()
            for t in self.manager.tools:
                seen.add(t.get("category") or "other")
            for cat in sorted(seen):
                tree.root.add_leaf(cat, data=cat)

            table = self.query_one("#tool_table", DataTable)
            table.add_columns("Tool", "Category", "Size (MB)", "Status")
            table.cursor_type = "row"
            self._populate_table()

        def _populate_table(self, query: str = "", category: str | None = None) -> None:
            table = self.query_one("#tool_table", DataTable)
            table.clear()
            q = (query or "").lower().strip()
            for t in self.manager.tools:
                if category and (t.get("category") or "other") != category:
                    continue
                if q and q not in t["name"].lower() and q not in (t.get("description") or "").lower():
                    continue
                size_mb = (int(t.get("size") or 0)) / (1024 * 1024)
                status = "✓" if t.get("installed") else "·"
                table.add_row(
                    t["name"],
                    t.get("category") or "other",
                    f"{size_mb:0.1f}",
                    status,
                    key=t["name"],
                )

        # ----- actions --------------------------------------------------------
        def action_focus_search(self) -> None:
            self.query_one("#search_box", Input).focus()

        def action_refresh(self) -> None:
            self._populate_table()

        def action_install_selected(self) -> None:
            table = self.query_one("#tool_table", DataTable)
            if table.cursor_row is None:
                return
            row = table.get_row_at(table.cursor_row)
            name = row[0]
            tool = next((t for t in self.manager.tools if t["name"] == name), None)
            if not tool:
                return

            removing = bool(tool.get("installed"))
            modal = _InstallModal(self.manager, name, removing=removing)
            self.push_screen(modal, callback=lambda _: self._populate_table())

        # ----- events ---------------------------------------------------------
        @on(Input.Changed, "#search_box")
        def _on_search(self, event: Input.Changed) -> None:
            self._populate_table(query=event.value, category=self._current_category)

        @on(Tree.NodeSelected, "#cat_tree")
        def _on_category(self, event) -> None:  # noqa: ANN001
            data = getattr(event.node, "data", None)
            self._current_category = data
            self._populate_table(category=data)

        @on(DataTable.RowHighlighted, "#tool_table")
        def _on_row(self, event) -> None:  # noqa: ANN001
            key = event.row_key
            if key is None:
                return
            name = key.value if hasattr(key, "value") else str(key)
            tool = next((t for t in self.manager.tools if t["name"] == name), None)
            self.query_one("#details", _ToolDetails).update_tool(tool)

else:  # pragma: no cover - import-time error surface

    class KaliToolsTUI:  # type: ignore[no-redef]
        def __init__(self, *_: Any, **__: Any) -> None:
            raise RuntimeError(
                "Textual is not installed. Install with `pip install 'kalitools-app[tui]'`."
            )


def run_tui(manager: KaliToolsManager) -> int:
    if not TEXTUAL_AVAILABLE:
        raise RuntimeError(
            "Textual is not installed. Install with `pip install 'kalitools-app[tui]'`."
        )
    KaliToolsTUI(manager).run()
    return 0

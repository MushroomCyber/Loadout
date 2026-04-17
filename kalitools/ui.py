"""Terminal UI for Kali Tools CLI."""

from __future__ import annotations

import json
import select
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from rich import box
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import console
from .constants import (
    CATEGORY_ICONS,
    CATEGORY_NAMES,
    TOOL_DESCRIPTIONS,
    get_category_description,
    get_category_display_name,
    get_subcategory_for,
)
from .manager import KaliToolsManager
from .model import Tool

try:
    import termios
    import tty

    TERMIOS_AVAILABLE = True
except ImportError:
    termios = None  # type: ignore
    tty = None  # type: ignore
    TERMIOS_AVAILABLE = False


class ToolsUI:
    """Enhanced Terminal UI for Kali Tools Manager"""

    def __init__(self, manager: KaliToolsManager, ui_mode: str = "rich"):
        self.manager = manager
        self.settings = self._load_settings()

        self.ui_mode = ui_mode
        self.interactive_supported = TERMIOS_AVAILABLE and sys.stdin.isatty()
        self.basic_mode = (ui_mode == "basic") or not self.interactive_supported
        self.current_filter = 'all'
        self.search_query = ''
        self.current_category = None
        self.current_page = 1
        self.per_page = self.settings.get('per_page', 25)
        self.cursor_index = 0  # Highlighted row within current page
        self.recent_operations = []  # Recently modified tools
        self.breadcrumbs = []  # Navigation trail
        self.theme = self.settings.get('theme', 'default')
        self.terminal_width = console.width
        self.sort_mode = 'name'  # one of: name, installed, size, category
        self.should_exit = False
        self.status_message = None
        # Buffer for multi-digit numeric jumps in interactive list
        self._num_buf = ''
        self._size_executor = ThreadPoolExecutor(max_workers=4)
        self._size_futures: dict[str, Future[int]] = {}

    def flush_input_buffer(self):
        """Clear any pending keyboard input to avoid phantom key events."""
        if not TERMIOS_AVAILABLE:
            return
        try:
            while sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                sys.stdin.read(1)
        except Exception:
            pass

    def _load_settings(self) -> dict:
        """Load user settings"""
        settings_file = Path.home() / ".kali_tools_settings.json"
        default_settings = {
            'show_size': True,
            'show_descriptions': True,
            'confirm_install': True,
            'auto_update': False,
            'notifications': True,
            'theme': 'minimal',
            'per_page': 25
        }
        if settings_file.exists():
            try:
                with open(settings_file) as f:
                    return {**default_settings, **json.load(f)}
            except Exception:
                return default_settings
        return default_settings

    def _save_settings(self):
        """Save user settings"""
        settings_file = Path.home() / ".kali_tools_settings.json"
        try:
            from .manager import _atomic_write_json
            _atomic_write_json(settings_file, self.settings)
        except Exception as e:
            console.print(f"[yellow]Could not save settings: {e}[/yellow]")

    def update_setting(self, key: str, value):
        """Persist a single setting value"""
        self.settings[key] = value
        self._save_settings()

    def set_view(self, filter_type: str = 'all', category: str | None = None,
                 search: str | None = None, reset_page: bool = True):
        """Centralized helper to update filtering/search state"""
        self.current_filter = filter_type
        self.current_category = category
        self.search_query = '' if search is None else search
        if reset_page:
            self.current_page = 1
            self.cursor_index = 0

    def confirm_action(self, message: str, default: bool = False) -> bool:
        """Unified confirmation dialog."""
        try:
            return Confirm.ask(message, default=default)
        except KeyboardInterrupt:
            return False

    def get_filtered_tools(self) -> list[dict]:
        """Return tools list after applying current filter/search criteria"""
        if self.current_category:
            tools = self.manager.filter_by_category(self.current_category)
        elif self.current_filter == 'installed':
            tools = self.manager.filter_by_status(True)
        elif self.current_filter == 'available':
            tools = self.manager.filter_by_status(False)
        else:
            tools = self.manager.tools

        if self.search_query:
            query = self.search_query.lower()
            tools = [tool for tool in tools if query in tool['name'].lower() or
                     any(query in cmd.lower() for cmd in tool['commands'])]
        if self.sort_mode == 'name':
            tools.sort(key=lambda t: t['name'])
        elif self.sort_mode == 'installed':
            tools.sort(key=lambda t: (not t['installed'], t['name']))
        elif self.sort_mode == 'size':
            tools.sort(key=lambda t: getattr(t, 'size', 0), reverse=True)
        elif self.sort_mode == 'category':
            tools.sort(key=lambda t: (getattr(t, 'category', ''), t['name']))
        else:
            tools.sort(key=lambda t: t['name'])
        return tools

    def cycle_sort_mode(self):
        order = ['name', 'installed', 'size', 'category']
        try:
            idx = order.index(self.sort_mode)
            self.sort_mode = order[(idx + 1) % len(order)]
        except ValueError:
            self.sort_mode = 'name'
        # No need to set a transient status; sort is shown persistently below the table

    def open_tool_action(self, tool: dict[str, Any]):
        """Display tool details and handle install/uninstall workflow"""
        console.clear()
        self.show_tool_details(tool['name'])

        if tool['installed']:
            if not self.confirm_action(f"\n[red]Uninstall[/red] [bold]{tool['name']}[/bold]?", default=False):
                console.clear()
                return
            success = self.manager.uninstall_tool(tool['name'])
            if success:
                self.add_to_recent(tool['name'], 'uninstall')
                self.show_toast(f"{tool['name']} uninstalled successfully!", "success")
        else:
            if not self.confirm_action(f"\n[green]Install[/green] [bold]{tool['name']}[/bold]?", default=True):
                console.clear()
                return
            success = self.manager.install_tool(tool['name'])
            if success:
                self.add_to_recent(tool['name'], 'install')
                self.show_toast(f"{tool['name']} installed successfully!", "success")

        Prompt.ask("\n[dim]Press Enter to continue[/dim]", default="")
        console.clear()

    def categorize_tool(self, tool: dict[str, Any]):
        """Prompt the user to override category/subcategory assignments."""
        if not tool:
            return
        try:
            tool_name = tool['name']  # type: ignore[index]
        except Exception:
            tool_name = getattr(tool, 'name', '')
        if not tool_name:
            console.print("[red]Tool name not available for categorization[/red]")
            time.sleep(1)
            return

        current_category = (tool.get('category') if isinstance(tool, dict) else getattr(tool, 'category', 'other')) or 'other'
        current_category = current_category.lower()
        current_sub = self.get_subcategory_for_tool(tool)

        console.print(f"[bold cyan]Categorize Tool:[/bold cyan] [white]{tool_name}[/white]\n")
        console.print("0) Auto (let Kali Tools decide)")
        category_slugs = list(CATEGORY_NAMES.keys())
        for idx, slug in enumerate(category_slugs, start=1):
            icon = CATEGORY_ICONS.get(slug, '🧰')
            label = CATEGORY_NAMES.get(slug, slug.title())
            marker = "*" if slug == current_category else " "
            console.print(f"{idx:>2}) {icon} {label} [{slug}]{'  ← current' if marker == '*' else ''}")

        default_choice = (
            str(category_slugs.index(current_category) + 1)
            if current_category in category_slugs
            else '0'
        )
        choice = Prompt.ask(
            "[cyan]Select category number or name ('auto' to clear)[/cyan]",
            default=default_choice,
        ).strip()

        selected_category: str | None
        if not choice or choice.lower() in ('0', 'auto'):
            selected_category = None
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(category_slugs):
                selected_category = category_slugs[idx]
            else:
                console.print("[yellow]Invalid category number[/yellow]")
                time.sleep(1)
                return
        else:
            slug = choice.lower()
            if slug in CATEGORY_NAMES:
                selected_category = slug
            else:
                console.print("[yellow]Unknown category name[/yellow]")
                time.sleep(1)
                return

        if selected_category is None:
            self.manager.set_tool_category_override(tool_name, None, None)
            self.status_message = f"[cyan]Reverted {tool_name} to automatic categorization[/cyan]"
        else:
            sub_default = current_sub or 'auto'
            sub_response = Prompt.ask(
                "[cyan]Subcategory (blank/auto = infer, '-' = clear)[/cyan]",
                default=sub_default,
            ).strip()
            if not sub_response or sub_response.lower() == 'auto':
                sub_override = ''
            elif sub_response == '-':
                sub_override = ''
            else:
                sub_override = sub_response

            self.manager.set_tool_category_override(tool_name, selected_category, sub_override)
            display_name = get_category_display_name(selected_category)
            if sub_override:
                label = f"{display_name}/{sub_override}"
            else:
                label = display_name
            self.status_message = f"[green]Saved category {label} for {tool_name}[/green]"

        console.print("\n[dim]Press Enter to continue...[/dim]")
        input()

    def add_to_recent(self, tool_name: str, operation: str):
        """Add tool to recent operations"""
        self.recent_operations.insert(0, {'tool': tool_name, 'operation': operation, 'time': datetime.now()})
        self.recent_operations = self.recent_operations[:10]  # Keep last 10

    def show_toast(self, message: str, style: str = "green"):
        """Show notification toast"""
        symbols = {
            'success': '✅',
            'error': '❌',
            'warning': '⚠️',
            'info': 'ℹ️'
        }
        symbol = symbols.get(style, '✅')
        console.print(f"\n[{style}]┌{'─' * (len(message) + 4)}┐[/{style}]")
        console.print(f"[{style}]│ {symbol} {message} │[/{style}]")
        console.print(f"[{style}]└{'─' * (len(message) + 4)}┘[/{style}]\n")

    def show_utilities_menu(self):
        """Expose export/import/backup helpers in both UI modes."""
        while True:
            console.print("\n[bold cyan]Utilities Menu[/bold cyan]")
            console.print("1) Export installed tools list")
            console.print("2) Import tools list")
            console.print("3) Create dpkg backup snapshot")
            console.print("4) Configure local apt repository")
            console.print("Q) Back")
            choice = Prompt.ask("Select option", choices=["1", "2", "3", "4", "Q"], default="Q")
            if choice == "1":
                default_name = f"installed_tools_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                path = Prompt.ask("Export file path", default=str(Path.cwd() / default_name))
                self.manager.config_manager.export_tools_list(path)
            elif choice == "2":
                path = Prompt.ask("Import file path", default=str(Path.cwd() / 'tools.json'))
                names = self.manager.config_manager.import_tools_list(path)
                if names:
                    console.print(f"[cyan]Imported {len(names)} tools from {path}[/cyan]")
            elif choice == "3":
                self.manager.create_backup()
            elif choice == "4":
                repo = Prompt.ask("Absolute path to local repo", default=str(Path.home()))
                self.manager.setup_local_repo(repo)
            else:
                break

    def get_breadcrumb(self) -> str:
        """Generate breadcrumb navigation"""
        parts = ['🔧 Kali Tools']

        if self.current_category:
            icon = CATEGORY_ICONS.get(self.current_category, '📦')
            parts.append(f"{icon} {self.current_category.title()}")

        if self.search_query:
            parts.append(f"🔍 Search: '{self.search_query}'")

        if self.current_filter == 'installed':
            parts.append("🟢 Installed")
        elif self.current_filter == 'available':
            parts.append("⭕ Available")

        if self.current_page > 1:
            parts.append(f"📄 Page {self.current_page}")

        return " > ".join(parts)

    def build_statistics_bar_text(self) -> Text:
        """Return the rich text representation of the statistics bar."""
        stats = self.manager.get_statistics()
        total = stats['total']
        installed = stats['installed']
        percentage = round((installed / total * 100), 1) if total > 0 else 0

        total_size_mb = stats.get('total_size_mb', 0)
        size_str = f"{total_size_mb:.1f} GB" if total_size_mb > 1024 else f"{total_size_mb:.0f} MB"

        stats_parts = [
            f"📦 [cyan]{total}[/cyan] Total",
            f"🟢 [green]{installed}[/green] Installed ([yellow]{percentage}%[/yellow])",
        ]

        if total_size_mb > 0:
            stats_parts.append(f"💾 [magenta]{size_str}[/magenta] Used")

        return Text.from_markup(" | ".join(stats_parts))

    def get_context_hint(self) -> str:
        """Return a helpful hint based on the current browsing mode (no L/I/A filters)."""
        if self.search_query:
            return f"[dim]💡 Searching for '{self.search_query}' — Press S to refine, B to show all[/dim]"
        elif self.current_category:
            return f"[dim]💡 Viewing {self.current_category.upper()} category[/dim]"
        elif self.current_filter == 'installed':
            return "[dim]💡 Showing installed tools only[/dim]"
        elif self.current_filter == 'available':
            return "[dim]💡 Showing available tools only[/dim]"
        else:
            return "[dim]💡 Tip: Press ? for full shortcuts[/dim]"

    def build_button_bar(self):
        """Render a compact, visually grouped command bar as button-like chips."""
        table = Table(show_header=False, box=box.SIMPLE, expand=False, pad_edge=False)
        for _ in range(3):
            table.add_column(justify="left", no_wrap=True)

        def btn(key: str, label: str, color: str) -> str:
            """Return a small button-like chip for a key/label pair.

            Use simple brackets instead of nested Rich tags to avoid
            markup parsing errors.
            """
            return f"[{color}][[/]{key}[{color}]][/][white] {label}[/white]"

        # Single-line command bar
        table.add_row(
            btn("C", "Categorize", "cyan"),
            btn("O", "Sort", "yellow")
            + "   " + btn("R", "Scan", "green")
            + "   " + btn("U", "Updates", "green"),
            btn("S", "Search", "blue")
            + "   " + btn("B", "Show All", "yellow")
            + "   " + btn("Y", "Utilities", "magenta")
            + "   " + btn("?", "Help", "red")
            + "   " + btn("Q", "Exit", "red"),
        )
        return table

    def get_subcategory_for_tool(self, tool: dict[str, Any]) -> str:
        """Return display subcategory for a tool row, resilient to Tool/dataclass or dict types."""
        try:
            subcat = tool['subcategory']  # type: ignore[index]
        except Exception:
            subcat = getattr(tool, 'subcategory', '')
        if subcat:
            return subcat
        try:
            name = tool['name']  # type: ignore[index]
            category = tool['category']  # type: ignore[index]
        except Exception:
            name = getattr(tool, 'name', '')
            category = getattr(tool, 'category', None)
        try:
            return get_subcategory_for(name, category)
        except Exception:
            return ''

    def format_description(self, text: str, max_len: int = 80) -> str:
        """Compact a description to a single sentence (or truncate) for table display.

        - Keep fallback markup like '[dim]No description[/dim]' untouched.
        - Take first sentence terminator among ., !, ? if within max_len.
        - Otherwise hard truncate to max_len and append an ellipsis if trimmed.
        """
        if not text or text.startswith('[dim]'):
            return text
        raw = text.strip()
        import re as _re
        m = _re.search(r'[.!?]', raw)
        if m and m.end() <= max_len:
            compact = raw[:m.end()].strip()
        else:
            compact = raw[:max_len].rstrip()
            if len(raw) > max_len:
                compact += '…'
        return compact

    @staticmethod
    def format_size(size_bytes: int | None) -> str:
        """Return human-readable size (bytes -> KB/MB/GB)."""
        try:
            size = float(size_bytes or 0)
        except (TypeError, ValueError):
            size = 0.0
        if size <= 0:
            return "—"
        units = ["B", "KB", "MB", "GB", "TB"]
        idx = 0
        while size >= 1024 and idx < len(units) - 1:
            size /= 1024
            idx += 1
        if idx == 0:
            return f"{int(size)} {units[idx]}"
        if size >= 100:
            return f"{size:.0f} {units[idx]}"
        return f"{size:.1f} {units[idx]}"

    def ensure_tool_size(self, tool: Any) -> int:
        """Return cached size for a tool, populating asynchronously if missing."""
        size = 0
        try:
            size = int(tool.get('size', 0))  # type: ignore[attr-defined]
        except AttributeError:
            size = int(getattr(tool, 'size', 0) or 0)
        except (TypeError, ValueError):
            size = 0
        if size:
            return size
        name = ''
        try:
            name = tool['name']  # type: ignore[index]
        except Exception:
            name = getattr(tool, 'name', '')
        if not name:
            return 0
        future = self._size_futures.get(name)
        if future:
            if future.done():
                try:
                    size = future.result() or 0
                except Exception:
                    size = 0
                finally:
                    self._size_futures.pop(name, None)
                if not size:
                    return 0
            else:
                return 0
        else:
            if len(self._size_futures) < 64:
                self._size_futures[name] = self._size_executor.submit(self.manager.get_package_size, name)
            return 0
        try:
            tool['size'] = size  # type: ignore[index]
        except Exception:
            try:
                tool.size = size
            except Exception:
                pass
        return size


    def handle_updates(self):
        """Interactive updates menu.

        Options:
          1. Search updates for installed tools (existing behaviour).
          2. Search for new tools (refresh JSON and tool list).
        """

        while True:
            console.clear()
            console.print("[bold cyan]Updates Menu[/bold cyan]\n")
            console.print("[1] Search updates for installed tools")
            console.print("[2] Search for new tools (refresh tool list)")
            console.print("[Q] Back to main list\n")

            choice = input().strip().upper()
            if choice == '1':
                console.clear()
                console.print("\n[yellow]🔄 Checking for upgradable tools...[/yellow]\n")

                columns = [
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(bar_width=None),
                    TimeElapsedColumn(),
                ]
                with Progress(*columns, transient=True) as progress_bar:
                    task_id = progress_bar.add_task("Refreshing package lists...", total=3)

                    def _on_progress(message: str, completed: int, total: int, _tid=task_id, _bar=progress_bar):
                        _bar.update(_tid, description=message, completed=completed, total=total)

                    upgradable = self.manager.check_updates(_on_progress)
                if upgradable:
                    console.print(f"\n[cyan]🔄 Upgradable tools ({len(upgradable)}):[/cyan]")
                    for tool in upgradable:
                        console.print(f"  • {tool}")
                    if self.confirm_action("\nUpgrade all upgradable tools now?", default=False):
                        subprocess.run(['sudo', 'apt-get', 'upgrade', '-y'])
                        self.status_message = "[green]✅ Upgrade task triggered[/green]"
                else:
                    console.print("\n[green]✅ All tools are up to date![/green]")
                    self.status_message = "[green]All tools are up to date[/green]"

                console.print("\n[dim]Press any key to return to the updates menu...[/dim]")
                input()

            elif choice == '2':
                console.clear()
                console.print("\n[yellow]🔍 Searching for new tools and refreshing JSON data...[/yellow]\n")

                def _task_refresh():
                    # Let the manager refresh and then compute new tool names
                    before = {t['name'] for t in self.manager.tools}
                    added_count = self.manager.refresh_tools_from_sources()
                    after = {t['name'] for t in self.manager.tools}
                    new_names = sorted(after - before)
                    return added_count, new_names

                added, new_names = self.run_knight_rider(_task_refresh, label="Discovering tools")
                console.print(f"\n[green]✅ Tool list refreshed. {added} new tool(s) detected.[/green]")
                if new_names:
                    console.print("\n[bold cyan]New tools detected:[/bold cyan]")
                    for name in new_names:
                        console.print(f"  • {name}")

                    # Offer to install all or remove from list
                    console.print("\n[bold yellow]Options:[/bold yellow] 1) Install all new tools  2) Remove a tool from list  3) Continue")
                    choice = Prompt.ask("[cyan]Select option[/cyan]", choices=["1","2","3"], default="3")
                    if choice == "1":
                        for name in new_names:
                            self.manager.install_tool(name)
                        self.status_message = "[green]Installation requested for all new tools[/green]"
                    elif choice == "2":
                        to_remove = Prompt.ask("[cyan]Enter tool name to remove[/cyan]", default=new_names[0])
                        self.manager.remove_tool_from_list(to_remove)
                        self.status_message = f"[yellow]Removed {to_remove} from tool list[/yellow]"
                else:
                    self.status_message = "[green]Tool list updated from sources[/green]"

                console.print("\n[dim]Press any key to return to the updates menu...[/dim]")
                input()

            elif choice == 'Q':
                break

    def show_statistics_bar(self):
        """Show compact statistics dashboard at top"""
        console.print(self.build_statistics_bar_text())
        console.print()

    def show_banner(self):
        """Display awesome cybersecurity-themed banner"""
        banner = """
[bold red]        ██╗  ██╗ █████╗ ██╗     ██╗[/bold red]    [bold white]████████╗ ██████╗  ██████╗ ██╗     ███████╗[/bold white]
[bold red]        ██║ ██╔╝██╔══██╗██║     ██║[/bold red]    [bold white]╚══██╔══╝██╔═══██╗██╔═══██╗██║     ██╔════╝[/bold white]
[bold red]        █████╔╝ ███████║██║     ██║[/bold red]       [bold white]██║   ██║   ██║██║   ██║██║     ███████╗[/bold white]
[bold red]        ██╔═██╗ ██╔══██║██║     ██║[/bold red]       [bold white]██║   ██║   ██║██║   ██║██║     ╚════██║[/bold white]
[bold red]        ██║  ██╗██║  ██║███████╗██║[/bold red]       [bold white]██║   ╚██████╔╝╚██████╔╝███████╗███████║[/bold white]
[bold red]        ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝[/bold red]       [bold white]╚═╝    ╚═════╝  ╚═════╝ ╚══════╝╚══════╝[/bold white]
        """

        console.print(banner)

    def get_banner_text(self) -> str:
        return (
            "\n"
            "[bold red]        ██╗  ██╗ █████╗ ██╗     ██╗[/bold red]    [bold white]████████╗ ██████╗  ██████╗ ██╗     ███████╗[/bold white]\n"
            "[bold red]        ██║ ██╔╝██╔══██╗██║     ██║[/bold red]    [bold white]╚══██╔══╝██╔═══██╗██╔═══██╗██║     ██╔════╝[/bold white]\n"
            "[bold red]        █████╔╝ ███████║██║     ██║[/bold red]       [bold white]██║   ██║   ██║██║   ██║██║     ███████╗[/bold white]\n"
            "[bold red]        ██╔═██╗ ██╔══██║██║     ██║[/bold red]       [bold white]██║   ██║   ██║██║   ██║██║     ╚════██║[/bold white]\n"
            "[bold red]        ██║  ██╗██║  ██║███████╗██║[/bold red]       [bold white]██║   ╚██████╔╝╚██████╔╝███████╗███████║[/bold white]\n"
            "[bold red]        ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝[/bold red]       [bold white]╚═╝    ╚═════╝  ╚═════╝ ╚══════╝╚══════╝[/bold white]\n"
        )

    def run_knight_rider(self, func, label: str = "Working", width: int = 20):
        """Run a function while displaying a Knight Rider-style progress bar.

        The function `func` is called once; this helper animates a moving
        highlight across a dim bar until `func` returns, then clears the bar
        line and returns the function's result.
        """
        # Simple inline animation loop; not threaded to keep behaviour
        # deterministic in this TUI.
        pos = 0
        direction = 1
        bar_template = "[dim]{left}[/dim][bright]{mid}[/bright][dim]{right}[/dim]"

        # Run the task and animate in small steps
        import threading

        result_container = {"done": False, "value": None}

        def _runner():
            result_container["value"] = func()
            result_container["done"] = True

        t = threading.Thread(target=_runner, daemon=True)
        t.start()

        try:
            while not result_container["done"]:
                left = "■" * pos
                mid = "■"
                right = "■" * max(0, width - pos - 1)
                bar = bar_template.format(left=left, mid=mid, right=right)
                console.print(f"[dim]{label}:[/dim] {bar}", end="\r")
                time.sleep(0.06)
                pos += direction
                if pos == 0 or pos == width - 1:
                    direction *= -1
        finally:
            t.join(timeout=0.1)
            # Clear the line after completion
            console.print(" " * (width + len(label) + 10), end="\r")

        return result_container["value"]

    def list_tools_interactive(self):
        """Interactive tool browser with real-time keyboard navigation"""
        if not TERMIOS_AVAILABLE:
            console.print("[yellow]Interactive mode requires termios-compatible terminal. Falling back to basic mode.[/yellow]")
            self.basic_mode = True
            self.run_basic_mode()
            return
        console.clear()
        self.flush_input_buffer()

        needs_render = True
        tools: list[Any] = []
        page_tools: list[Any] = []
        total_tools = 0
        total_pages = 1
        per_page_effective = 1
        start_idx = 0
        end_idx = 0

        while True:
            if needs_render:
                tools = self.get_filtered_tools()
                total_tools = len(tools)

                terminal_size = console.size
                self.terminal_width = terminal_size.width
                terminal_height = terminal_size.height

                reserved_rows = 10
                max_rows = max(5, terminal_height - reserved_rows)
                per_page_effective = max(1, min(self.per_page, max_rows))

                header_items = [
                    Text.from_markup(self.get_banner_text()),
                ]  # type: ignore[list-item]

                breadcrumb = self.get_breadcrumb()
                header_items.append(Text.from_markup(f"[dim]{breadcrumb}[/dim]"))
                if self.current_category:
                    desc = get_category_description(self.current_category)
                    if desc:
                        header_items.append(Text.from_markup(f"[dim]{desc}[/dim]"))

                if total_tools == 0:
                    header_items.append(Panel(
                        "[yellow]🔍 No tools found![/yellow]\n\n"
                        "[dim]💡 Press 'S' to search or 'B' to show all tools[/dim]",
                        title="Empty Results",
                        border_style="yellow"
                    ))
                    header_items.append(Text(""))
                    header_items.append(Text.from_markup("[dim]Press any key to continue...[/dim]"))

                    console.clear()
                    for item in header_items:
                        console.print(item)
                    input()
                    self.set_view('all')
                    needs_render = True
                    continue

                total_pages = max(1, (total_tools + per_page_effective - 1) // per_page_effective)
                self.current_page = max(1, min(self.current_page, total_pages))

                start_idx = (self.current_page - 1) * per_page_effective
                end_idx = min(start_idx + per_page_effective, total_tools)
                page_tools = tools[start_idx:end_idx]

                if self.cursor_index >= len(page_tools):
                    self.cursor_index = max(0, len(page_tools) - 1)

                table = Table(box=box.ROUNDED, show_lines=False)

                name_w, status_w, size_w, cat_w = self.get_column_widths()
                name_w = max(20, name_w - 4)
                table.add_column("#", style="yellow bold", width=5, justify="right")
                table.add_column("Tool Name", style="cyan bold", width=name_w)
                table.add_column("Status", width=status_w, justify="center")
                table.add_column("Size", width=size_w, justify="right")
                table.add_column("Category", style="magenta", width=cat_w)
                table.add_column("Subcategory", style="blue", width=20)

                for local_idx, tool in enumerate(page_tools):
                    display_index = start_idx + local_idx + 1
                    status = "[green]🟢 Installed[/green]" if tool['installed'] else "[red]⭕ Available[/red]"
                    subcat = self.get_subcategory_for_tool(tool)
                    size_cached = tool.get('size') if isinstance(tool, dict) else getattr(tool, 'size', None)
                    if size_cached:
                        size_text = self.format_size(size_cached)
                    else:
                        size_text = self.format_size(self.ensure_tool_size(tool))
                    row = [
                        str(display_index),
                        tool['name'],
                        status,
                        size_text,
                        (tool['category'] or 'other'),
                        subcat,
                    ]
                    row_style = "reverse" if local_idx == self.cursor_index else None
                    table.add_row(*row, style=row_style)

                body_items = header_items + [table, Text("")]

                progress_bar = "■" * self.current_page + "□" * (total_pages - self.current_page)
                if total_pages <= 20:
                    status_line = f"[dim]Page [{progress_bar}] {self.current_page}/{total_pages} | Showing {start_idx + 1}-{end_idx} of {total_tools} tools[/dim]"
                else:
                    percentage = round((self.current_page / total_pages) * 100)
                    status_line = f"[dim]Page {self.current_page}/{total_pages} ({percentage}%) | Showing {start_idx + 1}-{end_idx} of {total_tools} tools[/dim]"
                nav_hint = Text.from_markup(
                    "[cyan][[/]↑↓/←/→[cyan]][/][white] Navigate[/white]   "
                    "[cyan][[/]ENTER[cyan]][/][white] Details[/white]"
                )
                body_items.append(nav_hint)
                body_items.append(Text.from_markup(status_line))

                sort_line = f"[dim]Sort: [cyan]{self.sort_mode}[/cyan] | Filter: "
                if self.current_filter == 'installed':
                    sort_line += "[green]installed[/green]"
                elif self.current_filter == 'available':
                    sort_line += "[red]available[/red]"
                else:
                    sort_line += "all tools"
                body_items.append(Text.from_markup(sort_line))

                if self.status_message:
                    body_items.append(Text.from_markup(self.status_message))
                    self.status_message = None

                body_items.append(Text(""))
                button_bar = self.build_button_bar()
                body_items.append(button_bar)

                console.clear()
                for item in body_items:
                    console.print(item)
                needs_render = False

            # Pre-loop auto-jump check (in case buffer aged while rendering)
            if getattr(self, '_num_buf', '') and getattr(self, '_num_buf_ts', 0):
                try:
                    if (time.time() - self._num_buf_ts) >= 0.4:
                        gi = int(self._num_buf) - 1
                        if 0 <= gi < total_tools:
                            new_page = (gi // per_page_effective) + 1
                            self.current_page = max(1, min(new_page, total_pages))
                            start_idx = (self.current_page - 1) * per_page_effective
                            self.cursor_index = max(0, min(gi - start_idx, per_page_effective - 1))
                            self.status_message = f"[cyan]Jumped to #{gi + 1}[/cyan]"
                        else:
                            self.status_message = f"[yellow]Invalid index: {self._num_buf}[/yellow]"
                        self._num_buf = ''
                        needs_render = True
                except Exception:
                    self._num_buf = ''
                    needs_render = True

            key = None
            while key is None:
                # Auto-execute numeric jump after short pause without needing ENTER
                if self._num_buf:
                    try:
                        if (time.time() - getattr(self, '_num_buf_ts', 0)) >= 0.4:
                            global_idx = int(self._num_buf) - 1
                            if 0 <= global_idx < total_tools:
                                new_page = (global_idx // per_page_effective) + 1
                                self.current_page = max(1, min(new_page, total_pages))
                                start_idx = (self.current_page - 1) * per_page_effective
                                self.cursor_index = max(0, min(global_idx - start_idx, per_page_effective - 1))
                                self.status_message = f"[cyan]Jumped to #{global_idx + 1}[/cyan]"
                            else:
                                self.status_message = f"[yellow]Invalid index: {self._num_buf}[/yellow]"
                            self._num_buf = ''
                            key = 'REDRAW'
                            needs_render = True
                            break
                    except Exception:
                        self._num_buf = ''
                        key = 'REDRAW'
                        needs_render = True
                        break

                # Read keyboard input - switch to raw mode temporarily
                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    # Check if input is available
                    if sys.stdin in select.select([sys.stdin], [], [], 0.05)[0]:
                        ch = sys.stdin.read(1)

                        if ch == '\x1b':  # ESC sequence
                            # Read the rest of the escape sequence
                            seq = sys.stdin.read(2)
                            if len(seq) == 2 and seq[0] == '[':
                                arrow_map = {'A': 'UP', 'B': 'DOWN', 'C': 'RIGHT', 'D': 'LEFT'}
                                key = arrow_map.get(seq[1], 'ESC')
                            else:
                                key = 'ESC'
                        elif ch in ('\r', '\n'):
                            key = 'ENTER'
                        elif ch == '\x7f':
                            key = 'BACKSPACE'
                        elif ch == '\x03':  # Ctrl+C
                            key = 'Q'  # Treat as quit
                        else:
                            key = ch.upper()
                finally:
                    # Always restore terminal settings
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

            # Numeric entry buffer: type multi-digit index then press ENTER to jump
            if key and key.isdigit():
                self._num_buf += key
                self._num_buf_ts = time.time()
                self.status_message = f"[cyan]Target index: #{self._num_buf}[/cyan]"
                needs_render = True
                continue
            if key == 'ENTER' and self._num_buf:
                try:
                    global_idx = int(self._num_buf) - 1
                    if 0 <= global_idx < total_tools:
                        new_page = (global_idx // per_page_effective) + 1
                        self.current_page = max(1, min(new_page, total_pages))
                        start_idx = (self.current_page - 1) * per_page_effective
                        self.cursor_index = max(0, min(global_idx - start_idx, per_page_effective - 1))
                        self.status_message = f"[cyan]Jumped to #{global_idx + 1}[/cyan]"
                    else:
                        self.status_message = f"[yellow]Invalid index: {self._num_buf}[/yellow]"
                except Exception:
                    self.status_message = f"[yellow]Invalid input: {self._num_buf}[/yellow]"
                finally:
                    self._num_buf = ''
                    needs_render = True
                continue
            # Any non-digit key clears the buffer silently
            if self._num_buf and (not key or not key.isdigit()):
                self._num_buf = ''

            if key in ('UP', 'K'):
                if self.cursor_index > 0:
                    self.cursor_index -= 1
                elif self.current_page > 1:
                    self.current_page -= 1
                    self.cursor_index = min(per_page_effective - 1, len(page_tools) - 1)
                needs_render = True

            elif key in ('DOWN', 'J'):
                if self.cursor_index < len(page_tools) - 1:
                    self.cursor_index += 1
                elif self.current_page < total_pages:
                    self.current_page += 1
                    self.cursor_index = 0
                needs_render = True

            elif key == 'ENTER':
                tool = page_tools[self.cursor_index]
                console.clear()
                self.show_tool_details(tool['name'])
                needs_render = True

            elif key == 'I':
                tool = page_tools[self.cursor_index]
                console.clear()
                self.open_tool_action(tool)
                needs_render = True

            elif key == 'D':
                tool = page_tools[self.cursor_index]
                console.clear()
                self.show_tool_details(tool['name'])
                needs_render = True

            elif key == 'C':
                tool = page_tools[self.cursor_index]
                console.clear()
                self.categorize_tool(tool)
                needs_render = True

            elif key in ('N', 'RIGHT'):
                if self.current_page < total_pages:
                    self.current_page += 1
                    self.cursor_index = 0
                    needs_render = True

            elif key in ('P', 'LEFT'):
                if self.current_page > 1:
                    self.current_page -= 1
                    self.cursor_index = 0
                    needs_render = True

            elif key == 'S':

                def do_search():
                    console.clear()
                    query = Prompt.ask("[cyan]🔍 Enter search query (blank to clear)[/cyan]", default="")
                    self.set_view(self.current_filter, self.current_category, query or None)

                do_search()
                needs_render = True

            elif key == 'B':

                def clear_filters():
                    self.set_view('all', None, None)

                clear_filters()
                self.status_message = "[cyan]Showing all tools[/cyan]"
                needs_render = True

            elif key == 'U':

                def updates_action():
                    console.clear()
                    self.handle_updates()
                    console.print("\n[dim]Press any key to continue...[/dim]")
                    input()

                updates_action()
                needs_render = True

            elif key == 'R':

                def scan_action():
                    console.clear()
                    console.print("\n[yellow]🔍 Starting system scan...[/yellow]")
                    installed, total = self.manager.scan_all_tools()
                    self.show_toast(f"Scan complete! Found {installed}/{total} installed", "success")
                    time.sleep(1)

                scan_action()
                needs_render = True

            elif key == 'Y':

                def utilities_action():
                    console.clear()
                    self.show_utilities_menu()
                    console.print("\n[dim]Press any key to continue...[/dim]")
                    input()

                utilities_action()
                needs_render = True
            elif key == '?':

                def help_action():
                    console.clear()
                    self.show_help()
                    console.print("\n[dim]Press any key to continue...[/dim]")
                    input()

                help_action()
                needs_render = True

            elif key == 'O':
                self.cycle_sort_mode()
                needs_render = True

            elif key in ('Q', 'ESC'):
                console.clear()
                if self.confirm_action("\n[yellow]Exit Kali Tools Manager?[/yellow]", default=True):
                    self.should_exit = True
                    break
                self.status_message = "[yellow]Exit cancelled[/yellow]"
                console.clear()

        console.clear()

    def show_tool_details(self, tool_name: str, interactive: bool = True):
        """Show enhanced tool details with apt-cache package information."""
        tool = next((t for t in self.manager.tools if t['name'] == tool_name), None)
        if not tool:
            console.print(f"[red]Tool '{tool_name}' not found![/red]")
            return

        status_color = "green" if tool['installed'] else "red"
        status_text = "✓ Installed" if tool['installed'] else "✗ Not Installed"

        # Build comprehensive tool details including package info
        info = f"[bold cyan]Package Name:[/bold cyan] {tool['name']}\n"
        info += f"[bold cyan]Category:[/bold cyan] {tool['category'] or 'other'}\n"
        info += f"[bold cyan]Status:[/bold cyan] [{status_color}]{status_text}[/{status_color}]\n"

        # Get full apt-cache information
        full_info = self.manager.get_tool_info(tool['name'])
        description_lines = []

        if full_info:
            # Parse key fields from apt-cache show
            lines = full_info.split('\n')
            for line in lines:
                if line.startswith('Version:'):
                    info += f"[bold cyan]{line.split(':', 1)[0]}:[/bold cyan] {line.split(':', 1)[1].strip()}\n"
                elif line.startswith('Installed-Size:'):
                    info += f"[bold cyan]{line.split(':', 1)[0]}:[/bold cyan] {line.split(':', 1)[1].strip()}\n"
                elif line.startswith('Maintainer:'):
                    info += f"[bold cyan]{line.split(':', 1)[0]}:[/bold cyan] {line.split(':', 1)[1].strip()}\n"
                elif line.startswith('Homepage:'):
                    info += f"[bold cyan]{line.split(':', 1)[0]}:[/bold cyan] {line.split(':', 1)[1].strip()}\n"
                elif line.startswith('Section:'):
                    info += f"[bold cyan]{line.split(':', 1)[0]}:[/bold cyan] {line.split(':', 1)[1].strip()}\n"
                elif line.startswith('Priority:'):
                    info += f"[bold cyan]{line.split(':', 1)[0]}:[/bold cyan] {line.split(':', 1)[1].strip()}\n"

            # Extract FULL description from apt-cache (not truncated)
            desc_started = False
            for line in lines:
                if line.startswith('Description:') or line.startswith('Description-en:'):
                    desc_started = True
                    desc_text = line.split(':', 1)[1].strip()
                    if desc_text:
                        description_lines.append(desc_text)
                elif desc_started:
                    if line.startswith(' '):
                        # Continuation line
                        description_lines.append(line.strip())
                    elif line and not line.startswith(' '):
                        # End of description (next field started)
                        break

        # Add commands count
        if tool['commands']:
            info += f"[bold cyan]Number of Commands:[/bold cyan] {len(tool['commands'])}\n"

        # Add FULL description at the top (right after metadata)
        if description_lines:
            full_desc = ' '.join(description_lines)
            info += f"\n[bold cyan]Description:[/bold cyan]\n{full_desc}\n"
        else:
            # Fallback to cached or hardcoded description if apt-cache didn't provide one
            cached_desc = self.manager.get_cached_description(tool['name'])
            if cached_desc:
                info += f"\n[bold cyan]Description:[/bold cyan]\n{cached_desc}\n"
            else:
                fallback_desc = TOOL_DESCRIPTIONS.get(tool['name'])
                if fallback_desc:
                    info += f"\n[bold cyan]Description:[/bold cyan]\n{fallback_desc}\n"

        # Show sub-packages if available
        subpackages = getattr(tool, 'subpackages', []) or []
        if subpackages:
            info += f"\n[bold cyan]Related Packages ({len(subpackages)}):[/bold cyan]\n"
            for subpkg in subpackages[:10]:  # Limit display to 10
                sub_installed = self.manager.check_installation(subpkg)
                sub_status = "[green]✓[/green]" if sub_installed else "[red]✗[/red]"
                info += f"  {sub_status} [yellow]{subpkg}[/yellow]\n"
            if len(subpackages) > 10:
                info += f"  ... and {len(subpackages) - 10} more\n"

        if tool['commands']:
            info += "\n[bold cyan]Available Commands:[/bold cyan]\n"
            for cmd in tool['commands'][:10]:  # Limit to 10
                info += f"  • [green]{cmd}[/green]\n"
            if len(tool['commands']) > 10:
                info += f"  ... and {len(tool['commands']) - 10} more\n"

        if tool['installed']:
            deps = self.manager.get_dependencies(tool['name'])
            if deps:
                info += f"\n[bold cyan]Dependencies ({len(deps)}):[/bold cyan] {', '.join(deps[:5])}"
                if len(deps) > 5:
                    info += f" ... +{len(deps) - 5} more"
                info += "\n"

        # Add full package info at the bottom (excluding Description field since it's already shown at top)
        if full_info:
            info += f"\n[dim]{'-' * 80}[/dim]\n"
            info += "[bold cyan]Full Package Information:[/bold cyan]\n\n"
            # Filter out the Description and Conffiles fields from full info
            filtered_lines = []
            skip_desc = False
            skip_conffiles = False
            for line in lines:
                if line.startswith('Description:') or line.startswith('Description-en:'):
                    skip_desc = True
                    continue
                elif line.startswith('Conffiles:'):
                    skip_conffiles = True
                    continue
                elif skip_desc:
                    if line.startswith(' '):
                        # Still in description, skip
                        continue
                    else:
                        # Description ended
                        skip_desc = False
                elif skip_conffiles:
                    if line.startswith(' '):
                        # Still in conffiles, skip
                        continue
                    else:
                        # Conffiles ended
                        skip_conffiles = False

                if not skip_desc and not skip_conffiles:
                    filtered_lines.append(line)

            info += '\n'.join(filtered_lines)

        console.print(Panel(info, title=f"🔍 Tool Details: {tool['name']}", border_style="cyan"))

        if not tool['installed']:
            install_cmd = f"sudo apt-get install {tool['name']}"
            console.print("\n[bold cyan]Installation Command:[/bold cyan]")
            syntax = Syntax(install_cmd, "bash", theme="monokai", line_numbers=False)
            console.print(syntax)
        else:
            if tool['commands']:
                console.print(f"\n[bold cyan]Launch Command:[/bold cyan] [green]{tool['commands'][0]}[/green]")

        if interactive:
            # Enhanced options: handle main package and sub-packages
            if tool['installed']:
                options_text = "[bold green]Options:[/bold green] 1) Uninstall Main Package"
                choices = ["1"]
                if subpackages:
                    options_text += "  2) Manage Sub-packages"
                    choices.append("2")
                    options_text += "  3) Remove from List  4) Back to Menu"
                    choices.extend(["3", "4"])
                else:
                    options_text += "  2) Remove from List  3) Back to Menu"
                    choices.extend(["2", "3"])

                console.print(f"\n{options_text}")
                default_choice = choices[-1]
                choice = Prompt.ask("[cyan]Select option[/cyan]", choices=choices, default=default_choice)

                if choice == "1":
                    success = self.manager.uninstall_tool(tool['name'])
                    if success:
                        self.add_to_recent(tool['name'], 'uninstall')
                        self.show_toast(f"{tool['name']} uninstalled successfully!", "success")
                elif subpackages and choice == "2":
                    self._manage_subpackages(tool, subpackages)
                elif (not subpackages and choice == "2") or (subpackages and choice == "3"):
                    if self.manager.remove_tool_from_list(tool['name']):
                        self.show_toast(f"{tool['name']} removed from list", "warning")
                return
            else:
                options_text = "[bold yellow]Options:[/bold yellow] 1) Install Main Package"
                choices = ["1"]
                if subpackages:
                    options_text += "  2) Manage Sub-packages"
                    choices.append("2")
                    options_text += "  3) Remove from List  4) Back to Menu"
                    choices.extend(["3", "4"])
                else:
                    options_text += "  2) Remove from List  3) Back to Menu"
                    choices.extend(["2", "3"])

                console.print(f"\n{options_text}")
                default_choice = choices[-1]
                choice = Prompt.ask("[cyan]Select option[/cyan]", choices=choices, default=default_choice)

                if choice == "1":
                    success = self.manager.install_tool(tool['name'])
                    if success:
                        self.add_to_recent(tool['name'], 'install')
                        self.show_toast(f"{tool['name']} installed successfully!", "success")
                elif subpackages and choice == "2":
                    self._manage_subpackages(tool, subpackages)
                elif (not subpackages and choice == "2") or (subpackages and choice == "3"):
                    if self.manager.remove_tool_from_list(tool['name']):
                        self.show_toast(f"{tool['name']} removed from list", "warning")
                return

    def _manage_subpackages(self, tool: Tool, subpackages: list[str]):
        """Interactive sub-package installation/uninstallation menu."""
        console.clear()
        console.print(f"\n[bold cyan]Sub-packages for {tool['name']}:[/bold cyan]\n")

        # Build table of sub-packages with status
        table = Table(box=box.ROUNDED, show_lines=False)
        table.add_column("#", style="yellow bold", width=5, justify="right")
        table.add_column("Package Name", style="cyan bold", width=40)
        table.add_column("Status", width=15, justify="center")

        pkg_status = []
        for idx, subpkg in enumerate(subpackages, 1):
            installed = self.manager.check_installation(subpkg)
            status_text = "[green]✓ Installed[/green]" if installed else "[red]✗ Available[/red]"
            table.add_row(str(idx), subpkg, status_text)
            pkg_status.append((subpkg, installed))

        console.print(table)

        console.print("\n[bold yellow]Options:[/bold yellow]")
        console.print("  • Enter a number to install/uninstall that package")
        console.print("  • Press 'A' to install all sub-packages")
        console.print("  • Press 'U' to uninstall all sub-packages")
        console.print("  • Press 'Q' to go back")

        choice = Prompt.ask("\n[cyan]Select option[/cyan]", default="Q").strip().upper()

        if choice == 'Q':
            return
        elif choice == 'A':
            # Install all uninstalled sub-packages
            for subpkg, installed in pkg_status:
                if not installed:
                    console.print(f"\n[yellow]Installing {subpkg}...[/yellow]")
                    self.manager.install_tool(subpkg)
            self.show_toast("All sub-packages installed!", "success")
        elif choice == 'U':
            # Uninstall all installed sub-packages
            for subpkg, installed in pkg_status:
                if installed:
                    console.print(f"\n[yellow]Uninstalling {subpkg}...[/yellow]")
                    self.manager.uninstall_tool(subpkg)
            self.show_toast("All sub-packages uninstalled!", "success")
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(pkg_status):
                subpkg, installed = pkg_status[idx]
                if installed:
                    console.print(f"\n[yellow]Uninstalling {subpkg}...[/yellow]")
                    self.manager.uninstall_tool(subpkg)
                    self.show_toast(f"{subpkg} uninstalled!", "success")
                else:
                    console.print(f"\n[yellow]Installing {subpkg}...[/yellow]")
                    self.manager.install_tool(subpkg)
                    self.show_toast(f"{subpkg} installed!", "success")
            else:
                console.print("[red]Invalid package number![/red]")
        else:
            console.print("[yellow]Invalid option![/yellow]")

        console.print("\n[dim]Press any key to continue...[/dim]")
        input("Press Enter to continue...")
    def show_menu(self):
        """Entry point for launching the interactive list directly."""
        self.list_tools_interactive()

    def show_help(self):
        """Show compact keyboard shortcuts help."""
        help_text = """
[bold cyan]╔═══════════════════════════════════════════════════════════════╗[/bold cyan]
[bold cyan]║              INTERACTIVE KEYBOARD NAVIGATION GUIDE             ║[/bold cyan]
[bold cyan]╚═══════════════════════════════════════════════════════════════╝[/bold cyan]

[bold green]🎯 ESSENTIAL NAVIGATION:[/bold green]
    [bold white]↑/↓ or K/J[/bold white]      Move cursor up/down
    [bold white]ENTER[/bold white]            Open details for highlighted tool
    [bold white]0-9[/bold white]              Type a number to jump to that entry, then ENTER
    [bold white]I[/bold white]                Install/Uninstall highlighted tool
    [bold white]D[/bold white]                View detailed info for tool
    [bold white]C[/bold white]                Set/clear category & subcategory override
    [bold white]O[/bold white]                Cycle sort: name → installed → size → category
    [bold green]N/P or ←/→[/bold green]      Next/Previous page

[bold blue]🔍 SEARCH & FILTERS:[/bold blue]
    [bold cyan]S[/bold cyan]                Search tools by name/command
    [bold cyan]B[/bold cyan]                Clear search / show all tools

[bold yellow]🛠  SYSTEM ACTIONS:[/bold yellow]
    [bold white]U[/bold white]                Updates menu (check for / apply upgrades)
    [bold white]R[/bold white]                Re-scan installed tools
    [bold magenta]Y[/bold magenta]                Utilities: export/import/backup/local repo
    [bold red]?[/bold red]                Show this help screen
    [bold red]Q / ESC[/bold red]          Exit application
    [bold red]Ctrl+C[/bold red]          Force quit

[bold magenta]💡 TIPS:[/bold magenta]
    • Arrow keys auto-page at boundaries (↑ on row 1 = previous page)
    • Non-interactive scripting: `kalitools list --installed --json`
    • Launch the Textual UI with `kalitools --tui` (requires the `[tui]` extra)
    • Profiles: `kalitools profile list` / `kalitools profile apply <slug>`
    • History:  `kalitools history --limit 20`

[bold cyan]🏷 CATEGORY DESCRIPTIONS:[/bold cyan]
[dim]Web:[/dim] Web app scanning & content discovery
[dim]Wireless:[/dim] Wi‑Fi recon & attack tooling
[dim]Forensics:[/dim] Disk/memory artifact analysis
[dim]Exploitation:[/dim] Exploit frameworks & active attacks
[dim]Password:[/dim] Hash cracking & brute force tools
[dim]Recon:[/dim] Asset/service enumeration & OSINT
[dim]Sniffing:[/dim] Packet capture & protocol analysis
[dim]Reverse:[/dim] Binary/firmware reverse engineering
[dim]Social:[/dim] Phishing & campaign orchestration
[dim]Database:[/dim] SQL/DB exploitation & auditing
[dim]Other:[/dim] Miscellaneous / uncategorized tools

[bold cyan]═══════════════════════════════════════════════════════════════[/bold cyan]
        """
        console.print(help_text)

    def run(self):
        """Main application loop - starts directly in tool list with enhanced features"""
        # Use Knight Rider-style animation during initial startup for consistency
        def _startup_task():
            # Small sleep to let the animation be visible while any
            # constructor/cache work settles. Adjusted to stay snappy.
            time.sleep(0.25)
            return True

        self.run_knight_rider(_startup_task, label="Loading tools")

        self.show_banner()

        try:
            if self.basic_mode:
                self.run_basic_mode()
                return

            try:
                self.list_tools_interactive()
            except Exception as e:
                import traceback

                from rich.markup import escape
                console.print("\n[red]An unexpected error occurred:[/red] " + escape(str(e)))
                console.print(escape(traceback.format_exc()), style="dim")
                console.print("\n[yellow]Please report this issue.[/yellow]\n")
                return

            if self.should_exit:
                console.print("\n[bold green]Thanks for using Kali Tools Manager! 🛡️  Stay secure![/bold green]\n")
        finally:
            self._size_executor.shutdown(wait=False, cancel_futures=True)

    def run_basic_mode(self):
        """Simplified, low-color experience for limited terminals."""
        console.print("[cyan]Basic mode enabled – rendering simplified list (install/uninstall prompts only).[/cyan]")
        per_page = 20
        page = 1
        linux_capable = sys.platform.startswith('linux')

        while True:
            tools = self.get_filtered_tools()
            if not tools:
                console.print("No tools match the current filter/search.")
                return

            total_pages = max(1, (len(tools) + per_page - 1) // per_page)
            page = max(1, min(page, total_pages))
            start = (page - 1) * per_page
            end = min(start + per_page, len(tools))
            subset = tools[start:end]

            console.print("\n" + "=" * 60)
            console.print(f"Tools {start + 1}-{end} of {len(tools)} | Page {page}/{total_pages}")
            console.print("=" * 60)
            for idx, tool in enumerate(subset, start=start + 1):
                status = "INST" if tool['installed'] else "avail"
                category = tool.get('category', 'other')
                subcat = self.get_subcategory_for_tool(tool)
                extra = f" | {category}"
                if subcat:
                    extra += f"/{subcat}"
                desc = self.manager.get_cached_description(tool['name']) or TOOL_DESCRIPTIONS.get(tool['name'], '')
                if desc:
                    desc = self.format_description(desc, 60)
                    extra += f" -> {desc}"
                size_cached = tool.get('size') if isinstance(tool, dict) else getattr(tool, 'size', None)
                if size_cached:
                    size_text = self.format_size(size_cached)
                else:
                    size_text = self.format_size(self.ensure_tool_size(tool))
                console.print(f"{idx:>4}. {tool['name']:<30} [{status}] {size_text:>8} {extra}")

            console.print("\nCommands: [number]=details  N=next  P=prev  S=search  F=filter reset  U=utilities  C=categorize  Q=quit")
            choice = Prompt.ask("Select command", default="Q").strip()
            upper_choice = choice.upper()

            if not choice:
                continue
            if upper_choice == 'Q':
                self.should_exit = True
                break
            if upper_choice == 'N':
                page = min(page + 1, total_pages)
                continue
            if upper_choice == 'P':
                page = max(page - 1, 1)
                continue
            if upper_choice == 'S':
                query = Prompt.ask("Enter search term (blank clears)", default="")
                self.set_view(self.current_filter, self.current_category, query or None)
                page = 1
                continue
            if upper_choice == 'F':
                self.set_view('all', None, None)
                page = 1
                continue
            if upper_choice == 'U':
                self.show_utilities_menu()
                continue
            if upper_choice == 'C':
                target = Prompt.ask("Enter tool number to categorize", default=str(start + 1)).strip()
                if target.isdigit():
                    idx = int(target) - 1
                    if 0 <= idx < len(tools):
                        console.clear()
                        self.categorize_tool(tools[idx])
                        continue
                console.print("[yellow]Invalid tool number[/yellow]")
                continue

            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(tools):
                    tool = tools[index]
                    self.show_tool_details(tool['name'], interactive=False)
                    if linux_capable:
                        action = Prompt.ask("Action: (I)nstall, (U)ninstall, (C)ategorize, Enter to skip", default="").strip().upper()
                        if action == 'I':
                            self.manager.install_tool(tool['name'])
                        elif action == 'U':
                            self.manager.uninstall_tool(tool['name'])
                        elif action == 'C':
                            console.clear()
                            self.categorize_tool(tool)
                else:
                    console.print("[yellow]Invalid selection number[/yellow]")
            else:
                console.print("[yellow]Unknown command[/yellow]")

    def get_column_widths(self) -> tuple[int, int, int, int]:
        """Return (name_width, status_width, size_width, category_width) based on theme."""
        theme = (self.theme or '').lower()
        if theme == 'minimal':
            return (28, 12, 9, 10)
        # default theme
        return (40, 15, 10, 14)


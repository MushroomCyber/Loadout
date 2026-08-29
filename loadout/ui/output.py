"""Rendering. Every command can emit either a human view or JSON.

Machine-readability is not an afterthought here: ``--json`` is a global flag and
each command produces a documented shape, so the tool composes in a pipeline
instead of only in a terminal.
"""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from .. import env_flag, get_console

#: Status glyphs. One column, one glyph -- the previous UI spent a whole column
#: on coloured words, which is a lot of width to spend on a boolean.
GLYPHS = {
    "installed": ("●", "[ok]"),
    "available": ("○", "[ ]"),
    "starred": ("★", "*"),
    "held": ("⏸", "[h]"),
    "ok": ("✓", "[ok]"),
    "warn": ("!", "[!]"),
    "fail": ("✗", "[x]"),
    "arrow": ("→", "->"),
    "bullet": ("•", "-"),
}


@functools.lru_cache(maxsize=1)
def _terminal_is_ascii_only() -> bool:
    """True when stdout cannot encode our glyphs.

    A Windows console on a legacy code page raises UnicodeEncodeError partway
    through rendering, which leaves a half-drawn table and a traceback. Detect
    it and fall back rather than letting the terminal decide by crashing.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    probe = "".join(fancy for fancy, _plain in GLYPHS.values())
    try:
        probe.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return True
    return False


def ascii_mode() -> bool:
    return env_flag("LOADOUT_NO_EMOJI") or _terminal_is_ascii_only()


def glyph(name: str) -> str:
    fancy, plain = GLYPHS.get(name, ("?", "?"))
    return plain if ascii_mode() else fancy


def emit_json(payload: Any) -> None:
    """Write JSON to stdout. Never themed, never truncated, never to stderr."""
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def print_error(message: str, remediation: str = "") -> None:
    console = get_console()
    console.print(f"[bold red]{glyph('fail')}[/bold red] {message}", highlight=False)
    if remediation:
        console.print(f"  [dim]{glyph('arrow')} {remediation}[/dim]", highlight=False)


def print_ok(message: str) -> None:
    get_console().print(f"[green]{glyph('ok')}[/green] {message}", highlight=False)


def print_warn(message: str) -> None:
    get_console().print(f"[yellow]{glyph('warn')}[/yellow] {message}", highlight=False)


def print_note(message: str) -> None:
    get_console().print(f"[dim]{message}[/dim]", highlight=False)


def tool_rows(tools: Sequence[Any], installed: set[str], starred: set[str]) -> list[dict]:
    rows = []
    for tool in tools:
        rows.append(
            {
                "status": glyph("installed") if tool.id in installed else glyph("available"),
                "star": glyph("starred") if tool.id in starred else " ",
                "name": tool.id,
                "summary": tool.summary or "",
                "category": tool.category,
                "providers": ", ".join(tool.providers) or "-",
            }
        )
    return rows


def render_tool_table(
    tools: Sequence[Any],
    *,
    installed: set[str] | None = None,
    starred: set[str] | None = None,
    show_providers: bool = True,
) -> None:
    """Four columns, not six.

    Summary is the highest-value column and it is the one the old six-column
    layout did not have. Size, subcategory and version live in ``show``, where
    there is room for them.
    """
    from rich import box
    from rich.table import Table

    console = get_console()
    installed = installed or set()
    starred = starred or set()

    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, show_edge=False, header_style="dim")
    table.add_column("", width=1, no_wrap=True)
    table.add_column("TOOL", style="bold", no_wrap=True)
    table.add_column("SUMMARY", overflow="ellipsis", max_width=64)
    if show_providers:
        table.add_column("VIA", style="dim", no_wrap=True)

    for tool in tools:
        is_installed = tool.id in installed
        mark = (
            f"[green]{glyph('installed')}[/green]"
            if is_installed
            else f"[dim]{glyph('available')}[/dim]"
        )
        name = tool.id
        if tool.id in starred:
            name = f"{name} [yellow]{glyph('starred')}[/yellow]"
        summary = tool.summary or "[dim]no description in catalog[/dim]"
        row = [mark, name, summary]
        if show_providers:
            row.append(", ".join(tool.providers) or "-")
        table.add_row(*row)

    console.print(table)


def render_detail(tool: Any, status: dict | None = None, provider_status: dict | None = None) -> None:
    """The pane that justifies using this instead of ``apt show``."""
    from rich.panel import Panel
    from rich.table import Table

    console = get_console()
    status = status or {}
    provider_status = provider_status or {}

    header = f"[bold]{tool.id}[/bold]"
    if status.get("installed"):
        header += f"  [green]{glyph('installed')} installed[/green]"
        if status.get("version"):
            header += f" [dim]{status['version']}[/dim]"
        if status.get("provider"):
            header += f" [dim]via {status['provider']}[/dim]"
    else:
        header += f"  [dim]{glyph('available')} not installed[/dim]"

    body = Table.grid(padding=(0, 2))
    body.add_column(style="dim", no_wrap=True)
    body.add_column(overflow="fold")

    if tool.summary:
        body.add_row("", tool.summary)
        body.add_row("", "")
    if tool.description and tool.description != tool.summary:
        body.add_row("", f"[dim]{tool.description[:400]}[/dim]")
        body.add_row("", "")

    def row(label: str, value: str) -> None:
        if value:
            body.add_row(label, value)

    row("category", ", ".join(tool.categories))
    row("phases", ", ".join(tool.phases))
    row("tags", ", ".join(tool.tags))
    row("binaries", ", ".join(tool.binaries) or "[dim]unknown[/dim]")
    row("homepage", tool.homepage)
    row("license", tool.license)
    if tool.size:
        row("size", f"{tool.size / 1024 / 1024:.1f} MB")
    if tool.requires_root:
        row("privileges", "requires root")

    if tool.install:
        body.add_row("", "")
        for method in tool.install:
            state = provider_status.get(method.provider)
            if state is None:
                mark = "[dim]?[/dim]"
            elif state.available:
                mark = f"[green]{glyph('ok')}[/green]"
            else:
                mark = "[dim]-[/dim]"
            spec = " ".join(f"{k}={v}" for k, v in method.spec.items() if k != "provider")
            body.add_row("install", f"{mark} [bold]{method.provider}[/bold]  [dim]{spec}[/dim]")

    if tool.alternatives:
        body.add_row("", "")
        body.add_row("see also", ", ".join(tool.alternatives))

    console.print(Panel(body, title=header, border_style="cyan", padding=(1, 2)))


def render_plan(plan: Any, *, verbose: bool = False) -> None:
    console = get_console()
    if not plan.actions and not plan.skipped:
        print_note("Nothing to do.")
        return

    for action in plan.actions:
        if not action.steps:
            continue
        console.print(
            f"  [bold]{action.tool.id}[/bold] "
            f"[dim]via {action.provider}[/dim]"
            + ("  [yellow]root[/yellow]" if action.needs_root else "")
        )
        if verbose:
            for line in action.render():
                console.print(f"      [dim]$ {line}[/dim]", highlight=False)

    for skipped in plan.skipped:
        console.print(f"  [dim]{skipped.tool_id}: {skipped.reason}[/dim]", highlight=False)


def render_table(rows: Iterable[dict], columns: Sequence[str], *, title: str = "") -> None:
    from rich import box
    from rich.table import Table

    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, header_style="dim")
    for column in columns:
        table.add_column(column.upper(), overflow="fold")
    count = 0
    for row in rows:
        table.add_row(*[str(row.get(column, "")) for column in columns])
        count += 1
    console = get_console()
    if title:
        console.print(f"[bold]{title}[/bold]")
    if count:
        console.print(table)
    else:
        print_note("(nothing to show)")


def confirm(question: str, *, assume_yes: bool = False, default: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print_warn(f"{question} -- not a terminal, assuming no. Pass --yes to proceed.")
        return False
    from rich.prompt import Confirm

    try:
        return Confirm.ask(question, default=default)
    except (KeyboardInterrupt, EOFError):
        return False

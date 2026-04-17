"""Utility helpers for exporting/importing Kali tool configurations."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import console
from .model import Tool


class ConfigManager:
    """Import / export helper for :class:`Tool` lists."""

    def __init__(self, tools: list[Tool]):
        self.tools = tools

    # ----- export -------------------------------------------------------------
    def export_tools_list(self, filename: str) -> bool:
        try:
            installed = [t for t in self.tools if t.installed]
            export_data = {
                "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "total_tools": len(installed),
                "tools": [
                    {"name": t.name, "commands": list(t.commands), "category": t.category}
                    for t in installed
                ],
            }
            Path(filename).write_text(json.dumps(export_data, indent=2), encoding="utf-8")
            console.print(f"[green]✓ Exported {len(installed)} tools to {filename}[/green]")
            return True
        except OSError as exc:
            console.print(f"[red]Error exporting tools: {exc}[/red]")
            return False

    # ----- import -------------------------------------------------------------
    def _read_tool_names(self, filename: str) -> list[str]:
        try:
            payload = Path(filename).read_text(encoding="utf-8")
        except OSError as exc:
            console.print(f"[red]Could not read {filename}: {exc}[/red]")
            return []
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid JSON in {filename}: {exc}[/red]")
            return []
        tools = data.get("tools") or []
        names: list[str] = []
        seen: set[str] = set()
        for entry in tools:
            name = ""
            if isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()
            elif isinstance(entry, str):
                name = entry.strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names

    def import_tools_list(
        self,
        filename: str,
        *,
        installer: Callable[[str], bool] | None = None,
        assume_yes: bool = False,
    ) -> list[str]:
        """Read a JSON export and optionally install the listed packages.

        Returns the list of tool names that were parsed from the file
        (regardless of install outcome). If ``installer`` is provided
        each name is passed to it; the caller is responsible for any
        per-package policy (sudo, dry-run, etc.).
        """
        names = self._read_tool_names(filename)
        if not names:
            return []
        console.print(f"[cyan]Loaded {len(names)} tool(s) from {filename}[/cyan]")
        if installer is None:
            return names

        known = {t.name for t in self.tools}
        missing = [n for n in names if n not in known]
        if missing:
            console.print(f"[yellow]Skipping {len(missing)} unknown tool(s): "
                          f"{', '.join(missing[:8])}{'...' if len(missing) > 8 else ''}[/yellow]")
        targets = [n for n in names if n in known]
        if not targets:
            console.print("[yellow]Nothing installable in the imported list.[/yellow]")
            return names

        if not assume_yes:
            from rich.prompt import Confirm

            if not Confirm.ask(
                f"Install {len(targets)} tool(s) from import?",
                default=True,
            ):
                console.print("[yellow]Install skipped by user.[/yellow]")
                return names

        failures: list[str] = []
        for pkg in targets:
            try:
                ok = bool(installer(pkg))
            except Exception as exc:
                ok = False
                console.print(f"[red]Installer raised for {pkg}: {exc}[/red]")
            if not ok:
                failures.append(pkg)
        if failures:
            console.print(f"[red]Failed to install {len(failures)} package(s): "
                          f"{', '.join(failures)}[/red]")
        else:
            console.print(f"[green]✓ Installed {len(targets)} package(s) from import[/green]")
        return names

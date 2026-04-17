"""Command-line interface for Kali Tools Manager.

The CLI exposes two faces:

* **No subcommand** – launches the interactive Rich/Textual UI (the
  historical behaviour).
* **Subcommands** – scriptable, ``--json``-friendly automation surface
  (``list``, ``search``, ``show``, ``install``, ``remove``, ``update``,
  ``upgrade``, ``catalog``, ``profile``, ``history``, ``export``).

Design goals:
* No subcommand is required for the interactive launcher; existing
  ``kalitools`` shell wrappers keep working.
* Every subcommand returns a machine-readable result via ``--json``.
* All destructive operations respect ``--yes`` / ``--dry-run``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import __version__, configure_logging, console

try:
    import termios  # noqa: F401 parity with UI module
    import tty  # noqa: F401

    TERMIOS_AVAILABLE = True
except ImportError:
    TERMIOS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kalitools",
        description="Discover and manage Kali Linux tooling from the terminal.",
    )
    parser.add_argument("--version", action="version", version=f"kalitools {__version__}")
    parser.add_argument(
        "--mode",
        choices=["auto", "rich", "basic"],
        default="auto",
        help="UI mode for the interactive launcher (default: auto)",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch the Textual UI instead of the Rich interface.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (DEBUG, INFO, WARNING, ERROR).",
    )
    parser.add_argument(
        "--discovery-workers",
        type=int,
        default=8,
        help="Concurrent scraper workers for legacy web discovery.",
    )
    parser.add_argument(
        "--discovery-delay",
        type=float,
        default=0.2,
        help="Delay between HTTP fetches during legacy web discovery (seconds).",
    )
    parser.add_argument(
        "--debug-scraper",
        action="store_true",
        help="Emit verbose scraper diagnostics to debug_scraper.txt.",
    )
    parser.add_argument(
        "--no-emoji",
        action="store_true",
        help="Strip emoji glyphs from output (helpful for minimal terminals).",
    )
    parser.add_argument(
        "--theme",
        default=os.environ.get("KALITOOLS_THEME", "default"),
        choices=["default", "mono", "solarized-dark", "high-contrast"],
        help="Rich colour theme for the interactive UI and CLI output.",
    )
    parser.add_argument(
        "--log-file",
        default=os.environ.get("KALITOOLS_LOG_FILE"),
        help="Also append log records to this file.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip any network call (scraping, update check).",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # list
    p_list = sub.add_parser("list", help="List tools (optionally filtered).")
    p_list.add_argument("--category", help="Filter by category slug.")
    p_list.add_argument("--installed", action="store_true", help="Only installed tools.")
    p_list.add_argument("--available", action="store_true", help="Only not-installed tools.")
    p_list.add_argument("--starred", action="store_true", help="Only starred tools.")
    p_list.add_argument("--json", action="store_true", dest="as_json")
    p_list.add_argument("--limit", type=int, default=0, help="Maximum entries (0=all).")

    # search
    p_search = sub.add_parser(
        "search",
        help="Search tools by name/description. Supports category:web, tag:osint, fuzzy.",
    )
    p_search.add_argument("query", nargs="+")
    p_search.add_argument("--json", action="store_true", dest="as_json")
    p_search.add_argument("--limit", type=int, default=50)

    # star / unstar
    p_star = sub.add_parser("star", help="Mark a tool as a favourite.")
    p_star.add_argument("name")
    p_unstar = sub.add_parser("unstar", help="Remove a tool from favourites.")
    p_unstar.add_argument("name")

    # hold / unhold
    p_hold = sub.add_parser("hold", help="apt-mark hold (pin current version).")
    p_hold.add_argument("name")
    p_unhold = sub.add_parser("unhold", help="apt-mark unhold (release pin).")
    p_unhold.add_argument("name")
    sub.add_parser("holds", help="List packages currently held via apt-mark.")

    # doctor
    p_doctor = sub.add_parser("doctor", help="Diagnose common environment issues.")
    p_doctor.add_argument("--json", action="store_true", dest="as_json")

    # show
    p_show = sub.add_parser("show", help="Show details for a single tool.")
    p_show.add_argument("name")
    p_show.add_argument("--json", action="store_true", dest="as_json")

    # install / remove / update / upgrade
    p_install = sub.add_parser("install", help="Install one or more tools via apt-get.")
    p_install.add_argument("packages", nargs="+")
    p_install.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
    p_install.add_argument("--dry-run", action="store_true", help="Preview only; do not install.")

    p_remove = sub.add_parser("remove", help="Uninstall one or more tools.")
    p_remove.add_argument("packages", nargs="+")
    p_remove.add_argument("--yes", action="store_true")
    p_remove.add_argument("--dry-run", action="store_true")

    sub.add_parser("update", help="Run `apt-get update` and list upgradable tools.")
    sub.add_parser("upgrade", help="Run `apt-get upgrade -y`.")

    # catalog
    p_cat = sub.add_parser("catalog", help="Catalog management (build/refresh/info).")
    cat_sub = p_cat.add_subparsers(dest="catalog_command", required=True)
    p_cat_refresh = cat_sub.add_parser("refresh", help="Rebuild the tool catalog.")
    p_cat_refresh.add_argument(
        "--source",
        choices=["apt", "kali.org", "both"],
        default="apt",
        help="Catalog source (default: apt).",
    )
    p_cat_refresh.add_argument("--force", action="store_true")
    p_cat_refresh.add_argument("--filter-kali", action="store_true",
                               help="Keep only packages that look like Kali tools.")
    cat_sub.add_parser("info", help="Print catalog metadata.")

    # profile
    p_prof = sub.add_parser("profile", help="Manage curated tool profiles.")
    prof_sub = p_prof.add_subparsers(dest="profile_command", required=True)
    prof_sub.add_parser("list", help="List available profiles.")
    p_prof_show = prof_sub.add_parser("show", help="Show profile details.")
    p_prof_show.add_argument("slug")
    p_prof_show.add_argument("--json", action="store_true", dest="as_json")
    p_prof_apply = prof_sub.add_parser("apply", help="Install all packages in a profile.")
    p_prof_apply.add_argument("slug")
    p_prof_apply.add_argument("--yes", action="store_true")
    p_prof_apply.add_argument("--dry-run", action="store_true")

    # history
    p_hist = sub.add_parser("history", help="Show operation history.")
    p_hist.add_argument("--package", help="Filter by package name.")
    p_hist.add_argument("--limit", type=int, default=50)
    p_hist.add_argument("--json", action="store_true", dest="as_json")
    p_hist.add_argument("--clear", action="store_true", help="Clear history.")

    # export
    p_exp = sub.add_parser("export", help="Export installed tools.")
    p_exp.add_argument("--format", choices=["json", "script"], default="json")
    p_exp.add_argument("--output", "-o", help="Output file (default: stdout).")

    return parser


def resolve_ui_mode(requested: str) -> str:
    if requested == "auto":
        return "rich" if TERMIOS_AVAILABLE else "basic"
    return requested


# ---------------------------------------------------------------------------
# Subcommand helpers
# ---------------------------------------------------------------------------

def _make_manager(args: argparse.Namespace):
    from .manager import KaliToolsManager

    return KaliToolsManager(
        discovery_workers=args.discovery_workers,
        discovery_delay=args.discovery_delay,
        debug_scraper=args.debug_scraper,
    )


def _tool_to_dict(tool) -> dict[str, Any]:
    if hasattr(tool, "to_dict"):
        return tool.to_dict()
    return dict(tool)


def _print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    from rich.table import Table

    table = Table(show_lines=False)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(row.get(c, "")) for c in columns])
    console.print(table)


def _prompt_confirm(question: str, *, assume_yes: bool, default: bool = False) -> bool:
    if assume_yes:
        return True
    from rich.prompt import Confirm

    return Confirm.ask(question, default=default)


# ---- list / search / show ---------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    manager = _make_manager(args)
    tools = manager.tools
    if args.installed:
        tools = [t for t in tools if t.get("installed")]
    if args.available:
        tools = [t for t in tools if not t.get("installed")]
    if args.category:
        cat = args.category.strip().lower()
        tools = [t for t in tools if (t.get("category") or "other") == cat]
    if args.starred:
        from .state import get_state_db

        starred = set(get_state_db().star_list())
        tools = [t for t in tools if t.get("name") in starred]
    if args.limit and args.limit > 0:
        tools = tools[: args.limit]

    if args.as_json:
        print(json.dumps([_tool_to_dict(t) for t in tools], indent=2))
        return 0

    from .state import get_state_db

    starred = set(get_state_db().star_list())
    rows = [{
        "name": ("* " if t.get("name") in starred else "  ") + str(t.get("name")),
        "category": t.get("category") or "other",
        "installed": "yes" if t.get("installed") else "no",
        "size": t.get("size") or 0,
    } for t in tools]
    _print_table(rows, ["name", "category", "installed", "size"])
    console.print(f"[dim]{len(rows)} tool(s)[/dim]")
    return 0


def _parse_search_query(tokens: list[str]) -> tuple[str, dict[str, str]]:
    """Parse tokens into (free_text, filters). Filters: category:X, tag:Y, installed:yes/no."""
    free: list[str] = []
    filters: dict[str, str] = {}
    for tok in tokens:
        if ":" in tok:
            key, _, val = tok.partition(":")
            key = key.strip().lower()
            val = val.strip().lower()
            if key in {"category", "cat", "tag", "installed"}:
                if key == "cat":
                    key = "category"
                filters[key] = val
                continue
        free.append(tok)
    return " ".join(free).strip().lower(), filters


def _score_tool(tool: dict, query: str) -> int:
    """Return 0..100 match score. Uses rapidfuzz if available, else substring heuristic."""
    if not query:
        return 50
    name = (tool.get("name") or "").lower()
    desc = (tool.get("description") or "").lower()
    try:
        from rapidfuzz import fuzz

        return max(
            fuzz.partial_ratio(query, name),
            fuzz.partial_ratio(query, desc) // 2,
        )
    except ImportError:
        if query in name:
            return 90 if name.startswith(query) else 75
        if query in desc:
            return 40
        # naive subsequence fallback
        it = iter(name)
        if all(ch in it for ch in query):
            return 30
        return 0


def cmd_search(args: argparse.Namespace) -> int:
    manager = _make_manager(args)
    query, filters = _parse_search_query(args.query)

    def _match_filters(t: dict) -> bool:
        if "category" in filters:
            if (t.get("category") or "other").lower() != filters["category"]:
                return False
        if "tag" in filters:
            tags = [str(x).lower() for x in (t.get("tags") or [])]
            if filters["tag"] not in tags:
                return False
        if "installed" in filters:
            want = filters["installed"] in {"yes", "true", "1"}
            if bool(t.get("installed")) != want:
                return False
        return True

    candidates = [t for t in manager.tools if _match_filters(t)]
    scored = [(t, _score_tool(t, query)) for t in candidates]
    threshold = 50 if query else 0
    scored = [(t, s) for t, s in scored if s >= threshold]
    scored.sort(key=lambda p: (-p[1], p[0].get("name") or ""))
    hits = [t for t, _ in scored[: max(args.limit, 1)]]

    if args.as_json:
        print(json.dumps([_tool_to_dict(t) for t in hits], indent=2))
        return 0
    rows = [{"name": t.get("name"), "category": t.get("category") or "other",
             "description": (t.get("description") or "")[:70]} for t in hits]
    _print_table(rows, ["name", "category", "description"])
    console.print(f"[dim]{len(rows)} match(es) for {' '.join(args.query)!r}[/dim]")
    return 0


def cmd_star(args: argparse.Namespace) -> int:
    from .state import get_state_db

    get_state_db().set_starred(args.name, True)
    console.print(f"[green]✓ Starred {args.name}[/green]")
    return 0


def cmd_unstar(args: argparse.Namespace) -> int:
    from .state import get_state_db

    get_state_db().set_starred(args.name, False)
    console.print(f"[green]✓ Unstarred {args.name}[/green]")
    return 0


def cmd_hold(args: argparse.Namespace) -> int:
    manager = _make_manager(args)
    return 0 if manager.hold_package(args.name) else 1


def cmd_unhold(args: argparse.Namespace) -> int:
    manager = _make_manager(args)
    return 0 if manager.unhold_package(args.name) else 1


def cmd_holds(args: argparse.Namespace) -> int:
    manager = _make_manager(args)
    held = manager.list_held_packages()
    if args.as_json if hasattr(args, "as_json") else False:
        print(json.dumps(held, indent=2))
        return 0
    if not held:
        console.print("[dim]No packages currently held.[/dim]")
        return 0
    for name in held:
        console.print(f"🔒 {name}")
    console.print(f"[dim]{len(held)} held package(s)[/dim]")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import doctor

    results = doctor.run_all()
    if args.as_json:
        print(json.dumps(
            [{"name": r.name, "severity": r.severity, "message": r.message,
              "remediation": r.remediation} for r in results],
            indent=2,
        ))
    else:
        badge = {"ok": "[green]✓[/green]", "warn": "[yellow]![/yellow]",
                 "fail": "[red]✗[/red]"}
        for r in results:
            console.print(f"{badge.get(r.severity, '?')} [b]{r.name}[/b] — {r.message}")
            if r.remediation and r.severity != "ok":
                console.print(f"   [dim]→ {r.remediation}[/dim]")
    worst = doctor.worst_severity(results)
    return {"ok": 0, "warn": 0, "fail": 2}.get(worst, 1)


def cmd_show(args: argparse.Namespace) -> int:
    manager = _make_manager(args)
    tool = next((t for t in manager.tools if t.get("name") == args.name), None)
    if not tool:
        console.print(f"[red]Tool '{args.name}' not found in catalog.[/red]")
        return 1
    data = _tool_to_dict(tool)
    if args.as_json:
        print(json.dumps(data, indent=2))
        return 0
    for key, value in data.items():
        console.print(f"[b cyan]{key}:[/b cyan] {value}")
    return 0


# ---- install / remove / update / upgrade ------------------------------------

def _bulk_action(
    manager,
    packages: Iterable[str],
    *,
    action: str,
    dry_run: bool,
) -> int:
    failures = 0
    for pkg in packages:
        console.print(f"\n[cyan]{action}[/cyan] {pkg}")
        if dry_run:
            console.print("  [dim](dry-run)[/dim]")
            continue
        if action == "install":
            ok = manager.install_tool(pkg)
        else:
            ok = manager.uninstall_tool(pkg)
        if not ok:
            failures += 1
    return failures


def cmd_install(args: argparse.Namespace) -> int:
    manager = _make_manager(args)
    if not _prompt_confirm(
        f"Install {len(args.packages)} package(s)?",
        assume_yes=args.yes or args.dry_run,
        default=True,
    ):
        console.print("[yellow]Aborted.[/yellow]")
        return 130
    failures = _bulk_action(manager, args.packages, action="install", dry_run=args.dry_run)
    return 1 if failures else 0


def cmd_remove(args: argparse.Namespace) -> int:
    manager = _make_manager(args)
    if not _prompt_confirm(
        f"Uninstall {len(args.packages)} package(s)?",
        assume_yes=args.yes or args.dry_run,
        default=False,
    ):
        console.print("[yellow]Aborted.[/yellow]")
        return 130
    failures = _bulk_action(manager, args.packages, action="uninstall", dry_run=args.dry_run)
    return 1 if failures else 0


def cmd_update(args: argparse.Namespace) -> int:
    manager = _make_manager(args)
    upgradable = manager.check_updates()
    if not upgradable:
        console.print("[green]All tools up to date.[/green]")
        return 0
    console.print(f"[cyan]{len(upgradable)} upgradable tool(s):[/cyan]")
    for name in upgradable:
        console.print(f"  • {name}")
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    import subprocess

    console.print("[yellow]Running apt-get upgrade -y (requires sudo)...[/yellow]")
    rc = subprocess.run(["sudo", "apt-get", "upgrade", "-y"]).returncode
    return 0 if rc == 0 else rc


# ---- catalog ----------------------------------------------------------------

def cmd_catalog(args: argparse.Namespace) -> int:
    if args.catalog_command == "info":
        data_path = Path(__file__).parent / "data" / "tools_merged.json"
        if not data_path.exists():
            console.print("[red]Catalog file missing.[/red]")
            return 1
        try:
            payload = json.loads(data_path.read_text(encoding="utf-8"))
        except Exception as exc:
            console.print(f"[red]Cannot parse catalog: {exc}[/red]")
            return 1
        if isinstance(payload, list):
            console.print(f"schema: 1 (legacy flat list)\ntools: {len(payload)}")
        else:
            tools = payload.get("tools", [])
            console.print(
                f"schema: {payload.get('schema')}\n"
                f"generated_at: {payload.get('generated_at')}\n"
                f"source: {payload.get('source')}\n"
                f"tools: {len(tools)}"
            )
        return 0

    # refresh
    from datetime import datetime, timezone

    from . import apt_catalog
    from .manager import _atomic_write_json

    entries: list[dict[str, Any]] | None = None
    if args.source in ("apt", "both"):
        console.print("[cyan]Building catalog from APT...[/cyan]")
        entries = apt_catalog.build_catalog()
        if entries is None:
            console.print("[red]APT catalog build failed (no python-apt, no apt-cache).[/red]")
            return 2
        if args.filter_kali:
            entries = apt_catalog.filter_kali_tools(entries)
        console.print(f"[green]✓ Collected {len(entries)} entries from APT.[/green]")

    if args.source in ("kali.org", "both"):
        console.print("[cyan]Supplementing from kali.org...[/cyan]")
        manager = _make_manager(args)
        manager.discover_from_kali_site()
        if entries is None:
            # We only have the manager's enriched in-memory list
            entries = [t.to_dict() if hasattr(t, "to_dict") else dict(t) for t in manager.tools]

    if not entries:
        console.print("[red]No entries produced.[/red]")
        return 1

    installed = set(apt_catalog.installed_packages_via_dpkg())
    for e in entries:
        if e.get("name") in installed:
            e["installed"] = True

    payload = {
        "schema": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"type": args.source, "filter_kali": bool(args.filter_kali)},
        "tools": entries,
    }
    data_path = Path(__file__).parent / "data" / "tools_merged.json"
    _atomic_write_json(data_path, payload)
    console.print(f"[green]✓ Wrote catalog with {len(entries)} tools to {data_path}[/green]")

    # Garbage-collect state DB rows for packages no longer in the catalog.
    try:
        from .state import get_state_db

        pruned = get_state_db().prune_unknown(e.get("name", "") for e in entries)
        if pruned:
            console.print(f"[dim]Pruned {pruned} orphaned state row(s).[/dim]")
    except Exception as exc:  # pragma: no cover
        console.print(f"[dim]state prune failed: {exc}[/dim]")
    return 0


# ---- profile ----------------------------------------------------------------

def cmd_profile(args: argparse.Namespace) -> int:
    from . import profiles

    if args.profile_command == "list":
        items = profiles.list_profiles()
        if not items:
            console.print("[yellow]No profiles found.[/yellow]")
            return 0
        rows = [{"slug": p.slug, "name": p.name, "packages": len(p.packages),
                 "source": p.source, "tags": ",".join(p.tags)} for p in items]
        _print_table(rows, ["slug", "name", "packages", "source", "tags"])
        return 0

    if args.profile_command == "show":
        prof = profiles.get_profile(args.slug)
        if not prof:
            console.print(f"[red]Profile '{args.slug}' not found.[/red]")
            return 1
        if getattr(args, "as_json", False):
            print(json.dumps(prof.to_dict(), indent=2))
            return 0
        console.print(f"[b cyan]{prof.slug}[/b cyan] — {prof.name}")
        console.print(f"[dim]{prof.description}[/dim]")
        console.print(f"tags: {', '.join(prof.tags) or '-'}")
        console.print(f"packages ({len(prof.packages)}):")
        for pkg in prof.packages:
            console.print(f"  • {pkg}")
        return 0

    # apply
    prof = profiles.get_profile(args.slug)
    if not prof:
        console.print(f"[red]Profile '{args.slug}' not found.[/red]")
        return 1
    console.print(f"[cyan]Applying profile {prof.slug} ({len(prof.packages)} pkg)...[/cyan]")
    manager = _make_manager(args)
    known = {t.get("name") for t in manager.tools}
    filtered = [p for p in prof.packages if p in known] or list(prof.packages)
    if not _prompt_confirm(
        f"Install {len(filtered)} package(s) from profile {prof.slug}?",
        assume_yes=args.yes or args.dry_run,
        default=True,
    ):
        return 130
    failures = _bulk_action(manager, filtered, action="install", dry_run=args.dry_run)
    return 1 if failures else 0


# ---- history ----------------------------------------------------------------

def cmd_history(args: argparse.Namespace) -> int:
    from . import history as hist

    if args.clear:
        removed = hist.clear()
        console.print(f"[yellow]Cleared {removed} history row(s).[/yellow]")
        return 0
    rows = hist.recent(limit=args.limit, package=args.package)
    if args.as_json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        console.print("[dim]No history yet.[/dim]")
        return 0
    display = [{
        "ts": r["ts"], "action": r["action"], "package": r["package"],
        "success": "yes" if r["success"] else "no",
        "detail": (r.get("detail") or "")[:60],
    } for r in rows]
    _print_table(display, ["ts", "action", "package", "success", "detail"])
    return 0


# ---- export -----------------------------------------------------------------

def cmd_export(args: argparse.Namespace) -> int:
    manager = _make_manager(args)
    installed = [t for t in manager.tools if t.get("installed")]
    if args.format == "json":
        from datetime import datetime, timezone

        payload = {
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(installed),
            "tools": [_tool_to_dict(t) for t in installed],
        }
        text = json.dumps(payload, indent=2)
    else:
        # idempotent bash script
        lines = [
            "#!/usr/bin/env bash",
            "# Generated by kalitools export --format script",
            "set -euo pipefail",
            "export DEBIAN_FRONTEND=noninteractive",
            "sudo apt-get update -y",
            "PKGS=(",
        ]
        for t in installed:
            lines.append(f"  {t.get('name')}")
        lines += [
            ")",
            'for pkg in "${PKGS[@]}"; do',
            '  if ! dpkg -s "$pkg" >/dev/null 2>&1; then',
            '    sudo apt-get install -y "$pkg"',
            '  fi',
            'done',
        ]
        text = "\n".join(lines) + "\n"

    if args.output and args.output != "-":
        Path(args.output).write_text(text, encoding="utf-8")
        if args.format == "script":
            os.chmod(args.output, 0o755)
        console.print(f"[green]✓ Wrote {args.output}[/green]")
    else:
        print(text)
    return 0


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_SUBCOMMANDS = {
    "list": cmd_list,
    "search": cmd_search,
    "show": cmd_show,
    "star": cmd_star,
    "unstar": cmd_unstar,
    "install": cmd_install,
    "remove": cmd_remove,
    "update": cmd_update,
    "upgrade": cmd_upgrade,
    "catalog": cmd_catalog,
    "profile": cmd_profile,
    "history": cmd_history,
    "export": cmd_export,
}


def _run_interactive(args: argparse.Namespace) -> None:
    from .manager import KaliToolsManager

    manager = KaliToolsManager(
        discovery_workers=args.discovery_workers,
        discovery_delay=args.discovery_delay,
        debug_scraper=args.debug_scraper,
    )

    if args.tui:
        from .tui.app import run_tui, textual_available
        if not textual_available():
            console.print(
                "[yellow]Textual is not installed. "
                "Install with `pip install 'kalitools-app[tui]'`. "
                "Falling back to Rich UI.[/yellow]"
            )
        else:
            run_tui(manager)
            return

    from .ui import ToolsUI

    ui_mode = resolve_ui_mode(args.mode)
    ui = ToolsUI(manager, ui_mode=ui_mode)
    ui.run()


def main(argv: Iterable[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level, log_file=getattr(args, "log_file", None))

    # Rebuild the shared console with the chosen theme / emoji policy before
    # any other module uses it.
    from . import configure_console

    configure_console(theme=getattr(args, "theme", "default"),
                      no_emoji=bool(getattr(args, "no_emoji", False)))
    if args.no_emoji:
        os.environ["KALITOOLS_NO_EMOJI"] = "1"
    if getattr(args, "offline", False):
        os.environ["KALITOOLS_OFFLINE"] = "1"

    if not sys.platform.startswith("linux"):
        console.print(
            "[red]Kali Tools Manager requires Kali Linux or another Debian-based Linux distribution.[/red]"
        )
        raise SystemExit(1)

    try:
        if args.command is None:
            _run_interactive(args)
            return
        handler = _SUBCOMMANDS.get(args.command)
        if handler is None:  # pragma: no cover - argparse rejects unknown
            parser.print_help()
            raise SystemExit(2)
        rc = handler(args)
        raise SystemExit(rc or 0)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        raise SystemExit(130) from None
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"\n[red]Fatal error:[/red] {exc}")
        if str(args.log_level).upper() == "DEBUG":
            import traceback

            console.print(traceback.format_exc(), style="dim")
        else:
            console.print("[dim]Re-run with --log-level DEBUG for a full traceback.[/dim]")
        raise SystemExit(1) from exc


def tui_main(argv: Iterable[str] | None = None) -> None:
    """Entry point for the ``kalitools-tui`` console script."""
    args = build_parser().parse_args(list(argv) if argv else [])
    args.tui = True
    args.command = None
    configure_logging(args.log_level, log_file=getattr(args, "log_file", None))
    from . import configure_console

    configure_console(theme=getattr(args, "theme", "default"),
                      no_emoji=bool(getattr(args, "no_emoji", False)))
    _run_interactive(args)

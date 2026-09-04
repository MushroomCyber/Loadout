"""Command-line interface.

Design rules that differ from the previous release:

* Installed state is **queried live** from the providers, never read from a
  stale JSON cache. ``list --installed`` on a fresh machine used to print
  nothing at all, silently, with exit code 0.
* Every command accepts ``--json`` and returns a documented shape.
* Nothing touches the network during startup. Catalog refresh is an explicit
  command, not a side effect of constructing an object.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import __version__, completions, configure_console, configure_logging, lockfile, logger
from .. import verify as verify_mod
from ..errors import LoadoutError
from . import output as out

# ---------------------------------------------------------------------------
# Shared context
# ---------------------------------------------------------------------------


class _Sub(argparse.ArgumentParser):
    """Subparser that inherits the global flags, so they work in either position."""

    shared: argparse.ArgumentParser | None = None

    def __init__(self, *args, **kwargs) -> None:
        parents = list(kwargs.pop("parents", []))
        if _Sub.shared is not None and _Sub.shared not in parents:
            parents.append(_Sub.shared)
        kwargs["parents"] = parents
        kwargs.setdefault("conflict_handler", "resolve")
        super().__init__(*args, **kwargs)


@dataclass
class Context:
    args: argparse.Namespace
    _catalog: Any = None
    _state: Any = None
    _installed: set[str] | None = None
    _statuses: dict[str, Any] | None = None

    @property
    def json_mode(self) -> bool:
        return bool(getattr(self.args, "as_json", False))

    @property
    def catalog(self):
        if self._catalog is None:
            from ..catalog import open_catalog

            explicit = getattr(self.args, "catalog", None)
            self._catalog = open_catalog(Path(explicit) if explicit else None)
        return self._catalog

    @property
    def state(self):
        if self._state is None:
            from ..state import get_state_db

            self._state = get_state_db()
        return self._state

    @property
    def provider_status(self) -> dict[str, Any]:
        if self._statuses is None:
            from ..providers import available_providers

            self._statuses = available_providers()
        return self._statuses

    def installed(self, *, refresh: bool = True) -> set[str]:
        """What is actually on this machine, right now.

        Asks each available provider for its full inventory in one call, maps
        those back to catalog ids, and reconciles the state DB. This is the fix
        for install-state being read from a cache file that started out empty.
        """
        if self._installed is not None:
            return self._installed
        if not refresh:
            self._installed = self.state.installed_ids()
            return self._installed

        from ..providers import get_provider

        inventories: dict[str, set[str]] = {}
        for name, status in self.provider_status.items():
            if not status.available:
                continue
            try:
                inventories[name] = get_provider(name).list_installed()
            except Exception:
                inventories[name] = set()

        found: set[str] = set()
        provider_of: dict[str, str] = {}
        for tool in self.catalog.iter_all():
            for method in tool.install:
                inventory = inventories.get(method.provider)
                if not inventory:
                    continue
                keys = [
                    str(method.spec.get(key, ""))
                    for key in ("package", "formula", "crate", "gem", "image")
                ]
                if any(key and key in inventory for key in keys):
                    found.add(tool.id)
                    provider_of.setdefault(tool.id, method.provider)
                    break
            else:
                # Fall back to "is the binary on PATH", which catches manual
                # installs the package managers do not know about.
                if tool.binaries and any(
                    binary in inventories.get("go", set())
                    or binary in inventories.get("github", set())
                    for binary in tool.binaries
                ):
                    found.add(tool.id)

        try:
            for tool_id in found:
                self.state.set_installed(tool_id, True, provider=provider_of.get(tool_id, ""))
            for tool_id in self.state.installed_ids() - found:
                self.state.set_installed(tool_id, False)
        except Exception as exc:
            logger.debug("state reconciliation failed: %s", exc)

        self._installed = found
        self._raw_inventories = inventories
        return found

    def starred(self) -> set[str]:
        return set(self.state.starred_ids())

    def planner(self):
        from ..planner import Planner

        inventories = getattr(self, "_raw_inventories", None)
        if inventories is None:
            self.installed()
            inventories = getattr(self, "_raw_inventories", {})
        return Planner(
            self.catalog,
            statuses=self.provider_status,
            preferred=getattr(self.args, "prefer", None) or [],
            installed=inventories,
        )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _global_flags() -> argparse.ArgumentParser:
    """Flags accepted both before and after the subcommand.

    `loadout show nmap --json` is what people actually type; requiring
    `loadout --json show nmap` is a papercut with no upside.
    """
    shared = argparse.ArgumentParser(add_help=False)
    # SUPPRESS is load-bearing: without it the subparser writes its own default
    # into the namespace and silently overwrites the value the main parser
    # already read from `loadout --json show nmap`.
    shared.add_argument("--json", action="store_true", dest="as_json",
                        default=argparse.SUPPRESS,
                        help="Emit machine-readable JSON.")
    shared.add_argument("--no-emoji", action="store_true",
                        default=argparse.SUPPRESS,
                        help="Use ASCII glyphs instead of symbols.")
    return shared


def build_parser() -> argparse.ArgumentParser:
    shared = _global_flags()
    parser = argparse.ArgumentParser(
        prog="loadout",
        description="Pick your kit, install it anywhere, prove what you used.",
        epilog="Run `loadout` with no arguments for the interactive browser.",
    )
    parser.add_argument("--version", action="version", version=f"loadout {__version__}")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit machine-readable JSON.")
    parser.add_argument("--catalog", help="Use a specific catalog database.")
    parser.add_argument("--offline", action="store_true", help="Make no network calls.")
    parser.add_argument("--log-level", default="WARNING",
                        help="DEBUG, INFO, WARNING, ERROR (default: WARNING).")
    parser.add_argument("--log-file", help="Append log records to this file.")
    parser.add_argument("--theme", default=os.environ.get("LOADOUT_THEME", "default"),
                        choices=["default", "mono", "solarized-dark", "high-contrast"])
    parser.add_argument("--no-emoji", action="store_true",
                        help="Use ASCII glyphs instead of symbols.")
    parser.add_argument("--prefer", action="append", metavar="PROVIDER",
                        help="Prefer this provider when several can install a tool "
                             "(repeatable, first wins).")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Sub)
    _Sub.shared = shared

    # -- browsing ----------------------------------------------------------
    p = sub.add_parser("list", help="List tools.")
    p.add_argument("--category", action="append", default=[])
    p.add_argument("--phase", action="append", default=[])
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--provider", action="append", default=[])
    p.add_argument("--installed", action="store_true")
    p.add_argument("--available", action="store_true")
    p.add_argument("--starred", action="store_true")
    p.add_argument("--limit", type=int, default=0)

    p = sub.add_parser("search", help="Search the catalog.")
    p.add_argument("query", nargs="+")
    p.add_argument("--category", action="append", default=[])
    p.add_argument("--phase", action="append", default=[])
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--limit", type=int, default=40)

    p = sub.add_parser("show", help="Everything known about one tool.")
    p.add_argument("tool")

    p = sub.add_parser("alt", help="Alternatives to a tool, and why.")
    p.add_argument("tool")

    p = sub.add_parser("phase", help="Browse tools by engagement phase.")
    p.add_argument("name", nargs="?")

    sub.add_parser("categories", help="List catalog categories with counts.")
    sub.add_parser("providers", help="Show which installers are usable here.")

    # -- changing the machine ---------------------------------------------
    for verb, helptext in (("install", "Install tools."), ("remove", "Uninstall tools.")):
        p = sub.add_parser(verb, help=helptext)
        p.add_argument("tools", nargs="+")
        p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation.")
        p.add_argument("--dry-run", action="store_true", help="Show the plan and stop.")
        p.add_argument("--provider", help="Force a specific provider.")
        if verb == "install":
            p.add_argument("--allow-unverified", action="store_true",
                           help="Permit downloads with no publishable checksum.")
            p.add_argument("--reinstall", action="store_true",
                           help="Act even if the tool is already installed.")

    p = sub.add_parser("run", help="Run a tool, in a container if it is not installed.")
    p.add_argument("tool")
    p.add_argument("args", nargs=argparse.REMAINDER)

    sub.add_parser("update", help="Refresh package lists and report upgrades.")
    p = sub.add_parser("upgrade", help="Upgrade installed packages.")
    p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    # -- catalog -----------------------------------------------------------
    p = sub.add_parser("catalog", help="Build, refresh and inspect the catalog.")
    csub = p.add_subparsers(dest="catalog_command", required=True)
    csub.add_parser("info", help="Catalog metadata.")
    q = csub.add_parser("build", help="Compile the YAML source tree.")
    q.add_argument("--source", type=Path, default=Path("catalog"))
    q.add_argument("--output", type=Path)
    q.add_argument("--strict", action="store_true", default=True)
    q.add_argument("--no-strict", dest="strict", action="store_false")
    q = csub.add_parser("validate", help="Check the source tree without building.")
    q.add_argument("--source", type=Path, default=Path("catalog"))
    q = csub.add_parser(
        "enrich",
        help="Fill gaps in the YAML source tree from local APT metadata.",
    )
    q.add_argument("--source", type=Path, default=Path("catalog"))
    q.add_argument("--add-new", action="store_true",
                   help="Also add security packages not yet in the tree.")
    q.add_argument("--all-packages", action="store_true",
                   help="With --add-new, include every APT package.")
    q.add_argument("--no-binaries", dest="binaries", action="store_false", default=True,
                   help="Skip dpkg -L binary resolution (much faster).")

    q = csub.add_parser("update", help="Enrich the catalog from local APT metadata.")
    q.add_argument("--all-packages", action="store_true",
                   help="Include every APT package, not just security tooling.")

    # -- loadouts ----------------------------------------------------------
    p = sub.add_parser("loadout", help="Manage named tool sets.")
    lsub = p.add_subparsers(dest="loadout_command", required=True)
    lsub.add_parser("list", help="Available loadouts.")
    q = lsub.add_parser("show", help="What a loadout contains.")
    q.add_argument("slug")
    q = lsub.add_parser("apply", help="Install everything in a loadout.")
    q.add_argument("slug")
    q.add_argument("--yes", "-y", action="store_true")
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("--allow-unverified", action="store_true")
    q = lsub.add_parser("save", help="Snapshot this machine as a loadout.")
    q.add_argument("slug")
    q.add_argument("--output", type=Path)
    q = lsub.add_parser("diff", help="Compare a loadout against this machine.")
    q.add_argument("slug", nargs="?")

    p = sub.add_parser("sync", help="Converge this machine to a loadout manifest.")
    p.add_argument("slug", nargs="?", help="Defaults to ./loadout.yaml")
    p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-unverified", action="store_true")
    p.add_argument("--prune", action="store_true",
                   help="Also remove installed tools the manifest does not list.")

    p = sub.add_parser("completions", help="Print a shell completion script.")
    p.add_argument("shell", choices=list(completions.SHELLS))

    p = sub.add_parser(
        "lock", help="Record what a loadout resolved to, for a reproducible rebuild."
    )
    p.add_argument("slug", nargs="?", help="Defaults to ./loadout.yaml")
    p.add_argument("--check", action="store_true",
                   help="Compare against the lockfile instead of writing it. "
                        "Exits non-zero on any drift.")
    p.add_argument("--output", "-o", type=Path,
                   help=f"Where to write (default ./{lockfile.LOCK_NAME}).")

    # -- state -------------------------------------------------------------
    p = sub.add_parser("history", help="What this tool has done.")
    p.add_argument("--tool")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--clear", action="store_true")

    p = sub.add_parser("report", help="Signed inventory of tools used in a window.")
    p.add_argument("--since", help="ISO date, or Nd / Nh (e.g. 30d).")
    p.add_argument("--until")
    p.add_argument("--all-installed", action="store_true",
                   help="Include every installed tool, not only those used.")
    p.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    p.add_argument("--output", type=Path)

    p = sub.add_parser("audit", help="Flag unmaintained, deprecated or risky tooling.")
    p.add_argument("--limit", type=int, default=0)

    for verb in ("star", "unstar"):
        p = sub.add_parser(verb, help=f"{verb.capitalize()} a tool.")
        p.add_argument("tool")

    for verb in ("hold", "unhold"):
        p = sub.add_parser(verb, help=f"{verb} a package version (apt).")
        p.add_argument("tool")
    sub.add_parser("holds", help="List held packages.")

    # -- export ------------------------------------------------------------
    p = sub.add_parser("export", help="Export the installed set.")
    p.add_argument("--format", choices=["json", "script", "docker", "ansible", "loadout"],
                   default="json")
    p.add_argument("--output", "-o", type=Path)

    # -- offline bundles ---------------------------------------------------
    p = sub.add_parser("bundle", help="Build and install offline kits.")
    bsub = p.add_subparsers(dest="bundle_command", required=True)

    q = bsub.add_parser("create", help="Download a kit for an offline machine.")
    q.add_argument("tools", nargs="*", help="Tools to include.")
    q.add_argument("--loadout", "-l", default="", help="Include a named loadout.")
    q.add_argument("--out", "-o", type=Path, required=True,
                   help="Bundle to write (.tar or .tar.gz).")
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("--allow-unverified", action="store_true",
                   help="Bundle artifacts that publish no checksum.")

    q = bsub.add_parser("inspect", help="Show what a bundle contains.")
    q.add_argument("archive", type=Path)

    q = bsub.add_parser("verify", help="Check a bundle is intact and installable here.")
    q.add_argument("archive", type=Path)

    q = bsub.add_parser("install", help="Install from a bundle, using no network.")
    q.add_argument("archive", type=Path)
    q.add_argument("tools", nargs="*", help="Only these (default: everything in it).")
    q.add_argument("--yes", "-y", action="store_true")
    q.add_argument("--dry-run", action="store_true")

    # -- environment -------------------------------------------------------
    sub.add_parser("doctor", help="Diagnose environment problems.")

    p = sub.add_parser("self-update", help="Update Loadout itself from its git checkout.")
    p.add_argument("--check", action="store_true", help="Only report whether an update is available.")
    p.add_argument("--yes", "-y", action="store_true")

    p = sub.add_parser("verify", help="Check that installed tools actually run.")
    p.add_argument("tools", nargs="*", help="Tools to check (default: everything installed).")
    p.add_argument("--timeout", type=int, default=verify_mod.DEFAULT_TIMEOUT,
                   help=f"Seconds to allow each check (default: {verify_mod.DEFAULT_TIMEOUT}).")
    p.add_argument("--jobs", "-j", type=int, default=verify_mod.DEFAULT_JOBS,
                   help=f"Checks to run at once (default: {verify_mod.DEFAULT_JOBS}).")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="Only report tools that failed.")

    return parser


# ---------------------------------------------------------------------------
# Browsing
# ---------------------------------------------------------------------------


def cmd_list(ctx: Context) -> int:
    args = ctx.args
    tools = ctx.catalog.search(
        "",
        categories=args.category,
        tags=args.tag,
        phases=args.phase,
        providers=args.provider,
    )
    installed = ctx.installed()
    starred = ctx.starred()

    if args.installed:
        tools = [t for t in tools if t.id in installed]
    if args.available:
        tools = [t for t in tools if t.id not in installed]
    if args.starred:
        tools = [t for t in tools if t.id in starred]
    if args.limit > 0:
        tools = tools[: args.limit]

    if ctx.json_mode:
        out.emit_json(
            [
                {**t.to_dict(), "installed": t.id in installed, "starred": t.id in starred}
                for t in tools
            ]
        )
        return 0

    out.render_tool_table(tools, installed=installed, starred=starred)
    out.print_note(f"{len(tools)} tool(s), {len(installed & {t.id for t in tools})} installed")
    return 0


def cmd_search(ctx: Context) -> int:
    args = ctx.args
    query = " ".join(args.query)
    tools = ctx.catalog.search(
        query,
        categories=args.category,
        tags=args.tag,
        phases=args.phase,
        limit=args.limit,
    )
    installed = ctx.installed()

    if ctx.json_mode:
        out.emit_json([{**t.to_dict(), "installed": t.id in installed} for t in tools])
        return 0

    if not tools:
        out.print_note(f"No match for {query!r}.")
        return 1
    out.render_tool_table(tools, installed=installed, starred=ctx.starred())
    out.print_note(f"{len(tools)} match(es) for {query!r}")
    return 0


def cmd_show(ctx: Context) -> int:
    from ..errors import ToolNotFound

    tool = ctx.catalog.get(ctx.args.tool)
    if tool is None:
        raise ToolNotFound(ctx.args.tool, suggestions=ctx.catalog.suggest(ctx.args.tool))

    installed = ctx.installed()
    state = ctx.state.get(tool.id) or {}
    state["installed"] = tool.id in installed

    if ctx.json_mode:
        out.emit_json(
            {
                **tool.to_dict(),
                "installed": state["installed"],
                "installed_version": state.get("version", ""),
                "installed_via": state.get("provider", ""),
                "starred": bool(state.get("starred")),
                "providers_available": {
                    name: status.available for name, status in ctx.provider_status.items()
                },
            }
        )
        return 0

    out.render_detail(tool, status=state, provider_status=ctx.provider_status)
    return 0


def cmd_alt(ctx: Context) -> int:
    from ..errors import ToolNotFound

    tool = ctx.catalog.get(ctx.args.tool)
    if tool is None:
        raise ToolNotFound(ctx.args.tool, suggestions=ctx.catalog.suggest(ctx.args.tool))

    alternatives = list(tool.alternatives)
    inferred = False

    if not alternatives and tool.category != "other":
        # Fall back to catalog neighbours, ranked by how much metadata they
        # share. Deliberately skipped for uncategorised entries: "everything
        # else in `other`" is 600 alphabetical rows, not an answer.
        siblings = [
            t
            for t in ctx.catalog.search("", categories=list(tool.categories))
            if t.id != tool.id and t.summary
        ]

        def overlap(other) -> tuple[int, int, str]:
            shared_tags = len(set(tool.tags) & set(other.tags))
            shared_phases = len(set(tool.phases) & set(other.phases))
            return (-shared_tags, -shared_phases, other.id)

        siblings.sort(key=overlap)
        alternatives = [t.id for t in siblings[:8]]
        inferred = True

    resolved = ctx.catalog.get_many(alternatives)
    installed = ctx.installed()

    if ctx.json_mode:
        out.emit_json(
            {
                "tool": tool.id,
                "deprecated_by": tool.deprecated_by,
                "inferred": inferred,
                "alternatives": [
                    {**t.to_dict(), "installed": t.id in installed} for t in resolved
                ],
                "unresolved": [a for a in alternatives if a not in {t.id for t in resolved}],
            }
        )
        return 0

    if tool.deprecated_by:
        out.print_warn(f"{tool.id} is superseded by {tool.deprecated_by}")

    if not resolved:
        out.print_note(
            f"No alternatives recorded for {tool.id}."
            + (
                "  This entry has not been curated yet — "
                f"`loadout search {tool.id}` may help, and catalog "
                "contributions are welcome."
                if tool.category == "other"
                else ""
            )
        )
        return 0

    label = "Similar tools to" if inferred else "Alternatives to"
    out.print_note(f"{label} {tool.id}:")
    out.render_tool_table(resolved, installed=installed, starred=ctx.starred())
    return 0


def cmd_phase(ctx: Context) -> int:
    from ..catalog.schema import PHASES, phase_label

    if not ctx.args.name:
        counts = dict(ctx.catalog.facet_values("phase"))
        rows = [
            {"phase": slug, "tools": counts.get(slug, 0), "description": description}
            for slug, description in PHASES.items()
            if counts.get(slug, 0) or True
        ]
        if ctx.json_mode:
            out.emit_json(rows)
            return 0
        out.render_table(rows, ["phase", "tools", "description"], title="Engagement phases")
        return 0

    name = ctx.args.name.strip().lower()
    tools = ctx.catalog.search("", phases=[name])
    installed = ctx.installed()
    if ctx.json_mode:
        out.emit_json([{**t.to_dict(), "installed": t.id in installed} for t in tools])
        return 0
    if not tools:
        out.print_note(f"No tools tagged for phase {name!r}.")
        return 1
    out.print_note(f"{phase_label(name)} — {len(tools)} tool(s)")
    out.render_tool_table(tools, installed=installed, starred=ctx.starred())
    return 0


def cmd_categories(ctx: Context) -> int:
    from ..catalog.schema import category_label

    values = ctx.catalog.facet_values("category")
    rows = [
        {"category": slug, "tools": count, "description": category_label(slug)}
        for slug, count in values
    ]
    if ctx.json_mode:
        out.emit_json(rows)
        return 0
    out.render_table(rows, ["category", "tools", "description"], title="Categories")
    return 0


def cmd_providers(ctx: Context) -> int:
    from ..providers import all_providers, detect_distro

    rows = []
    for name, provider in sorted(all_providers().items()):
        status = ctx.provider_status.get(name)
        rows.append(
            {
                "provider": name,
                "available": "yes" if status and status.available else "no",
                "version": (status.version if status else "")[:40],
                "detail": (status.detail if status else "") or provider.label,
            }
        )
    if ctx.json_mode:
        out.emit_json({"distro": detect_distro(), "providers": rows})
        return 0
    out.print_note(f"Detected platform: {detect_distro()}")
    out.render_table(rows, ["provider", "available", "version", "detail"])
    return 0


# ---------------------------------------------------------------------------
# Changing the machine
# ---------------------------------------------------------------------------


def _run_plan(ctx: Context, plan, *, action: str) -> int:
    from ..executor import (
        EVENT_ACTION_DONE,
        EVENT_OUTPUT,
        EVENT_PROGRESS,
        EVENT_VERIFY,
        EVENT_WARN,
        Executor,
        past_tense,
    )
    from ..policy import detect_privilege, refresh_credentials

    args = ctx.args
    dry_run = getattr(args, "dry_run", False)

    if not plan.actions:
        if ctx.json_mode:
            out.emit_json({"ok": True, "results": [], "skipped": plan.to_dict()["skipped"]})
        else:
            for skipped in plan.skipped:
                out.print_note(f"{skipped.tool_id}: {skipped.reason}")
            out.print_note("Nothing to do.")
        return 0

    if ctx.json_mode and dry_run:
        out.emit_json(plan.to_dict())
        return 0

    verbose = dry_run or args.log_level.upper() == "DEBUG"
    if not ctx.json_mode:
        out.print_note(f"Plan ({action}):")
        out.render_plan(plan, verbose=verbose)

    if dry_run:
        out.print_note("Dry run — nothing was changed.")
        return 0

    if not out.confirm(
        f"{action.capitalize()} {len(plan.actions)} tool(s)?",
        assume_yes=getattr(args, "yes", False),
        default=action == "install",
    ):
        out.print_note("Aborted.")
        return 130

    # Prime sudo while the terminal is still ours, before any progress display
    # takes it over. This is what stops a privileged install looking like a hang.
    privilege = detect_privilege()
    if plan.needs_root and not privilege.is_root and not refresh_credentials(privilege):
        out.print_error(
            "Could not obtain sudo credentials.",
            "Check you are in the sudoers file, or re-run as root.",
        )
        return 8

    console = out.get_console()
    progress_state: dict[str, Any] = {"task": None, "progress": None}

    def sink(event) -> None:
        if ctx.json_mode:
            return
        if event.kind == EVENT_PROGRESS and event.percent is not None:
            bar = progress_state.get("progress")
            task = progress_state.get("task")
            if bar is not None and task is not None:
                bar.update(task, completed=event.percent, description=event.message[:48])
        elif event.kind == EVENT_WARN:
            out.print_warn(event.message)
        elif event.kind == EVENT_VERIFY:
            # Always shown, not gated behind --log-level DEBUG like EVENT_OUTPUT
            # below -- a passing checksum/signature check should be as visible
            # as a failing one, not something you only see by asking for it.
            if event.success:
                out.print_ok(f"{event.tool_id}: {event.message} verified")
            else:
                out.print_warn(f"{event.tool_id}: unverified ({event.message})")
        elif event.kind == EVENT_OUTPUT and args.log_level.upper() == "DEBUG":
            console.print(f"    [dim]{event.message}[/dim]", highlight=False)
        elif event.kind == EVENT_ACTION_DONE:
            if event.success:
                out.print_ok(event.message)
            else:
                out.print_error(f"{event.tool_id}: {event.message}")

    executor = Executor(
        sink=sink,
        dry_run=False,
        allow_unverified=getattr(args, "allow_unverified", False),
        privilege=privilege,
        state=ctx.state,
    )

    if ctx.json_mode:
        result = executor.run(plan)
        out.emit_json(result.to_dict())
        return 0 if result.ok else 1

    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[cyan]{task.description}"),
        BarColumn(bar_width=28, complete_style="cyan", finished_style="green"),
        TextColumn("[dim]{task.percentage:>5.1f}%"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as bar:
        progress_state["progress"] = bar
        progress_state["task"] = bar.add_task("preparing", total=100)
        result = executor.run(plan)

    ok = len(result.succeeded)
    failed = len(result.failures)
    if failed:
        out.print_error(f"{ok} succeeded, {failed} failed")
        return 1
    out.print_ok(f"{ok} tool(s) {past_tense(action)}")
    return 0


def cmd_install(ctx: Context) -> int:
    from ..planner import ACTION_INSTALL

    planner = ctx.planner()
    plan = planner.plan(
        ctx.args.tools,
        action=ACTION_INSTALL,
        skip_installed=not getattr(ctx.args, "reinstall", False),
        provider_override=ctx.args.provider or "",
    )
    return _run_plan(ctx, plan, action="install")


def cmd_remove(ctx: Context) -> int:
    from ..planner import ACTION_REMOVE

    planner = ctx.planner()
    plan = planner.plan(
        ctx.args.tools, action=ACTION_REMOVE, provider_override=ctx.args.provider or ""
    )
    return _run_plan(ctx, plan, action="remove")


def cmd_run(ctx: Context) -> int:
    """Run an installed tool, or fall back to its container image."""
    from ..errors import ToolNotFound
    from ..policy import validate_argv

    tool = ctx.catalog.get(ctx.args.tool)
    if tool is None:
        raise ToolNotFound(ctx.args.tool, suggestions=ctx.catalog.suggest(ctx.args.tool))

    if tool.is_content:
        where = tool.paths[0] if tool.paths else ""
        out.print_error(
            f"{tool.id} is content, not a command -- there is nothing to run.",
            f"Its files are at {where}." if where else
            f"See `loadout show {tool.id}` for where its files live.",
        )
        return 4

    extra = [a for a in ctx.args.args if a != "--"]
    binary = tool.primary_binary

    import shutil as _shutil

    if binary and _shutil.which(binary):
        argv = validate_argv([binary, *extra])
        ctx.state.mark_used(tool.id)
        ctx.state.record("run", tool.id, detail=" ".join(extra)[:200])
        return subprocess.run(argv, check=False).returncode  # noqa: S603

    container = next((m for m in tool.install if m.provider == "docker"), None)
    if container is None:
        if not binary:
            out.print_error(
                f"{tool.id} has no known binary in the catalog.",
                "Add a `binaries:` field to its catalog entry.",
            )
        else:
            out.print_error(
                f"{binary} is not installed and {tool.id} has no container image.",
                f"Install it first: loadout install {tool.id}",
            )
        return 4

    from ..providers import get_provider

    provider = get_provider("docker")
    if not ctx.provider_status.get("docker", None) or not ctx.provider_status["docker"].available:
        out.print_error("No container engine available.", "Install docker or podman.")
        return 5

    steps = provider.plan_run(tool, container, extra)  # type: ignore[attr-defined]
    argv = validate_argv(steps[0].argv)
    out.print_note(f"running {tool.id} in a container ({container.spec.get('image')})")
    ctx.state.record("run", tool.id, detail="container")
    return subprocess.run(argv, check=False).returncode  # noqa: S603


def cmd_update(ctx: Context) -> int:
    from ..providers import get_provider

    status = ctx.provider_status.get("apt")
    if not status or not status.available:
        out.print_error("apt is not available on this machine.")
        return 5

    apt = get_provider("apt")
    plan_steps = apt.plan_update()  # type: ignore[attr-defined]

    from ..policy import detect_privilege, elevate, refresh_credentials, subprocess_env

    privilege = detect_privilege()
    if not privilege.is_root and not refresh_credentials(privilege):
        out.print_error("Could not obtain sudo credentials.")
        return 8

    for step in plan_steps:
        argv = elevate(step.argv, privilege=privilege) if step.elevate else step.argv
        result = subprocess.run(argv, env=subprocess_env(), check=False)  # noqa: S603
        if result.returncode != 0:
            out.print_error(f"apt-get update exited {result.returncode}")
            return result.returncode

    upgradable = apt.upgradable()  # type: ignore[attr-defined]
    known = {t.id for t in ctx.catalog.iter_all()}
    relevant = {name: version for name, version in upgradable.items() if name in known}

    if ctx.json_mode:
        out.emit_json({"upgradable": upgradable, "catalog_tools": relevant})
        return 0

    if not upgradable:
        out.print_ok("Everything is up to date.")
        return 0
    out.print_note(f"{len(upgradable)} package(s) upgradable, {len(relevant)} in the catalog:")
    out.render_table(
        [{"tool": k, "version": v} for k, v in sorted(relevant.items())], ["tool", "version"]
    )
    return 0


def cmd_upgrade(ctx: Context) -> int:
    from ..policy import detect_privilege, elevate, refresh_credentials, subprocess_env
    from ..providers import get_provider

    status = ctx.provider_status.get("apt")
    if not status or not status.available:
        out.print_error("apt is not available on this machine.")
        return 5

    steps = get_provider("apt").plan_upgrade()  # type: ignore[attr-defined]
    if ctx.args.dry_run:
        for step in steps:
            out.print_note(f"$ {step.render()}")
        return 0
    if not out.confirm("Upgrade all packages?", assume_yes=ctx.args.yes, default=False):
        return 130

    privilege = detect_privilege()
    if not privilege.is_root and not refresh_credentials(privilege):
        out.print_error("Could not obtain sudo credentials.")
        return 8

    for step in steps:
        argv = elevate(step.argv, privilege=privilege) if step.elevate else step.argv
        result = subprocess.run(argv, env=subprocess_env(), check=False)  # noqa: S603
        if result.returncode != 0:
            return result.returncode
    out.print_ok("Upgrade complete.")
    return 0


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def _apt_available(ctx: Context) -> bool:
    status = ctx.provider_status.get("apt")
    return bool(status and status.available)


def cmd_catalog(ctx: Context) -> int:
    args = ctx.args
    command = args.catalog_command

    if command == "info":
        info = ctx.catalog.info()
        payload = {
            "path": str(ctx.catalog.path),
            "schema": info.schema,
            "generated_at": info.generated_at,
            "source": info.source,
            "revision": info.revision,
            "tools": info.tool_count,
            "categories": dict(ctx.catalog.facet_values("category")),
            "providers": dict(ctx.catalog.facet_values("provider")),
        }
        if ctx.json_mode:
            out.emit_json(payload)
            return 0
        for key in ("path", "schema", "generated_at", "source", "revision", "tools"):
            out.get_console().print(f"[dim]{key:>13}[/dim]  {payload[key]}")
        out.get_console().print(
            f"[dim]{'categories':>13}[/dim]  "
            + ", ".join(f"{k}={v}" for k, v in list(payload["categories"].items())[:8])
        )
        return 0

    if command in ("build", "validate"):
        from ..catalog import compile_tree, load_source_tree

        source = args.source
        if not source.is_dir():
            out.print_error(
                f"No catalog source at {source}",
                "Run this from a checkout of the catalog repository, "
                "or pass --source.",
            )
            return 3

        if command == "validate":
            report = load_source_tree(source, strict=True)
        else:
            destination = args.output or Path("loadout/data/catalog.db")
            report = compile_tree(source, destination, strict=args.strict)

        payload = {
            "files": report.files_read,
            "tools": len(report.tools),
            "errors": report.errors,
            "warnings": report.warnings,
        }
        if ctx.json_mode:
            out.emit_json(payload)
            return 1 if report.errors else 0

        for warning in report.warnings[:20]:
            out.print_warn(warning)
        if len(report.warnings) > 20:
            out.print_note(f"... and {len(report.warnings) - 20} more warning(s)")
        for error in report.errors:
            out.print_error(error)
        if report.errors:
            out.print_error(f"{len(report.errors)} error(s) — catalog not written")
            return 1
        out.print_ok(f"{len(report.tools)} tool(s) from {report.files_read} file(s)")
        return 0

    if command == "enrich":
        from ..catalog import enrich_source_tree

        if not args.source.is_dir():
            out.print_error(
                f"No catalog source at {args.source}",
                "Run this from a checkout of the repository, or pass --source.",
            )
            return 3
        if not _apt_available(ctx):
            out.print_error(
                "apt is not available, so there is no local metadata to read.",
                "Enrichment needs a Debian-family host. CI runs it in a Kali "
                "container; see .github/workflows/catalog.yml.",
            )
            return 5

        if not ctx.json_mode:
            out.print_note(f"Reading APT metadata and updating {args.source}/ ...")
        stats = enrich_source_tree(
            args.source,
            only_security=not args.all_packages,
            resolve_binaries=args.binaries,
            add_new=args.add_new,
        )
        if ctx.json_mode:
            out.emit_json(stats)
            return 0
        out.print_ok(
            f"{stats['changed']} entry(ies) updated, {stats['added']} added "
            f"({stats['entries']} total)"
        )
        out.print_note(
            f"described {stats['described']}/{stats['entries']}, "
            f"categorised {stats['categorised']}/{stats['entries']}"
        )
        if stats["changed"]:
            out.print_note("Next: loadout catalog build --source " + str(args.source))
        return 0

    # update: enrich from local APT metadata
    from ..catalog import build_catalog
    from ..catalog.seed_apt import build_tools, enrich
    from ..paths import catalog_db

    if not _apt_available(ctx):
        out.print_error(
            "apt is not available, so there is no local metadata to read.",
            "On a non-Debian host, use `loadout catalog build` against the "
            "YAML source tree instead.",
        )
        return 5

    if not ctx.json_mode:
        out.print_note("Reading APT metadata...")
    existing = list(ctx.catalog.iter_all())
    enriched = enrich(existing)
    discovered = build_tools(only_security=not args.all_packages)

    by_id = {tool.id: tool for tool in enriched}
    added = 0
    for tool in discovered:
        if tool.id not in by_id:
            by_id[tool.id] = tool
            added += 1

    destination = catalog_db()
    count = build_catalog(
        destination,
        sorted(by_id.values(), key=lambda t: t.id),
        source="apt+yaml",
    )

    try:
        pruned = ctx.state.prune_unknown(by_id.keys())
    except Exception as exc:
        pruned = 0
        if not ctx.json_mode:
            out.print_warn(f"state prune failed: {exc}")

    improved = sum(
        1
        for tool in enriched
        if tool.summary and not next(t for t in existing if t.id == tool.id).summary
    )
    payload = {
        "path": str(destination),
        "tools": count,
        "added": added,
        "descriptions_filled": improved,
        "state_rows_pruned": pruned,
    }
    if ctx.json_mode:
        out.emit_json(payload)
        return 0
    out.print_ok(f"Catalog rebuilt: {count} tools ({added} new) -> {destination}")
    if improved:
        out.print_note(f"Filled in {improved} missing description(s) from APT.")
    if pruned:
        out.print_note(f"Pruned {pruned} orphaned state row(s).")
    return 0


# ---------------------------------------------------------------------------
# Loadouts
# ---------------------------------------------------------------------------


def cmd_loadout(ctx: Context) -> int:
    from .. import loadouts

    args = ctx.args
    command = args.loadout_command

    if command == "list":
        items = loadouts.listing()
        rows = [
            {
                "slug": item.slug,
                "name": item.name,
                "tools": len(item.tools),
                "source": item.source,
                "tags": ", ".join(item.tags),
            }
            for item in items
        ]
        if ctx.json_mode:
            out.emit_json(rows)
            return 0
        out.render_table(rows, ["slug", "name", "tools", "source", "tags"])
        return 0

    if command == "save":
        installed = sorted(ctx.installed())
        if not installed:
            out.print_warn("Nothing is installed, so there is nothing to capture.")
            return 1
        manifest = loadouts.from_installed(args.slug, installed)
        destination = args.output or (loadouts.user_dir() / f"{args.slug}.yaml")
        manifest.write(destination)
        if ctx.json_mode:
            out.emit_json({"slug": manifest.slug, "tools": len(manifest.tools),
                           "path": str(destination)})
            return 0
        out.print_ok(f"Saved {len(manifest.tools)} tool(s) to {destination}")
        out.print_note(f"Reproduce it elsewhere with: loadout sync {manifest.slug}")
        return 0

    target = loadouts.get(args.slug) if getattr(args, "slug", None) else None
    if target is None:
        out.print_error(
            f"No loadout named {getattr(args, 'slug', '')!r}.",
            "See `loadout loadout list`.",
        )
        return 4

    if command == "show":
        resolved = ctx.catalog.get_many(list(target.tools))
        installed_here = ctx.installed()
        if ctx.json_mode:
            out.emit_json(
                {
                    **target.to_dict(),
                    "resolved": [
                        {**t.to_dict(), "installed": t.id in installed_here}
                        for t in resolved
                    ],
                    "unknown": sorted(set(target.tools) - {t.id for t in resolved}),
                }
            )
            return 0
        out.get_console().print(f"[bold]{target.slug}[/bold] — {target.name}")
        if target.description:
            out.print_note(target.description)
        out.render_tool_table(resolved, installed=installed_here, starred=ctx.starred())
        unknown = sorted(set(target.tools) - {t.id for t in resolved})
        if unknown:
            out.print_warn(f"not in catalog: {', '.join(unknown)}")
        return 0

    if command == "diff":
        result = loadouts.diff(target, catalog=ctx.catalog, installed=ctx.installed())
        if ctx.json_mode:
            out.emit_json({"slug": target.slug, **result.to_dict()})
            return 0
        _print_diff(target, result)
        return 0 if result.in_sync else 1

    # apply
    from ..planner import ACTION_INSTALL

    planner = ctx.planner()
    plan = planner.plan(list(target.tools), action=ACTION_INSTALL)
    return _run_plan(ctx, plan, action="install")


def _print_diff(target, result) -> None:
    console = out.get_console()
    console.print(f"[bold]{target.slug}[/bold] — {len(target.tools)} tool(s) declared")
    if result.present:
        out.print_ok(f"{len(result.present)} already installed")
    for tool_id in result.missing:
        console.print(f"  [yellow]+ {tool_id}[/yellow] [dim]missing[/dim]")
    for tool_id in result.unknown:
        console.print(f"  [red]? {tool_id}[/red] [dim]not in catalog[/dim]")
    if result.in_sync:
        out.print_ok("Machine is in sync with this loadout.")


def cmd_sync(ctx: Context) -> int:
    """Converge the machine to a manifest. The flagship workflow."""
    from .. import loadouts
    from ..planner import ACTION_INSTALL, ACTION_REMOVE

    args = ctx.args
    target = None
    if args.slug:
        target = loadouts.get(args.slug)
        if target is None:
            out.print_error(f"No loadout named {args.slug!r}.", "See `loadout loadout list`.")
            return 4
    else:
        target = loadouts.project_manifest()
        if target is None:
            out.print_error(
                f"No {loadouts.PROJECT_MANIFEST} in this directory.",
                "Create one with `loadout loadout save <slug> --output loadout.yaml`, "
                "or name a loadout: `loadout sync <slug>`.",
            )
            return 4

    installed = ctx.installed()
    result = loadouts.diff(target, catalog=ctx.catalog, installed=installed)

    if not ctx.json_mode:
        _print_diff(target, result)

    planner = ctx.planner()
    plan = planner.plan(result.missing, action=ACTION_INSTALL)

    if args.prune and result.extra:
        removal = planner.plan(result.extra, action=ACTION_REMOVE)
        plan.actions.extend(removal.actions)
        plan.skipped.extend(removal.skipped)

    if not plan.actions:
        if ctx.json_mode:
            out.emit_json({"slug": target.slug, "in_sync": True, **result.to_dict()})
        else:
            out.print_ok("Nothing to do — already in sync.")
            _report_lock_drift(ctx, target)
        return 0

    code = _run_plan(ctx, plan, action="install")
    if code == 0:
        _report_lock_drift(ctx, target)
    return code


def _report_lock_drift(ctx: Context, target: Any) -> None:
    """Say whether the converged machine matches the lockfile, if there is one.

    Reported rather than enforced: honouring a pin at install time needs each
    provider to be able to express one, and only `go` can today. Saying "these
    six tools are not the versions the lock records" is the half that a
    disputed finding actually needs, and it is a claim this can support.
    """
    path = lockfile.lock_path()
    if not path.is_file():
        return
    try:
        lock = lockfile.Lock.read(path)
    except (OSError, ValueError) as exc:
        out.print_warn(f"{path.name} is not readable: {exc}")
        return
    installed = ctx.installed() & set(target.tools)
    drifts = lockfile.compare(lock, ctx.state.all_state(), installed=installed)
    if drifts:
        _print_drift(path, drifts)
    else:
        out.print_ok(f"Matches {path.name}.")


# ---------------------------------------------------------------------------
# State, reporting, audit
# ---------------------------------------------------------------------------


def _resolve_lock_target(ctx: Context):
    """The loadout a lock applies to: a named one, or ./loadout.yaml."""
    from .. import loadouts

    slug = getattr(ctx.args, "slug", None)
    if slug:
        target = loadouts.get(slug)
        if target is None:
            out.print_error(f"No loadout named {slug!r}.", "See `loadout loadout list`.")
        return target

    target = loadouts.project_manifest()
    if target is None:
        out.print_error(
            f"No {loadouts.PROJECT_MANIFEST} in this directory.",
            "Name a loadout instead: `loadout lock <slug>`.",
        )
    return target


def cmd_completions(ctx: Context) -> int:
    """Print a completion script for the named shell.

    Written to stdout rather than installed: where these files belong differs
    per shell and per distro, and a tool that writes into someone's shell
    configuration uninvited is a tool people stop trusting.
    """
    print(completions.render(ctx.args.shell, build_parser()), end="")
    return 0


def cmd_lock(ctx: Context) -> int:
    """Write or check ``loadout.lock``.

    A loadout names tool ids; this records what they resolved to on a real
    machine, so "rebuild the box from the engagement repo" and "prove this box
    matches it" stop being different questions with no answer.
    """
    target = _resolve_lock_target(ctx)
    if target is None:
        return 4

    path = ctx.args.output or lockfile.lock_path()
    state = ctx.state.all_state()

    if ctx.args.check:
        if not path.is_file():
            out.print_error(
                f"No {path} to check against.",
                f"Create one with `loadout lock {target.slug}`.",
            )
            return 4
        try:
            lock = lockfile.Lock.read(path)
        except (OSError, ValueError) as exc:
            out.print_error(f"{path} is not readable: {exc}")
            return 4

        # Compared against the loadout's own tools, not the whole machine:
        # every other package on a Kali box is "unlocked" and saying so would
        # bury the drift that matters.
        installed = ctx.installed() & set(target.tools)
        drifts = lockfile.compare(lock, state, installed=installed)
        if ctx.json_mode:
            out.emit_json(
                {
                    "slug": lock.slug,
                    "path": str(path),
                    "in_sync": not drifts,
                    "drift": [d.to_dict() for d in drifts],
                }
            )
            return 0 if not drifts else 1

        if not drifts:
            out.print_ok(f"{len(lock.entries)} tool(s) match {path.name}.")
            return 0
        _print_drift(path, drifts)
        return 1

    lock = lockfile.capture(target.slug, list(target.tools), state)
    if not lock.entries:
        out.print_error(
            f"None of {target.slug}'s tools are installed here.",
            "Run `loadout sync` first -- a lock records what a machine has, "
            "not what it should have.",
        )
        return 4

    lock.write(path)
    unrecorded = [e.tool_id for e in lock.entries.values() if not e.version]
    if ctx.json_mode:
        out.emit_json({"path": str(path), **lock.to_dict(), "no_version": unrecorded})
        return 0

    out.print_ok(f"Wrote {path} — {len(lock.entries)} tool(s).")
    missing_from_lock = sorted(set(target.tools) - set(lock.entries))
    if missing_from_lock:
        out.print_warn(
            f"Not locked (not installed here): {', '.join(missing_from_lock[:8])}"
        )
    if unrecorded:
        out.print_warn(
            f"No version recorded for: {', '.join(sorted(unrecorded)[:8])} — "
            "these cannot be compared later."
        )
    return 0


def _print_drift(path: Path, drifts: list) -> None:
    from ..lockfile import (
        DRIFT_MISSING,
        DRIFT_PROVIDER,
        DRIFT_UNKNOWN,
        DRIFT_UNLOCKED,
        DRIFT_VERSION,
    )

    labels = {
        DRIFT_MISSING: "not installed",
        DRIFT_VERSION: "version differs",
        DRIFT_PROVIDER: "different provider",
        DRIFT_UNLOCKED: "not in the lock",
        DRIFT_UNKNOWN: "no version to compare",
    }
    out.print_error(f"{len(drifts)} difference(s) from {path.name}.")
    for drift in drifts:
        detail = ""
        if drift.expected and drift.actual:
            detail = f"  {drift.expected} -> {drift.actual}"
        elif drift.expected:
            detail = f"  expected {drift.expected}"
        elif drift.actual:
            detail = f"  found {drift.actual}"
        out.print_note(f"  {drift.tool_id:<28} {labels.get(drift.kind, drift.kind)}{detail}")


def cmd_history(ctx: Context) -> int:
    args = ctx.args
    if args.clear:
        removed = ctx.state.clear_history()
        if ctx.json_mode:
            out.emit_json({"cleared": removed})
        else:
            out.print_ok(f"Cleared {removed} history row(s).")
        return 0

    rows = ctx.state.history(tool_id=args.tool, limit=args.limit)
    if ctx.json_mode:
        out.emit_json(rows)
        return 0
    if not rows:
        out.print_note("No history yet.")
        return 0
    out.render_table(
        [
            {
                "when": row["ts"].replace("T", " ").replace("+00:00", "Z"),
                "action": row["action"],
                "tool": row["tool_id"],
                "ok": out.glyph("ok") if row["success"] else out.glyph("fail"),
                "detail": (row.get("detail") or "")[:52],
            }
            for row in rows
        ],
        ["when", "action", "tool", "ok", "detail"],
    )
    return 0


def _parse_since(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    if text.endswith(("d", "h")):
        try:
            amount = int(text[:-1])
        except ValueError:
            return value
        delta = timedelta(days=amount) if text.endswith("d") else timedelta(hours=amount)
        return (datetime.now(timezone.utc) - delta).isoformat(timespec="seconds")
    return value


def _detail_fields(detail: str) -> dict[str, str]:
    """The ``key=value`` tokens the executor writes into a history row."""
    fields: dict[str, str] = {}
    for token in (detail or "").split():
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
    return fields


def _verification_of(events: list[dict[str, Any]], entry: dict[str, Any]) -> dict[str, Any]:
    """How the most recent install in the window checked this tool.

    Falls back to the state row for a tool installed before the window, which
    knows the method but not whether a bypass flag was what allowed it -- so
    that case reports what it knows and does not claim a bypass it cannot see.
    """
    from ..executor import VERIFY_NONE, VERIFY_NOT_APPLICABLE

    installs = [r for r in events if r["action"] == "install"]
    for row in installs:  # newest first
        fields = _detail_fields(row.get("detail", ""))
        method = fields.get("verify", "")
        if not method:
            continue
        return {
            "method": "" if method == VERIFY_NOT_APPLICABLE else method,
            "verified": method not in (VERIFY_NONE, VERIFY_NOT_APPLICABLE),
            "checkable": method != VERIFY_NOT_APPLICABLE,
            "allow_unverified": fields.get("allow_unverified") == "yes",
            "source": "history",
        }

    method = entry.get("verify_method") or ""
    return {
        "method": method,
        "verified": bool(entry.get("verify_ok")),
        "checkable": bool(method),
        "allow_unverified": False,
        "source": "state" if method else "unrecorded",
    }


def _verify_cell(verification: dict[str, Any]) -> str:
    """One column's worth of how a tool was checked.

    Four outcomes, kept apart because three of them are easy to read as a
    fourth: verified, no check of ours to run, a check that found nothing,
    and no record either way. The last is the one an older state file gives,
    and "n/a" would state something about it that isn't known.
    """
    if verification["verified"]:
        return verification["method"] or "yes"
    if verification["source"] == "unrecorded":
        return "—"
    if not verification["checkable"]:
        return "n/a"
    return "no (--allow-unverified)" if verification["allow_unverified"] else "no"


def cmd_report(ctx: Context) -> int:
    """Tool inventory for an engagement window.

    Pentest reports and DFIR chain-of-custody both need "which tools, which
    versions, when". This reads it straight out of the history the executor
    already records, so it is evidence rather than recollection.
    """
    import hashlib
    import json as _json
    import platform

    args = ctx.args
    since = _parse_since(args.since)
    rows = ctx.state.history(since=since, until=args.until, limit=0)
    used = [r for r in rows if r["action"] in ("install", "run", "launch")]

    state = ctx.state.all_state()
    tools: list[dict[str, Any]] = []

    # Default to what was actually touched in the window. Listing every
    # installed package buries the four tools that matter under 380 rows of
    # base system, which is the opposite of what a report is for.
    subjects = {r["tool_id"] for r in used}
    if getattr(args, "all_installed", False):
        subjects |= ctx.installed()

    for tool_id in sorted(subjects):
        entry = state.get(tool_id, {})
        catalog_entry = ctx.catalog.get(tool_id)
        events = [r for r in used if r["tool_id"] == tool_id]
        tools.append(
            {
                "tool": tool_id,
                "summary": catalog_entry.summary if catalog_entry else "",
                "version": entry.get("version", ""),
                "provider": entry.get("provider", ""),
                "installed": bool(entry.get("installed")),
                "first_seen": events[-1]["ts"] if events else "",
                "last_used": entry.get("last_used") or (events[0]["ts"] if events else ""),
                "invocations": len(events),
                "verification": _verification_of(events, entry),
            }
        )

    # The question a challenged finding asks is "could that binary have been
    # something else", so the tools that arrived unchecked are called out
    # rather than left to be spotted in a column.
    unverified = [
        t["tool"]
        for t in tools
        if t["verification"]["checkable"] and not t["verification"]["verified"]
    ]

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"since": since or "all time", "until": args.until or "now"},
        "host": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "loadout_version": __version__,
            "catalog": ctx.catalog.info().generated_at,
        },
        "tools": tools,
        "unverified": unverified,
    }
    digest = hashlib.sha256(
        _json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    payload["scope"] = "all installed" if getattr(args, "all_installed", False) else "used in window"
    payload["integrity"] = {"algorithm": "sha256", "digest": digest}

    if args.format == "json" or ctx.json_mode:
        text = _json.dumps(payload, indent=2, default=str)
    elif args.format == "markdown":
        lines = [
            f"# Tool inventory — {payload['host']['hostname']}",
            "",
            f"Generated {payload['generated_at']} · window: {payload['window']['since']}"
            f" → {payload['window']['until']} · scope: {payload['scope']}",
            "",
            "| Tool | Version | Via | Verified | Last used | Runs |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for tool in tools:
            lines.append(
                f"| {tool['tool']} | {tool['version'] or '—'} | {tool['provider'] or '—'} "
                f"| {_verify_cell(tool['verification'])} "
                f"| {tool['last_used'] or '—'} | {tool['invocations']} |"
            )
        if unverified:
            lines += [
                "",
                "## Installed without verification",
                "",
                "These arrived with nothing to check the download against:",
                "",
            ]
            lines += [f"- `{tool_id}`" for tool_id in unverified]
        lines += ["", f"`sha256:{digest}`"]
        text = "\n".join(lines)
    else:
        lines = [
            f"Tool inventory — {payload['host']['hostname']}",
            f"Generated {payload['generated_at']}",
            f"Window: {payload['window']['since']} -> {payload['window']['until']}",
            f"Scope:  {payload['scope']}",
            "",
        ]
        if not tools:
            lines.append("  (no tool activity recorded in this window)")
        for tool in tools:
            lines.append(
                f"  {tool['tool']:<28} {tool['version'][:18]:<20} "
                f"{tool['provider']:<8} {_verify_cell(tool['verification']):<14} "
                f"runs={tool['invocations']}"
            )
        if unverified:
            lines += [
                "",
                f"Installed without verification ({len(unverified)}):",
                "  " + ", ".join(unverified),
            ]
        lines += ["", f"sha256:{digest}"]
        text = "\n".join(lines)

    if args.output:
        args.output.write_text(text, encoding="utf-8")
        out.print_ok(f"Wrote {args.output} ({len(tools)} tool(s))")
    else:
        print(text)
    return 0


def cmd_audit(ctx: Context) -> int:
    """Flag installed tooling that is deprecated, superseded or unknown."""
    installed = ctx.installed()
    findings: list[dict[str, Any]] = []

    for tool_id in sorted(installed):
        tool = ctx.catalog.get(tool_id)
        if tool is None:
            findings.append(
                {
                    "tool": tool_id,
                    "severity": "info",
                    "issue": "installed but no longer in the catalog",
                    "action": "may have been renamed or dropped upstream",
                }
            )
            continue
        if tool.deprecated_by:
            findings.append(
                {
                    "tool": tool_id,
                    "severity": "warn",
                    "issue": f"superseded by {tool.deprecated_by}",
                    "action": f"loadout install {tool.deprecated_by}",
                }
            )
        state = ctx.state.get(tool_id) or {}
        if not state.get("version"):
            findings.append(
                {
                    "tool": tool_id,
                    "severity": "info",
                    "issue": "no recorded version",
                    "action": "reinstall through loadout to capture provenance",
                }
            )
        for method in tool.install:
            if method.provider == "github" and not method.spec.get("checksums"):
                findings.append(
                    {
                        "tool": tool_id,
                        "severity": "warn",
                        "issue": "GitHub install method publishes no checksum",
                        "action": "add a checksums: field to the catalog entry",
                    }
                )

    if ctx.args.limit > 0:
        findings = findings[: ctx.args.limit]

    if ctx.json_mode:
        out.emit_json({"installed": len(installed), "findings": findings})
        return 0
    if not findings:
        out.print_ok(f"No issues across {len(installed)} installed tool(s).")
        return 0
    out.render_table(findings, ["severity", "tool", "issue", "action"])
    out.print_note(f"{len(findings)} finding(s) across {len(installed)} installed tool(s)")
    return 0


def cmd_star(ctx: Context) -> int:
    starred = ctx.args.command == "star"
    ctx.state.set_starred(ctx.args.tool, starred)
    if ctx.json_mode:
        out.emit_json({"tool": ctx.args.tool, "starred": starred})
        return 0
    out.print_ok(f"{'Starred' if starred else 'Unstarred'} {ctx.args.tool}")
    return 0


def cmd_hold(ctx: Context) -> int:
    from ..policy import detect_privilege, elevate, refresh_credentials, subprocess_env
    from ..providers import get_provider

    status = ctx.provider_status.get("apt")
    if not status or not status.available:
        out.print_error("Holds are an APT feature and apt is not available here.")
        return 5

    apt = get_provider("apt")
    if ctx.args.command == "holds":
        held = sorted(apt.held())  # type: ignore[attr-defined]
        if ctx.json_mode:
            out.emit_json(held)
            return 0
        if not held:
            out.print_note("No packages held.")
            return 0
        for name in held:
            out.get_console().print(f"  {out.glyph('held')} {name}")
        return 0

    hold = ctx.args.command == "hold"
    steps = apt.plan_hold(ctx.args.tool, hold=hold)  # type: ignore[attr-defined]
    privilege = detect_privilege()
    if not privilege.is_root and not refresh_credentials(privilege):
        out.print_error("Could not obtain sudo credentials.")
        return 8
    for step in steps:
        argv = elevate(step.argv, privilege=privilege) if step.elevate else step.argv
        result = subprocess.run(argv, env=subprocess_env(), check=False)  # noqa: S603
        if result.returncode != 0:
            out.print_error(f"apt-mark exited {result.returncode}")
            return result.returncode
    ctx.state.record("hold" if hold else "unhold", ctx.args.tool)
    out.print_ok(f"{ctx.args.tool} {'held' if hold else 'unheld'}")
    return 0


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def cmd_export(ctx: Context) -> int:
    args = ctx.args
    installed = sorted(ctx.installed())
    tools = ctx.catalog.get_many(installed)
    state = ctx.state.all_state()

    if args.format == "json":
        import json as _json

        text = _json.dumps(
            {
                "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "count": len(tools),
                "tools": [
                    {
                        **tool.to_dict(),
                        "installed_version": state.get(tool.id, {}).get("version", ""),
                        "installed_via": state.get(tool.id, {}).get("provider", ""),
                    }
                    for tool in tools
                ],
            },
            indent=2,
        )
    elif args.format == "loadout":
        from .. import loadouts

        text = _yaml_dump(loadouts.from_installed("exported", installed).to_dict())
    elif args.format == "script":
        text = _render_script(tools)
    elif args.format == "docker":
        text = _render_dockerfile(tools)
    else:
        text = _render_ansible(tools)

    if args.output:
        args.output.write_text(text, encoding="utf-8")
        if args.format == "script":
            # An exported install script is meant to be run, so it is
            # deliberately executable.
            args.output.chmod(0o755)
        out.print_ok(f"Wrote {args.output}")
    else:
        print(text)
    return 0


def _yaml_dump(payload: dict[str, Any]) -> str:
    import yaml

    payload.pop("source", None)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)


def _by_provider(tools: list[Any]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for tool in tools:
        if not tool.install:
            continue
        method = tool.install[0]
        key = str(
            method.spec.get("package")
            or method.spec.get("formula")
            or method.spec.get("module")
            or method.spec.get("crate")
            or method.spec.get("gem")
            or tool.id
        )
        grouped.setdefault(method.provider, []).append(key)
    return grouped


def _render_script(tools: list[Any]) -> str:
    grouped = _by_provider(tools)
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by `loadout export --format script`",
        "set -euo pipefail",
        "export DEBIAN_FRONTEND=noninteractive",
        "",
    ]
    if "apt" in grouped:
        lines += [
            "sudo apt-get update -y",
            "sudo apt-get install -y -- \\",
            *[f"  {name} \\" for name in sorted(grouped['apt'])[:-1]],
            f"  {sorted(grouped['apt'])[-1]}",
            "",
        ]
    for provider, command in (
        ("brew", "brew install"),
        ("pipx", "pipx install"),
        ("go", "go install"),
        ("cargo", "cargo install --locked"),
        ("gem", "gem install"),
    ):
        for name in sorted(grouped.get(provider, [])):
            lines.append(f"{command} {shlex.quote(name)}")
    return "\n".join(lines) + "\n"


def _render_dockerfile(tools: list[Any]) -> str:
    grouped = _by_provider(tools)
    lines = [
        "# Generated by `loadout export --format docker`",
        "FROM kalilinux/kali-rolling",
        "ENV DEBIAN_FRONTEND=noninteractive",
        "",
    ]
    if "apt" in grouped:
        packages = " \\\n      ".join(sorted(grouped["apt"]))
        lines += [
            "RUN apt-get update \\",
            f" && apt-get install -y --no-install-recommends \\\n      {packages} \\",
            " && rm -rf /var/lib/apt/lists/*",
            "",
        ]
    if "pipx" in grouped:
        lines += [
            "RUN apt-get update && apt-get install -y pipx && rm -rf /var/lib/apt/lists/*",
            *[f"RUN pipx install {name}" for name in sorted(grouped["pipx"])],
            "",
        ]
    if "go" in grouped:
        lines += [
            "ENV GOBIN=/usr/local/bin",
            *[f"RUN go install {name}" for name in sorted(grouped["go"])],
            "",
        ]
    lines.append('CMD ["/bin/bash"]')
    return "\n".join(lines) + "\n"


def _render_ansible(tools: list[Any]) -> str:
    grouped = _by_provider(tools)
    lines = [
        "# Generated by `loadout export --format ansible`",
        "- name: Provision security tooling",
        "  hosts: all",
        "  become: true",
        "  tasks:",
    ]
    if "apt" in grouped:
        lines += [
            "    - name: Install APT packages",
            "      ansible.builtin.apt:",
            "        name:",
            *[f"          - {name}" for name in sorted(grouped["apt"])],
            "        state: present",
            "        update_cache: true",
        ]
    for provider, module in (("pipx", "community.general.pipx"), ("gem", "community.general.gem")):
        for name in sorted(grouped.get(provider, [])):
            lines += [
                f"    - name: Install {name} ({provider})",
                f"      {module}:",
                f"        name: {name}",
                "        state: present",
            ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def cmd_doctor(ctx: Context) -> int:
    from .. import doctor

    results = doctor.run_all()
    if ctx.json_mode:
        out.emit_json(
            [
                {
                    "check": r.name,
                    "severity": r.severity,
                    "message": r.message,
                    "remediation": r.remediation,
                }
                for r in results
            ]
        )
    else:
        badge = {
            "ok": f"[green]{out.glyph('ok')}[/green]",
            "warn": f"[yellow]{out.glyph('warn')}[/yellow]",
            "fail": f"[red]{out.glyph('fail')}[/red]",
        }
        for result in results:
            out.get_console().print(
                f"{badge.get(result.severity, '?')} [bold]{result.name}[/bold] — {result.message}",
                highlight=False,
            )
            if result.remediation and result.severity != "ok":
                out.get_console().print(
                    f"   [dim]{out.glyph('arrow')} {result.remediation}[/dim]", highlight=False
                )
    worst = doctor.worst_severity(results)
    return {"ok": 0, "warn": 0, "fail": 2}.get(worst, 1)


def cmd_self_update(ctx: Context) -> int:
    """Exactly one JSON document per invocation in --json mode, and a mutating
    pull always needs an explicit --yes there -- an interactive confirm
    prompt has no meaning against a script's stdout."""
    from .. import selfupdate

    def _fail(message: str, *, remediation: str = "", extra: dict[str, Any] | None = None) -> int:
        if ctx.json_mode:
            out.emit_json({"ok": False, "error": message, **(extra or {})})
        else:
            out.print_error(message, remediation=remediation)
        return 1

    repo_root = selfupdate.find_repo_root()
    if repo_root is None:
        return _fail(
            "Loadout isn't running from a git checkout, so it can't update itself.",
            remediation="`git pull` in your checkout, or reinstall from the latest release.",
        )

    status = selfupdate.check_update(repo_root)
    if status.error:
        return _fail(f"Could not check for updates: {status.error}", extra=status.to_dict())

    if not ctx.json_mode:
        out.print_note(
            f"{status.branch}: {status.current_commit[:10]} -> {status.remote_commit[:10]} "
            f"({status.remote_url})"
        )

    check_only = getattr(ctx.args, "check", False)
    if status.up_to_date or check_only:
        if ctx.json_mode:
            out.emit_json(status.to_dict())
        elif status.up_to_date:
            out.print_ok("Loadout is up to date.")
        else:
            out.print_note(f"{status.behind} commit(s) behind.")
        return 0

    if status.dirty:
        return _fail(
            "Local changes would be overwritten.",
            remediation="Commit or stash them first.",
            extra=status.to_dict(),
        )
    if status.ahead:
        return _fail(
            "This checkout has local commits the remote doesn't have.",
            remediation="Resolve manually with git.",
            extra=status.to_dict(),
        )

    assume_yes = getattr(ctx.args, "yes", False)
    if not assume_yes:
        if ctx.json_mode:
            return _fail("confirmation required -- pass --yes", extra=status.to_dict())
        if not out.confirm(f"Pull {status.behind} commit(s) and update Loadout?"):
            out.print_note("Aborted.")
            return 130

    result = selfupdate.apply_update(repo_root, status)
    if ctx.json_mode:
        out.emit_json(
            {
                "ok": result.ok,
                "old_commit": result.old_commit,
                "new_commit": result.new_commit,
                "deps_changed": result.deps_changed,
                "error": result.error,
            }
        )
        return 0 if result.ok else 1

    if not result.ok:
        out.print_error(result.error)
        return 1
    out.print_ok(f"Updated {result.old_commit[:10]} -> {result.new_commit[:10]}")
    if result.deps_changed:
        out.print_note("pyproject.toml changed -- reinstall with: pip install -e '.[dev,tui]'")
    out.print_note("Restart loadout to run the updated code.")
    return 0


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _event_sink(ctx: Context):
    """Progress reporting for plans run outside the install command.

    `cmd_install` wraps its own sink in a rich progress bar built around a
    single tool at a time. Bundle operations run many fetches back to back, so
    they report step by step instead -- but through the same Event stream, so
    there is still only one execution path.
    """
    from ..executor import EVENT_ACTION_DONE, EVENT_ACTION_START, EVENT_WARN

    console = out.get_console()

    def sink(event) -> None:
        if ctx.json_mode:
            return
        if event.kind == EVENT_ACTION_START:
            console.print(f"  [dim]{event.message}[/dim]", highlight=False)
        elif event.kind == EVENT_WARN:
            out.print_warn(event.message)
        elif event.kind == EVENT_ACTION_DONE:
            if event.success:
                out.print_ok(event.message)
            else:
                out.print_error(f"{event.tool_id}: {event.message}")

    return sink


def cmd_bundle(ctx: Context) -> int:
    command = ctx.args.bundle_command
    if command == "create":
        return _bundle_create(ctx)
    if command == "inspect":
        return _bundle_inspect(ctx)
    if command == "verify":
        return _bundle_verify(ctx)
    return _bundle_install(ctx)


def _bundle_tool_ids(ctx: Context) -> list[str]:
    tool_ids = list(ctx.args.tools)
    if ctx.args.loadout:
        from .. import loadouts

        manifest = loadouts.get(ctx.args.loadout)
        if manifest is None:
            out.print_error(f"No loadout named {ctx.args.loadout!r}")
            return []
        tool_ids = list(manifest.tools) + tool_ids
    return tool_ids


def _bundle_create(ctx: Context) -> int:
    import tempfile

    from .. import bundle as bundle_mod
    from ..executor import Executor

    tool_ids = _bundle_tool_ids(ctx)
    if not tool_ids:
        out.print_error("Nothing to bundle.", "Name some tools, or pass --loadout.")
        return 2

    with tempfile.TemporaryDirectory(prefix="loadout-bundle-") as staging:
        root = Path(staging)
        plan, skipped = bundle_mod.plan_fetch(ctx, tool_ids, root)

        if ctx.args.dry_run:
            out.render_plan(plan, verbose=True)
            for entry in skipped:
                out.print_warn(f"{entry.tool_id}: {entry.reason}")
            return 0
        if not plan.actions:
            out.print_error(
                "Nothing in this set can be bundled.",
                "Bundles carry apt packages and verified GitHub releases; "
                "everything else needs a toolchain on the target.",
            )
            for entry in skipped:
                out.print_warn(f"{entry.tool_id}: {entry.reason}")
            return 1

        executor = Executor(
            sink=_event_sink(ctx),
            allow_unverified=ctx.args.allow_unverified,
            state=None,
        )
        result = executor.run(plan)

        bundled = bundle_mod.collect(root, plan)
        manifest = bundle_mod.build_manifest(bundled, skipped, root)
        bundle_mod.write(ctx.args.out, manifest, root)

    size_mb = ctx.args.out.stat().st_size / 1024 / 1024
    files = len(manifest.files)
    if ctx.json_mode:
        out.emit_json({**manifest.to_dict(), "archive": str(ctx.args.out),
                       "bytes": ctx.args.out.stat().st_size})
        return 0 if result.ok else 1

    for entry in manifest.skipped:
        out.print_warn(f"not bundled — {entry.tool_id}: {entry.reason}")

    if not manifest.tools:
        # Never a tick for an empty kit. Someone carries this to a machine with
        # no network and no second chance; "it built fine" is the worst
        # possible thing to have been told.
        out.print_error(
            f"{ctx.args.out} contains nothing.",
            "Every tool was skipped or failed to download — see above.",
        )
        return 1

    out.print_ok(
        f"{ctx.args.out}  —  {len(manifest.tools)} tool(s), {files} file(s), "
        f"{size_mb:.1f} MB"
    )
    out.print_note(
        f"Built for {manifest.distro}/{manifest.arch}. Install with: "
        f"loadout bundle install {ctx.args.out.name}"
    )
    return 0 if result.ok else 1


def _bundle_inspect(ctx: Context) -> int:
    from .. import bundle as bundle_mod

    manifest = bundle_mod.read_manifest(ctx.args.archive)
    if ctx.json_mode:
        out.emit_json(manifest.to_dict())
        return 0

    console = out.get_console()
    console.print(
        f"[bold]{ctx.args.archive.name}[/bold]  [dim]format {manifest.format} · "
        f"built {manifest.created_at} on {manifest.distro}/{manifest.arch} "
        f"by loadout {manifest.loadout_version}[/dim]",
        highlight=False,
    )
    rows = [
        {
            "tool": entry.tool_id,
            "via": entry.provider,
            "files": str(len(entry.files)),
            "size": f"{sum(f.size for f in entry.files) / 1024 / 1024:.1f} MB",
        }
        for entry in manifest.tools
    ]
    if rows:
        out.render_table(rows, ["tool", "via", "files", "size"])
    for entry in manifest.skipped:
        out.print_warn(f"not bundled — {entry.tool_id}: {entry.reason}")
    for warning in bundle_mod.platform_warnings(manifest):
        out.print_warn(warning)
    return 0


def _bundle_verify(ctx: Context) -> int:
    """Check a bundle without installing anything from it.

    Worth having as its own command: the point of carrying a bundle is that
    the far side has no network, so "is this intact" needs answering before
    someone is standing in front of the isolated machine.
    """
    import tempfile

    from .. import bundle as bundle_mod

    with tempfile.TemporaryDirectory(prefix="loadout-verify-") as staging:
        manifest = bundle_mod.extract(ctx.args.archive, Path(staging))
    warnings = bundle_mod.platform_warnings(manifest)

    if ctx.json_mode:
        out.emit_json(
            {"archive": str(ctx.args.archive), "ok": True,
             "files": len(manifest.files), "warnings": warnings}
        )
        return 0

    out.print_ok(
        f"{ctx.args.archive.name}: {len(manifest.files)} file(s) match the manifest"
    )
    for warning in warnings:
        out.print_warn(warning)
    return 0


def _bundle_install(ctx: Context) -> int:
    import tempfile

    from .. import bundle as bundle_mod
    from ..executor import Executor
    from ..policy import refresh_credentials

    with tempfile.TemporaryDirectory(prefix="loadout-bundle-") as staging:
        root = Path(staging)
        manifest = bundle_mod.extract(ctx.args.archive, root)
        for warning in bundle_mod.platform_warnings(manifest):
            out.print_warn(warning)

        plan = bundle_mod.plan_install(ctx, manifest, root, list(ctx.args.tools))
        for entry in plan.skipped:
            out.print_warn(f"{entry.tool_id}: {entry.reason}")
        if not plan.actions:
            out.print_error("Nothing from this bundle can be installed here.")
            return 1

        out.render_plan(plan, verbose=bool(ctx.args.dry_run))
        if ctx.args.dry_run:
            return 0
        if not out.confirm("Install these?", assume_yes=ctx.args.yes):
            return 0
        if plan.needs_root and not refresh_credentials():
            out.print_error("Could not obtain the privileges this install needs.")
            return 8

        executor = Executor(sink=_event_sink(ctx), state=ctx.state)
        result = executor.run(plan)

    if ctx.json_mode:
        out.emit_json(result.to_dict())
    return 0 if result.ok else 1


def cmd_verify(ctx: Context) -> int:
    """Run each tool's catalog verify command and report what actually works.

    Exits non-zero when anything failed, so this is usable as the last line of
    a build script or the check before going on site.
    """
    from ..errors import ToolNotFound
    from ..verify import STATUS_FAILED, STATUS_OK, STATUS_PRESENT, verify_all

    if ctx.args.tools:
        tools = []
        for tool_id in ctx.args.tools:
            tool = ctx.catalog.get(tool_id)
            if tool is None:
                raise ToolNotFound(tool_id, suggestions=ctx.catalog.suggest(tool_id))
            tools.append(tool)
    else:
        installed = ctx.installed()
        tools = [t for t in ctx.catalog.search("", limit=0) if t.id in installed]

    if not tools:
        if ctx.json_mode:
            out.emit_json([])
        else:
            out.print_note("Nothing installed to verify.")
        return 0

    results = verify_all(tools, timeout=ctx.args.timeout, jobs=ctx.args.jobs)

    if ctx.json_mode:
        out.emit_json([r.to_dict() for r in results])
        return 1 if any(r.status == STATUS_FAILED for r in results) else 0

    badge = {
        STATUS_OK: f"[green]{out.glyph('ok')}[/green]",
        STATUS_PRESENT: f"[yellow]{out.glyph('ok')}[/yellow]",
        STATUS_FAILED: f"[red]{out.glyph('fail')}[/red]",
    }
    console = out.get_console()
    for result in results:
        if ctx.args.quiet and result.status != STATUS_FAILED:
            continue
        console.print(
            f"{badge.get(result.status, '[dim]?[/dim]')} [bold]{result.tool_id}[/bold] "
            f"[dim]{result.detail}[/dim]",
            highlight=False,
        )

    failed = [r for r in results if r.status == STATUS_FAILED]
    checked = sum(1 for r in results if r.status == STATUS_OK)
    present = sum(1 for r in results if r.status == STATUS_PRESENT)
    unchecked = len(results) - checked - present - len(failed)

    # "present" and "unchecked" are reported separately from "ok" because they
    # are weaker claims, and a summary that hid the difference would overstate
    # what this run actually proved.
    parts = [f"[green]{checked} verified[/green]"]
    if present:
        parts.append(f"[yellow]{present} on PATH, unverified[/yellow]")
    if unchecked:
        parts.append(f"[dim]{unchecked} not checkable[/dim]")
    if failed:
        parts.append(f"[red]{len(failed)} failed[/red]")
    console.print("  " + " · ".join(parts), highlight=False)

    if present or unchecked:
        out.print_note(
            "Tools with no `verify:` in the catalog can only be checked for "
            "presence on PATH. Adding one is a catalog pull request."
        )
    return 1 if failed else 0


_COMMANDS = {
    "list": cmd_list,
    "search": cmd_search,
    "show": cmd_show,
    "alt": cmd_alt,
    "phase": cmd_phase,
    "categories": cmd_categories,
    "providers": cmd_providers,
    "install": cmd_install,
    "remove": cmd_remove,
    "run": cmd_run,
    "update": cmd_update,
    "upgrade": cmd_upgrade,
    "catalog": cmd_catalog,
    "loadout": cmd_loadout,
    "sync": cmd_sync,
    "lock": cmd_lock,
    "completions": cmd_completions,
    "history": cmd_history,
    "report": cmd_report,
    "audit": cmd_audit,
    "star": cmd_star,
    "unstar": cmd_star,
    "hold": cmd_hold,
    "unhold": cmd_hold,
    "holds": cmd_hold,
    "export": cmd_export,
    "doctor": cmd_doctor,
    "self-update": cmd_self_update,
    "verify": cmd_verify,
    "bundle": cmd_bundle,
}


def _launch_browser(ctx: Context) -> int:
    from .tui.app import run_tui, textual_available

    if not textual_available():
        out.print_warn(
            "Textual is not installed, so the interactive browser is unavailable."
        )
        out.print_note("Install it with: pipx inject loadout textual")
        out.print_note("Meanwhile, try: loadout list  |  loadout search <term>")
        return 1
    return run_tui(ctx)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(args.log_level, log_file=args.log_file)
    configure_console(theme=args.theme, no_emoji=args.no_emoji)
    if args.offline:
        os.environ["LOADOUT_OFFLINE"] = "1"
    if args.no_emoji:
        os.environ["LOADOUT_NO_EMOJI"] = "1"

    ctx = Context(args=args)

    try:
        if args.command is None:
            return _launch_browser(ctx)
        handler = _COMMANDS.get(args.command)
        if handler is None:
            parser.print_help()
            return 2
        return handler(ctx) or 0
    except LoadoutError as exc:
        if ctx.json_mode:
            out.emit_json({"error": exc.message, "remediation": exc.remediation})
        else:
            out.print_error(exc.message, exc.remediation)
        return exc.exit_code
    except KeyboardInterrupt:
        out.print_note("\nInterrupted.")
        return 130
    except BrokenPipeError:
        # `loadout list | head` closes the pipe under us.
        _silence_broken_pipe()
        return 0
    except Exception as exc:
        if args.log_level.upper() == "DEBUG":
            raise
        out.print_error(
            f"Unexpected error: {exc}",
            "Re-run with --log-level DEBUG for the full traceback.",
        )
        return 1


def _silence_broken_pipe() -> None:
    """Point stdout at devnull so interpreter shutdown cannot re-raise.

    Catching BrokenPipeError inside main() is not enough: CPython flushes
    sys.stdout again during shutdown, after every handler has returned, and
    prints "Exception ignored on flushing sys.stdout" straight to stderr.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def cli() -> None:
    """Console-script entry point."""
    try:
        code = main()
    except BrokenPipeError:
        _silence_broken_pipe()
        code = 0
    raise SystemExit(code)

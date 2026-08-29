"""Run a :class:`~loadout.planner.Plan` and stream events.

The executor never prints. It emits :class:`Event` objects and the UI decides
how to render them, which is why the same execution path drives the CLI, the TUI
and ``--json`` output without duplicated logic.

Real progress comes from APT's own status file descriptor rather than counting
output lines, so a large install no longer sits at "95%" for several minutes.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import LoadoutError, PrivilegeError
from .planner import ACTION_INSTALL, Plan, PlannedAction
from .policy import Privilege, detect_privilege, elevate, subprocess_env
from .providers.apt import AptProvider, apt_status_fd_args
from .providers.base import CommandStep, PythonStep, Step

logger = logging.getLogger("loadout.executor")

EVENT_PLAN_START = "plan_start"
EVENT_ACTION_START = "action_start"
EVENT_PROGRESS = "progress"
EVENT_OUTPUT = "output"
EVENT_WARN = "warn"
EVENT_ACTION_DONE = "action_done"
EVENT_PLAN_DONE = "plan_done"

#: Past tense per action. f"{action}ed" gives "removeed", which every user who
#: removes a tool would see.
_PAST_TENSE = {"install": "installed", "remove": "removed"}


def past_tense(action: str) -> str:
    return _PAST_TENSE.get(action, f"{action}ed")


@dataclass
class Event:
    kind: str
    message: str = ""
    tool_id: str = ""
    percent: float | None = None
    success: bool = True
    detail: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[Event], None]


@dataclass
class ActionResult:
    tool_id: str
    action: str
    provider: str
    success: bool
    elapsed: float
    error: str = ""
    version: str = ""


@dataclass
class ExecResult:
    results: list[ActionResult] = field(default_factory=list)
    dry_run: bool = False

    @property
    def failures(self) -> list[ActionResult]:
        return [r for r in self.results if not r.success]

    @property
    def succeeded(self) -> list[ActionResult]:
        return [r for r in self.results if r.success]

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "ok": self.ok,
            "results": [
                {
                    "tool": r.tool_id,
                    "action": r.action,
                    "provider": r.provider,
                    "success": r.success,
                    "elapsed_seconds": round(r.elapsed, 2),
                    "error": r.error,
                    "version": r.version,
                }
                for r in self.results
            ],
        }


@dataclass
class ExecContext:
    """Handed to :class:`PythonStep` callables so they can report progress."""

    emit: EventSink
    allow_unverified: bool = False
    dry_run: bool = False
    tool_id: str = ""

    def progress(self, message: str, percent: float | None = None) -> None:
        self.emit(
            Event(EVENT_PROGRESS, message=message, percent=percent, tool_id=self.tool_id)
        )

    def warn(self, message: str) -> None:
        self.emit(Event(EVENT_WARN, message=message, tool_id=self.tool_id))

    def output(self, line: str) -> None:
        self.emit(Event(EVENT_OUTPUT, message=line, tool_id=self.tool_id))


class Executor:
    def __init__(
        self,
        *,
        sink: EventSink | None = None,
        dry_run: bool = False,
        allow_unverified: bool = False,
        privilege: Privilege | None = None,
        state=None,
    ) -> None:
        self.sink: EventSink = sink or (lambda event: None)
        self.dry_run = dry_run
        self.allow_unverified = allow_unverified
        self.privilege = privilege or detect_privilege()
        self.state = state
        #: Ring buffer of the last lines a step printed, for failure messages.
        self._recent_output: deque[str] = deque(maxlen=40)

    # -- entry point -------------------------------------------------------

    def run(self, plan: Plan) -> ExecResult:
        result = ExecResult(dry_run=self.dry_run)
        self.sink(
            Event(
                EVENT_PLAN_START,
                message=f"{len(plan.actions)} action(s)",
                detail={"needs_root": plan.needs_root, "dry_run": self.dry_run},
            )
        )

        if plan.needs_root and not self.dry_run and not self.privilege.can_elevate:
            raise PrivilegeError(
                "This plan needs root but neither root nor sudo is available.",
                remediation="Install sudo, or re-run as root.",
            )

        for action in plan.actions:
            if not action.steps:
                # Coalesced into an earlier action; still record the outcome.
                result.results.append(
                    ActionResult(action.tool.id, action.action, action.provider, True, 0.0)
                )
                continue
            result.results.append(self._run_action(action))

        self.sink(
            Event(
                EVENT_PLAN_DONE,
                success=result.ok,
                message=f"{len(result.succeeded)} ok, {len(result.failures)} failed",
            )
        )
        return result

    # -- per action --------------------------------------------------------

    def _run_action(self, action: PlannedAction) -> ActionResult:
        started = time.monotonic()
        self.sink(
            Event(
                EVENT_ACTION_START,
                tool_id=action.tool.id,
                message=f"{action.action} {action.tool.id} via {action.provider}",
                detail={"provider": action.provider, "action": action.action},
            )
        )

        context = ExecContext(
            emit=self.sink,
            allow_unverified=self.allow_unverified,
            dry_run=self.dry_run,
            tool_id=action.tool.id,
        )

        error = ""
        success = True
        try:
            for step in action.steps:
                self._run_step(step, context)
        except LoadoutError as exc:
            success = False
            error = exc.message
        except Exception as exc:
            success = False
            error = str(exc)
            logger.exception("step failed for %s", action.tool.id)

        elapsed = time.monotonic() - started
        version = ""
        if success and not self.dry_run:
            version = self._record(action, elapsed)

        self.sink(
            Event(
                EVENT_ACTION_DONE,
                tool_id=action.tool.id,
                success=success,
                message=error or f"{past_tense(action.action)} {action.tool.id}",
                percent=100.0,
                detail={"elapsed": elapsed},
            )
        )
        return ActionResult(
            tool_id=action.tool.id,
            action=action.action,
            provider=action.provider,
            success=success,
            elapsed=elapsed,
            error=error,
            version=version,
        )

    def _record(self, action: PlannedAction, elapsed: float) -> str:
        """Persist the outcome, including the version -- this is what makes
        `loadout report` able to state exactly what was used and when."""
        if self.state is None:
            return ""
        version = ""
        try:
            from .providers import get_provider

            provider = get_provider(action.provider)
            version = provider.installed_version(action.tool, action.method) or ""
        except Exception:
            version = ""
        try:
            installed = action.action == ACTION_INSTALL
            self.state.set_installed(
                action.tool.id,
                installed,
                provider=action.provider,
                version=version,
            )
            self.state.record(
                action.action,
                action.tool.id,
                success=True,
                detail=f"provider={action.provider} elapsed={elapsed:.1f}s version={version}",
            )
        except Exception as exc:  # pragma: no cover - state is best effort
            logger.debug("state record failed: %s", exc)
        return version

    # -- per step ----------------------------------------------------------

    def _run_step(self, step: Step, context: ExecContext) -> None:
        if isinstance(step, PythonStep):
            if self.dry_run:
                context.progress(f"[dry-run] {step.render()}")
                return
            step.fn(context)
            return

        if not isinstance(step, CommandStep):  # pragma: no cover - guard
            raise LoadoutError(f"unknown step type: {type(step).__name__}")

        argv = list(step.argv)
        if step.elevate:
            argv = elevate(argv, privilege=self.privilege)

        if self.dry_run:
            context.progress(f"[dry-run] {' '.join(argv)}")
            return

        context.progress(step.description, 0.0)
        self._recent_output.clear()
        returncode = self._spawn(argv, step, context)
        if returncode != 0 and step.check:
            # Show what the tool actually said. Reporting only "exited 100"
            # makes the user re-run the command by hand to learn anything.
            tail = [line for line in self._recent_output if line.strip()][-4:]
            detail = ("\n  " + "\n  ".join(tail)) if tail else ""
            raise LoadoutError(
                f"`{Path(argv[0]).name}` exited {returncode}{detail}",
                remediation=_troubleshoot(argv),
            )

    def _spawn(self, argv: list[str], step: CommandStep, context: ExecContext) -> int:
        env = subprocess_env(step.env)
        status_reader: threading.Thread | None = None
        read_fd = write_fd = -1
        use_status_fd = (argv and "apt-get" in argv[0]) or "apt-get" in argv[:2]

        if use_status_fd and hasattr(os, "pipe") and os.name == "posix":
            read_fd, write_fd = os.pipe()
            argv = _insert_options(argv, apt_status_fd_args(write_fd))

        try:
            process = subprocess.Popen(  # noqa: S603 - argv validated by policy
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
                pass_fds=(write_fd,) if write_fd != -1 else (),
            )
        except FileNotFoundError as exc:
            if write_fd != -1:
                os.close(write_fd)
                os.close(read_fd)
            raise LoadoutError(
                f"{argv[0]}: command not found",
                remediation="The provider reported itself available but its "
                "executable has since disappeared from PATH.",
            ) from exc

        if write_fd != -1:
            os.close(write_fd)
            status_reader = threading.Thread(
                target=self._pump_status, args=(read_fd, context), daemon=True
            )
            status_reader.start()

        # Not an assert: `python -O` strips those, and this sits in the
        # privileged execution path. Popen(stdout=PIPE) always yields a stream,
        # so this is really narrowing for the type checker.
        stdout = process.stdout
        if stdout is None:  # pragma: no cover - unreachable with stdout=PIPE
            process.kill()
            raise LoadoutError(f"{argv[0]}: could not capture output")
        for line in stdout:
            text = line.rstrip("\n")
            if text:
                context.output(text)
        process.wait()

        if status_reader is not None:
            status_reader.join(timeout=2)
        return process.returncode

    @staticmethod
    def _pump_status(read_fd: int, context: ExecContext) -> None:
        """Translate APT's status-fd records into progress events."""
        try:
            with os.fdopen(read_fd, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    parsed = AptProvider.parse_status_line(line)
                    if parsed is None:
                        continue
                    percent, message = parsed
                    context.progress(message, percent)
        except OSError:
            pass


def _insert_options(argv: list[str], options: list[str]) -> list[str]:
    """Insert *options* before any ``--`` separator.

    Appending them instead puts them where the package manager expects package
    names, which is how `apt-get ... -- nmap -o APT::Status-Fd=7` came to mean
    "install three packages, two of which do not exist".
    """
    try:
        cut = argv.index("--")
    except ValueError:
        return [*argv, *options]
    return [*argv[:cut], *options, *argv[cut:]]


def _troubleshoot(argv: list[str]) -> str:
    """Advice specific to the failing command, not a wall of generic tips."""
    joined = " ".join(argv)
    if "apt-get" in joined:
        return (
            "Common causes: another apt process holds the lock "
            "(check with `loadout doctor`), the package lists are stale "
            "(`loadout update`), or the package name changed in this release."
        )
    if "go install" in joined:
        return "Check that GOPATH/bin is on your PATH and the module path is correct."
    if joined.startswith("docker") or joined.startswith("podman"):
        return "Check the container daemon is running and you can reach the registry."
    return "Re-run with --log-level DEBUG to see the full command output."

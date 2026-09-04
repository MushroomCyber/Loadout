"""Run a :class:`~loadout.planner.Plan` and stream events.

The executor never prints. It emits :class:`Event` objects and the UI decides
how to render them, which is why the same execution path drives the CLI, the TUI
and ``--json`` output without duplicated logic.

Real progress comes from APT's own status file descriptor rather than counting
output lines, so a large install no longer sits at "95%" for several minutes.
It is pointed at fd 1 (the process's own stdout) rather than a separate pipe
passed via ``pass_fds`` -- a real install as a non-root sudo user hit
``E: Write error - write (9: Bad file descriptor)`` on every status write with
the pipe approach, which apt then treated as fatal and exited non-zero even
though dpkg had already finished (confirmed by cross-checking ``dpkg -l``,
which showed the package correctly installed). fd 1 sidesteps the whole class
of problem: it is guaranteed open and inherited by exec(), no ``pass_fds``
book-keeping or extra thread required, regardless of what sudo, PAM or an
LSM policy on a given box do to file descriptors above 2. APT interleaves its
normal human-readable output with the machine-readable ``pmstatus:`` lines on
the same stream; the parser below already ignores anything that is not one.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import LoadoutError, NothingToVerifyAgainst, PrivilegeError
from .planner import ACTION_FETCH, ACTION_INSTALL, Plan, PlannedAction
from .policy import Privilege, deliberate_env, detect_privilege, elevate, subprocess_env
from .providers.apt import AptProvider, apt_status_fd_args
from .providers.base import CommandStep, PythonStep, Step

logger = logging.getLogger("loadout.executor")

EVENT_PLAN_START = "plan_start"
EVENT_ACTION_START = "action_start"
EVENT_PROGRESS = "progress"
EVENT_OUTPUT = "output"
EVENT_WARN = "warn"
EVENT_VERIFY = "verify"
EVENT_ACTION_DONE = "action_done"
EVENT_PLAN_DONE = "plan_done"

#: Past tense per action. f"{action}ed" gives "removeed", which every user who
#: removes a tool would see.
_PAST_TENSE = {"install": "installed", "remove": "removed", "fetch": "fetched"}


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
    #: This failed only because nothing was published to check the download
    #: against, so a caller may offer to install it unverified. A failed check
    #: never sets this: those bytes are wrong, and no flag makes them right.
    waivable: bool = False


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
                    "waivable": r.waivable,
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
    #: Every verification a provider reported for this action, in order --
    #: e.g. [("signature", True), ("checksum", True)] or [("checksum", False)]
    #: for "no checksum published". Read back by the executor to persist a
    #: summary and by the TUI to show it outside the scrolling log.
    verify_checks: list[tuple[str, bool]] = field(default_factory=list)

    def progress(self, message: str, percent: float | None = None) -> None:
        self.emit(
            Event(EVENT_PROGRESS, message=message, percent=percent, tool_id=self.tool_id)
        )

    def warn(self, message: str) -> None:
        self.emit(Event(EVENT_WARN, message=message, tool_id=self.tool_id))

    def output(self, line: str) -> None:
        self.emit(Event(EVENT_OUTPUT, message=line, tool_id=self.tool_id))

    def verified(self, method: str, ok: bool) -> None:
        """Record a verification outcome (checksum, gpg signature, ...).

        Separate from :meth:`output`/:meth:`warn` because those lines scroll
        out of a long install log -- this is read back after the run to show
        a verification badge that does not.
        """
        self.verify_checks.append((method, ok))
        self.emit(Event(EVENT_VERIFY, message=method, success=ok, tool_id=self.tool_id))


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
        waivable = False
        try:
            for step in action.steps:
                self._run_step(step, context)
        except LoadoutError as exc:
            success = False
            error = exc.message
            waivable = isinstance(exc, NothingToVerifyAgainst)
        except Exception as exc:
            success = False
            error = str(exc)
            logger.exception("step failed for %s", action.tool.id)

        elapsed = time.monotonic() - started
        version = ""
        # A fetch downloads into a staging directory and installs nothing, so
        # recording it as installed state would make `loadout list --installed`
        # lie on the machine that built the bundle.
        if success and not self.dry_run and action.action != ACTION_FETCH:
            version = self._record(action, elapsed, context)

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
            waivable=waivable,
        )

    def _record(self, action: PlannedAction, elapsed: float, context: ExecContext) -> str:
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
        verify_method, verify_ok = _summarize_verification(context.verify_checks)
        try:
            installed = action.action == ACTION_INSTALL
            self.state.set_installed(
                action.tool.id,
                installed,
                provider=action.provider,
                version=version,
                verification=(verify_method, verify_ok),
            )
            audit = verification_detail(
                context.verify_checks, allow_unverified=self.allow_unverified
            )
            self.state.record(
                action.action,
                action.tool.id,
                success=True,
                detail=(
                    f"provider={action.provider} elapsed={elapsed:.1f}s "
                    f"version={version} {audit}"
                ),
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
            # The env we set below is applied to the *sudo* process, and sudo's
            # env_reset drops it before exec'ing the real command. Name it here
            # so elevate() can carry it across.
            argv = elevate(
                argv,
                privilege=self.privilege,
                preserve=deliberate_env(step.env),
            )

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
            # step.argv, not argv: the latter now starts with the sudo/env
            # wrapper, and "`sudo` exited 100" names the wrong program.
            raise LoadoutError(
                f"`{Path(step.argv[0]).name}` exited {returncode}{detail}",
                remediation=_troubleshoot(list(step.argv)),
            )

    def _spawn(self, argv: list[str], step: CommandStep, context: ExecContext) -> int:
        env = subprocess_env(step.env)
        # Decided from the step, not from `argv`: by the time it reaches here
        # argv may be wrapped in `sudo env VAR=value ...`, and looking for
        # apt-get in the first two tokens then finds the wrapper instead. The
        # step knows what it is regardless of how it was elevated.
        use_status_fd = bool(step.argv) and Path(step.argv[0]).name == "apt-get"

        if use_status_fd:
            # fd 1 is our own stdout -- already piped below, always open, and
            # inherited by exec() with no pass_fds bookkeeping. See the module
            # docstring for why this replaced a separate pipe.
            argv = _insert_options(argv, apt_status_fd_args(1))

        try:
            process = subprocess.Popen(  # noqa: S603 - argv validated by policy
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
            )
        except FileNotFoundError as exc:
            raise LoadoutError(
                f"{argv[0]}: command not found",
                remediation="The provider reported itself available but its "
                "executable has since disappeared from PATH.",
            ) from exc

        # Not an assert: `python -O` strips those, and this sits in the
        # privileged execution path. Popen(stdout=PIPE) always yields a stream,
        # so this is really narrowing for the type checker.
        stdout = process.stdout
        if stdout is None:  # pragma: no cover - unreachable with stdout=PIPE
            process.kill()
            raise LoadoutError(f"{argv[0]}: could not capture output")
        for line in stdout:
            text = line.rstrip("\n")
            if not text:
                continue
            parsed = AptProvider.parse_status_line(text) if use_status_fd else None
            if parsed is not None:
                percent, message = parsed
                context.progress(message, percent)
            else:
                self._recent_output.append(text)
                context.output(text)
        process.wait()
        return process.returncode


def _summarize_verification(checks: list[tuple[str, bool]]) -> tuple[str, bool]:
    """Collapse an action's verification events into one (method, ok) pair.

    A method that passed wins over one that didn't -- a github install that
    checked a signature and then found no separate checksum file is still
    verified overall, not "unverified" because the second check had nothing
    to check. An empty result means the provider never called `verified()`
    at all (apt and friends have no such step, not an unverified one).
    """
    if not checks:
        return "", False
    passed = [method for method, ok in checks if ok]
    if passed:
        return "+".join(passed), True
    return checks[-1][0], False


#: No verification step of ours ran, which is not the same as one that ran and
#: found nothing: apt verifies its own package signatures, and reporting that
#: as an unverified install would bury the ones that really are.
VERIFY_NOT_APPLICABLE = "n/a"

#: A check ran and had nothing to check against -- a github release with no
#: published checksum and no signature. Only reachable at all because
#: ``--allow-unverified`` was passed; without it the install refuses.
VERIFY_NONE = "none"


def verification_detail(
    checks: list[tuple[str, bool]], *, allow_unverified: bool
) -> str:
    """The audit fact written into the history row, as ``key=value`` tokens.

    Without this the state row is the only record of how a binary was checked,
    and it holds one row per tool -- overwritten by the next install and gone
    for anything since removed. A report asked months later which binaries
    arrived unchecked has to read a log of what happened, not a snapshot of
    what is currently true.
    """
    method, ok = _summarize_verification(checks)
    if not checks:
        return f"verify={VERIFY_NOT_APPLICABLE}"
    if ok:
        return f"verify={method}"
    if allow_unverified:
        return f"verify={VERIFY_NONE} allow_unverified=yes"
    return f"verify={VERIFY_NONE}"


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

"""Smoke-test installed tools.

An install that reported success is not the same as a tool that runs. A Go
binary can land somewhere off ``PATH``; an apt package can install while its
interpreter does not; a release archive can extract the wrong architecture. All
three report "installed" and all three fail at the worst moment, which for this
audience is on a client site with the engagement clock running.

The catalog already carries the answer. Entries may set ``verify:`` -- ``nmap
--version``, ``ffuf -V`` -- a command that exits 0 when the tool works. This
module runs it and reports what happened.

Four outcomes, kept distinct because collapsing them would overstate what was
actually proven:

``ok``
    The catalog's verify command ran and exited 0. The tool works.
``present``
    No verify command, but the tool's binary is on ``PATH``. Weaker: it says
    the file is findable, not that it runs.
``failed``
    The verify command exited non-zero, or the binary is missing.
``unchecked``
    The catalog knows neither a verify command nor a binary name.

Safety: the command comes from the catalog, so it is split with :mod:`shlex`
rather than handed to a shell, validated by :func:`loadout.policy.validate_argv`
before it becomes an argv, and never elevated. Only tools the caller asks about
are run, so a stale catalog entry cannot cause an install-wide sweep of
subprocesses.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .errors import UnsafeArgument
from .policy import subprocess_env, validate_argv

logger = logging.getLogger("loadout.verify")

STATUS_OK = "ok"
STATUS_PRESENT = "present"
STATUS_FAILED = "failed"
STATUS_UNCHECKED = "unchecked"

#: Enough for a slow interpreter to start and print a version, short enough
#: that one hung tool does not hold up the whole run.
DEFAULT_TIMEOUT = 20

#: Verify commands are independent subprocesses that spend their time waiting
#: on exec and I/O, so a small thread pool turns a minute of checks into a few
#: seconds. Capped low: this may run on a laptop mid-engagement.
DEFAULT_JOBS = 8


@dataclass
class VerifyResult:
    tool_id: str
    status: str
    detail: str = ""
    command: str = ""
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_OK, STATUS_PRESENT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool_id,
            "status": self.status,
            "detail": self.detail,
            "command": self.command,
            "elapsed_seconds": round(self.elapsed, 2),
        }


def verify_tool(tool: Any, *, timeout: int = DEFAULT_TIMEOUT) -> VerifyResult:
    """Run one tool's verify command, or fall back to a PATH check."""
    started = time.monotonic()

    if not tool.verify:
        return _without_a_command(tool, time.monotonic() - started)

    try:
        argv = validate_argv(shlex.split(tool.verify))
    except (UnsafeArgument, ValueError) as exc:
        # A catalog entry that cannot be turned into an argv is a catalog bug,
        # not a broken install -- say so rather than blaming the tool.
        return VerifyResult(
            tool_id=tool.id,
            status=STATUS_FAILED,
            detail=f"catalog verify command is not runnable: {exc}",
            command=tool.verify,
            elapsed=time.monotonic() - started,
        )

    if shutil.which(argv[0]) is None:
        return VerifyResult(
            tool_id=tool.id,
            status=STATUS_FAILED,
            detail=f"{argv[0]}: not found on PATH",
            command=tool.verify,
            elapsed=time.monotonic() - started,
        )

    try:
        completed = subprocess.run(  # noqa: S603 - argv validated, no shell
            argv,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=subprocess_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return VerifyResult(
            tool_id=tool.id,
            status=STATUS_FAILED,
            detail=f"timed out after {timeout}s",
            command=tool.verify,
            elapsed=time.monotonic() - started,
        )
    except OSError as exc:  # pragma: no cover - which() already screened this
        return VerifyResult(
            tool_id=tool.id,
            status=STATUS_FAILED,
            detail=str(exc),
            command=tool.verify,
            elapsed=time.monotonic() - started,
        )

    elapsed = time.monotonic() - started
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode == 0:
        return VerifyResult(
            tool_id=tool.id,
            status=STATUS_OK,
            detail=_first_meaningful_line(output),
            command=tool.verify,
            elapsed=elapsed,
        )
    return VerifyResult(
        tool_id=tool.id,
        status=STATUS_FAILED,
        detail=f"exited {completed.returncode}: {_first_meaningful_line(output)}",
        command=tool.verify,
        elapsed=elapsed,
    )


def _content_on_disk(tool: Any, elapsed: float) -> VerifyResult:
    """Check a content entry's `paths:` instead of looking for a command.

    A wordlist has no binary and never will, so the PATH fallback reported
    every content entry as `failed` -- accusing a perfectly good 1.8 GB
    install of being broken because it did not ship a command.
    """
    from pathlib import Path as _Path

    paths = [_Path(p) for p in tool.paths]
    if not paths:
        return VerifyResult(
            tool_id=tool.id,
            status=STATUS_UNCHECKED,
            detail="catalog records no paths for this content",
            elapsed=elapsed,
        )

    missing = [p for p in paths if not p.exists()]
    if missing:
        return VerifyResult(
            tool_id=tool.id,
            status=STATUS_FAILED,
            detail=f"missing: {', '.join(str(p) for p in missing[:3])}",
            elapsed=elapsed,
        )
    # `present`, not `ok`: the directory exists. Whether its contents are the
    # ones the catalog meant is a claim only a checksum could support.
    return VerifyResult(
        tool_id=tool.id,
        status=STATUS_PRESENT,
        detail=f"{paths[0]} exists",
        elapsed=elapsed,
    )


def _without_a_command(tool: Any, elapsed: float) -> VerifyResult:
    """No `verify:` in the catalog -- fall back to looking for the binary.

    Reported as ``present`` rather than ``ok`` on purpose: finding a file on
    PATH is a weaker claim than running it, and a report that blurs the two is
    worth less than one that admits the difference.
    """
    if getattr(tool, "is_content", False):
        return _content_on_disk(tool, elapsed)

    binary = tool.primary_binary
    if binary:
        found = shutil.which(binary)
        if found:
            return VerifyResult(
                tool_id=tool.id,
                status=STATUS_PRESENT,
                detail=f"{binary} on PATH ({found}); no verify command in the catalog",
                elapsed=elapsed,
            )
        # The catalog named this binary, so its absence is a real failure.
        return VerifyResult(
            tool_id=tool.id,
            status=STATUS_FAILED,
            detail=f"{binary}: not found on PATH",
            elapsed=elapsed,
        )

    # Most entries record no binaries at all, which would leave `verify`
    # saying nothing about the majority of an installed set. Trying the tool id
    # recovers the common case (`curl` ships `curl`) -- but a miss reports
    # `unchecked`, never `failed`: the id is a guess, and plenty of tools are
    # named nothing like their command (metasploit-framework ships msfconsole).
    # Guessing is only acceptable while a wrong guess cannot accuse a working
    # install of being broken.
    found = shutil.which(tool.id)
    if found:
        return VerifyResult(
            tool_id=tool.id,
            status=STATUS_PRESENT,
            detail=f"{tool.id} on PATH ({found}); binary inferred from the tool id",
            elapsed=elapsed,
        )
    return VerifyResult(
        tool_id=tool.id,
        status=STATUS_UNCHECKED,
        detail="catalog records no verify command and no binary name",
        elapsed=elapsed,
    )


def verify_all(
    tools: list[Any], *, timeout: int = DEFAULT_TIMEOUT, jobs: int = DEFAULT_JOBS
) -> list[VerifyResult]:
    """Verify several tools concurrently, returning results in id order.

    Order is normalised rather than left to completion time so two runs on the
    same machine produce diffable output.
    """
    if not tools:
        return []
    workers = max(1, min(jobs, len(tools)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda t: verify_tool(t, timeout=timeout), tools))
    return sorted(results, key=lambda r: r.tool_id)


def _first_meaningful_line(text: str) -> str:
    """The line a human would quote -- usually the version banner.

    Tools print their version to stdout or stderr with no consistency, and some
    lead with a blank line or an ASCII logo, so take the first line with actual
    content rather than line zero.
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return "no output"

"""Find `verify:` commands by asking the binaries themselves.

Most catalog entries carry no `verify:`, so `loadout verify` can only report
that a file exists on ``PATH`` -- a weaker claim than "it runs", and the one
that matters least on a client site. Writing 800 of them by hand is not the
bottleneck; knowing which flag each tool answers to is, and no package index
records that. ``apt``'s Contents index gives binary *names*, not behaviour.

So this asks. For a tool whose binary is installed, it runs a short list of
conventional flags and keeps the first that exits 0. The result is a
suggestion written into the YAML source tree and reviewed like any other
catalog change -- never a value the runtime invents for itself.

Two things constrain the candidate list, and both are why it is short:

* **A flag that is not a version flag may start the tool.** ``-v`` is the
  clearest example: version for some tools, verbose for others, and
  ``tcpdump -v`` starts capturing. Anything whose common meaning is not
  "print and exit" is excluded rather than guarded.
* **Exiting 0 is not the same as being informative.** ``--help`` exits 0 for
  almost everything, so a version-shaped answer is preferred when one exists
  and help is only accepted as a fallback.

Probing runs local binaries, which is the one thing this module cannot avoid.
It is a maintainer command, off the install path, with no elevation, no shell,
a hard timeout, closed stdin and a scrubbed environment.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from ..errors import UnsafeArgument
from ..model import Tool
from ..policy import subprocess_env, validate_argv

logger = logging.getLogger("loadout.catalog.probe_verify")

#: Tried in order. Version flags first: they answer the question `loadout
#: verify` is actually asking, and their output identifies what is installed.
#: `-v` is deliberately absent -- it means "verbose" often enough that probing
#: with it would start the tool rather than describe it.
VERSION_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--version",),
    ("-V",),
    ("version",),
)

#: Accepted only when nothing above works. `--help` exits 0 for nearly
#: everything, so it proves the binary starts and parses arguments and little
#: more -- still better than the `PATH` lookup it replaces.
HELP_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--help",),
    ("-h",),
)

#: A probe that has not answered in this long is not a verify command: this
#: runs across a whole installed set, and `loadout verify` itself caps each
#: check well below the time an interactive tool would take to give up.
DEFAULT_TIMEOUT = 5.0

#: Probes wait on exec and I/O, not CPU.
DEFAULT_JOBS = 8

#: Enough to tell "nmap version 7.95" from a usage banner. Deliberately loose:
#: the point is to rank two candidates that both exit 0, not to parse versions.
_VERSION_RE = re.compile(r"\b\d+\.\d+(\.\d+)?\b")

#: Output that stops here is a tool that answered, not one that started doing
#: work and got killed. Anything longer is almost certainly a help screen,
#: which is ranked below a version answer anyway.
_MAX_INTERESTING_OUTPUT = 4000

#: A candidate flag, run against one binary. Injectable so the tests can
#: exercise the ranking without executing anything.
Runner = Callable[[list[str], float], tuple[int, str]]

STATUS_FOUND = "found"
STATUS_NO_ANSWER = "no-answer"
STATUS_NOT_INSTALLED = "not-installed"
STATUS_SKIPPED = "skipped"


@dataclass(frozen=True)
class ProbeReport:
    """What a whole run established, for the CLI and for CI to report."""

    #: Entries a probe could have said something about: a binary, no verify.
    candidates: int = 0
    #: Of those, the ones whose binary is actually on this machine. The gap
    #: between the two is the ceiling on what any single box can contribute.
    installed: int = 0
    found: int = 0
    versioned: int = 0
    no_answer: int = 0
    written: int = 0
    #: Entries left alone because their file carries comments a regenerated
    #: file would delete. They need the command adding by hand.
    annotated: int = 0
    probes: tuple[Probe, ...] = ()

    def to_dict(self) -> dict[str, int]:
        return {
            "candidates": self.candidates,
            "installed": self.installed,
            "found": self.found,
            "versioned": self.versioned,
            "no_answer": self.no_answer,
            "written": self.written,
            "annotated": self.annotated,
        }


@dataclass(frozen=True)
class Probe:
    """What probing one catalog entry established."""

    tool_id: str
    status: str
    binary: str = ""
    command: str = ""
    #: True when the accepted command printed something version-shaped, which
    #: is the difference between "this tool works" and "this tool starts".
    versioned: bool = False
    detail: str = ""

    @property
    def found(self) -> bool:
        return self.status == STATUS_FOUND


def candidates_from(tools: Iterable[Tool]) -> list[Tool]:
    """Entries a probe could say something new about.

    An entry that already declares `verify:` is left alone: a hand-written
    command was chosen by someone who knew the tool, and a probe that exits 0
    is not evidence enough to overrule that.
    """
    return [t for t in tools if t.binaries and not t.verify]


def probe_tool(
    tool: Tool,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    runner: Runner | None = None,
) -> Probe:
    binary = next((b for b in tool.binaries if shutil.which(b)), "")
    if not binary:
        return Probe(
            tool.id,
            STATUS_NOT_INSTALLED,
            detail="no binary from this entry is on PATH",
        )

    run = runner or _run
    best: tuple[str, bool] | None = None
    for flags in VERSION_FLAGS:
        answered = _try(run, binary, flags, timeout)
        if answered is None:
            continue
        best = (answered[0], answered[1])
        if answered[1]:
            # Version-shaped output is the strongest answer available; stop
            # rather than keep executing a binary that already answered.
            break
    if best is None:
        for flags in HELP_FLAGS:
            answered = _try(run, binary, flags, timeout)
            if answered is not None:
                best = (answered[0], False)
                break

    if best is None:
        return Probe(
            tool.id,
            STATUS_NO_ANSWER,
            binary=binary,
            detail="no candidate flag exited 0",
        )
    return Probe(tool.id, STATUS_FOUND, binary=binary, command=best[0], versioned=best[1])


def probe_tools(
    tools: Sequence[Tool],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    jobs: int = DEFAULT_JOBS,
    runner: Runner | None = None,
) -> list[Probe]:
    if not tools:
        return []
    workers = max(1, min(jobs, len(tools)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                lambda t: probe_tool(t, timeout=timeout, runner=runner),
                tools,
            )
        )
    return results


def _try(
    run: Runner, binary: str, flags: tuple[str, ...], timeout: float
) -> tuple[str, bool] | None:
    """Run one candidate. ``(command, looks_versioned)``, or ``None``."""
    argv = [binary, *flags]
    try:
        validate_argv(argv)
    except UnsafeArgument:
        return None
    try:
        code, output = run(argv, timeout)
    except OSError as exc:
        logger.debug("%s: %s", " ".join(argv), exc)
        return None
    if code != 0:
        return None
    trimmed = output[:_MAX_INTERESTING_OUTPUT]
    return " ".join(argv), bool(_VERSION_RE.search(trimmed))


def _run(argv: list[str], timeout: float) -> tuple[int, str]:
    """Run a candidate flag with nothing inherited from this process.

    stdin is closed so a tool that decides to prompt is killed by the timeout
    instead of hanging on a terminal that is not there.
    """
    try:
        proc = subprocess.run(  # noqa: S603 - argv validated by the caller, no shell
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=subprocess_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def probe_source_tree(
    root: Path,
    *,
    write: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    jobs: int = DEFAULT_JOBS,
    versioned_only: bool = True,
    runner: Runner | None = None,
) -> ProbeReport:
    """Probe every candidate in the YAML tree, optionally writing results back.

    Writes into ``catalog/`` rather than the compiled database for the same
    reason enrichment does: the YAML is the source of truth and the diff is
    what gets reviewed. ``versioned_only`` keeps the default conservative --
    a ``--help`` that exits 0 says the binary starts, which is a weaker claim
    than the entry would then be making.
    """
    import yaml

    from .compile import dump_tool, iter_entry_files, load_source_tree

    root = Path(root)
    report = load_source_tree(root, strict=False)
    candidates = candidates_from(report.tools)
    probes = probe_tools(candidates, timeout=timeout, jobs=jobs, runner=runner)
    accepted = [p for p in probes if p.found and (p.versioned or not versioned_only)]

    written = 0
    annotated = 0
    if write and accepted:
        paths: dict[str, Path] = {}
        for path in iter_entry_files(root):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.debug("cannot map %s to an id: %s", path, exc)
                continue
            if isinstance(data, dict) and data.get("id"):
                paths[str(data["id"]).strip().lower()] = path

        by_id = {t.id: t for t in report.tools}
        for probe in accepted:
            tool = by_id.get(probe.tool_id)
            destination = paths.get(probe.tool_id)
            if tool is None or destination is None:
                continue
            if dump_tool(replace(tool, verify=probe.command), destination) is None:
                annotated += 1
                continue
            written += 1

    return ProbeReport(
        candidates=len(candidates),
        installed=sum(1 for p in probes if p.status != STATUS_NOT_INSTALLED),
        found=sum(1 for p in probes if p.found),
        versioned=sum(1 for p in probes if p.found and p.versioned),
        no_answer=sum(1 for p in probes if p.status == STATUS_NO_ANSWER),
        written=written,
        annotated=annotated,
        probes=tuple(accepted),
    )

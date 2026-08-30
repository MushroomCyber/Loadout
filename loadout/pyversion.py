"""Python interpreter constraints, checked before an install rather than after.

``pipx install modelscan`` on a current Kali fails with forty lines of pip
output ending in *"Ignored the following versions that require a different
python version"*. The information needed to predict that -- modelscan declares
``>=3.10,<3.13``, the machine has 3.13 -- is available before anything is
downloaded, so the failure should be a sentence at planning time instead of a
wall of text after the network work is done.

Only the subset of PEP 440 that ``requires_python`` actually uses is
implemented: comma-separated ``>=``, ``>``, ``<=``, ``<``, ``==``, ``!=`` and
``~=`` over dotted release numbers. Wildcards, epochs, local versions and
pre-release ordering do not appear in a ``requires_python`` field, and pretending
to support them would be a worse lie than not accepting them.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

#: `>=3.10`, `<3.13`, `!=3.9.7` -- operator then a dotted release.
_CLAUSE_RE = re.compile(r"^\s*(>=|<=|==|!=|~=|>|<)\s*([0-9]+(?:\.[0-9]+)*)\s*$")

#: How far to look for an alternative interpreter. 3.9 is the oldest release
#: any current security tool supports; beyond 3.20 is speculation.
_SEARCH_MINORS = range(9, 21)


class InvalidSpecifier(ValueError):
    """The specifier is not something this module is willing to guess at."""


def parse_release(text: str) -> tuple[int, ...]:
    """``"3.13.2"`` -> ``(3, 13, 2)``."""
    return tuple(int(part) for part in str(text).strip().split("."))


def _pad(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Compare at the declared precision.

    ``3.13.2`` is inside ``<3.14`` and outside ``<3.13``; padding the shorter
    side with zeros makes both comparisons fall out of plain tuple ordering.
    """
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)), right + (0,) * (width - len(right))


def _clause_holds(version: tuple[int, ...], operator: str, bound: tuple[int, ...]) -> bool:
    if operator == "~=":
        # `~=3.10.1` means >=3.10.1 and <3.11 -- compatible release.
        if len(bound) < 2:
            raise InvalidSpecifier("~= needs at least two release segments")
        lower_ok = _clause_holds(version, ">=", bound)
        ceiling = (*bound[:-2], bound[-2] + 1)
        return lower_ok and _clause_holds(version, "<", ceiling)

    left, right = _pad(version, bound)
    if operator == ">=":
        return left >= right
    if operator == ">":
        return left > right
    if operator == "<=":
        return left <= right
    if operator == "<":
        return left < right
    if operator == "==":
        # Compare only as precisely as the bound was written: `==3.11` accepts
        # every 3.11.x, which is what a requires_python of that shape means.
        return version[: len(bound)] == bound
    if operator == "!=":
        return version[: len(bound)] != bound
    raise InvalidSpecifier(f"unsupported operator {operator!r}")  # pragma: no cover


def validate_specifier(specifier: str) -> None:
    """Raise :class:`InvalidSpecifier` unless every clause parses.

    Called from catalog validation so a typo fails in CI rather than silently
    matching nothing on a user's machine.
    """
    text = str(specifier or "").strip()
    if not text:
        raise InvalidSpecifier("empty specifier")
    for clause in text.split(","):
        if not _CLAUSE_RE.match(clause):
            raise InvalidSpecifier(f"cannot parse {clause.strip()!r} in {text!r}")


def satisfies(version: tuple[int, ...] | str, specifier: str) -> bool:
    """Does *version* meet every clause of *specifier*?"""
    release = parse_release(version) if isinstance(version, str) else tuple(version)
    for clause in str(specifier).split(","):
        match = _CLAUSE_RE.match(clause)
        if match is None:
            raise InvalidSpecifier(f"cannot parse {clause.strip()!r} in {specifier!r}")
        if not _clause_holds(release, match.group(1), parse_release(match.group(2))):
            return False
    return True


def describe(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def running_version() -> tuple[int, ...]:
    return sys.version_info[:3]


def interpreter_version(executable: str) -> tuple[int, ...] | None:
    """The release *executable* reports, or None if it will not say.

    Asks the interpreter rather than trusting its filename: ``python3`` is a
    symlink whose target changes with the distribution, and a venv's
    ``python3.11`` can be a shim for something else entirely.
    """
    try:
        result = subprocess.run(  # noqa: S603 - resolved path, fixed argument
            [executable, "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return parse_release(result.stdout.strip())
    except ValueError:
        return None


def find_interpreter(specifier: str, *, default: str = "python3") -> str | None:
    """Path to an interpreter on PATH satisfying *specifier*, preferring the default.

    Returns None when the machine has nothing that fits -- which is the answer
    for modelscan on a stock Kali, where 3.13 is the only interpreter present.
    """
    resolved_default = shutil.which(default)
    if resolved_default:
        found = interpreter_version(resolved_default)
        if found is not None and satisfies(found, specifier):
            return resolved_default

    # Newest first: a tool that supports 3.11 and 3.12 should get 3.12, so an
    # install done today does not need redoing when 3.11 goes end-of-life.
    for minor in sorted(_SEARCH_MINORS, reverse=True):
        candidate = shutil.which(f"python3.{minor}")
        if not candidate:
            continue
        found = interpreter_version(candidate)
        if found is not None and satisfies(found, specifier):
            return candidate
    return None


def available_interpreters() -> list[tuple[str, tuple[int, ...]]]:
    """Every distinct python on PATH, for reporting what the machine does have."""
    seen: dict[str, tuple[int, ...]] = {}
    names = ["python3", *(f"python3.{minor}" for minor in _SEARCH_MINORS)]
    for name in names:
        path = shutil.which(name)
        if not path:
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        version = interpreter_version(path)
        if version is not None:
            seen[real] = version
    return sorted(((path, version) for path, version in seen.items()), key=lambda row: row[1])


def explain_gap(specifier: str, *, default: str = "python3") -> str:
    """One sentence naming the mismatch, for a message a user can act on."""
    resolved = shutil.which(default)
    current = interpreter_version(resolved) if resolved else None
    have = f"this machine has {describe(current)}" if current else "no python3 was found"
    others = [describe(v) for _p, v in available_interpreters()]
    extra = ""
    if len(others) > 1:
        extra = f" (also present: {', '.join(others)})"
    # Name the interpreter that would fix it. Without this the user is left to
    # work out which release satisfies the specifier, which is the tedious part.
    suggestion = suggested_package(specifier)
    fix = f" -- install {suggestion} to use it" if suggestion else ""
    return f"needs Python {specifier}; {have}{extra}{fix}"


def suggested_package(specifier: str) -> str:
    """The interpreter a user would install to satisfy *specifier*, as a name.

    Best effort and deliberately conservative: it only names a version when the
    specifier has an upper bound to work back from, because "install python3.x"
    is bad advice when any modern release would already do.
    """
    ceilings = []
    for clause in str(specifier).split(","):
        match = _CLAUSE_RE.match(clause)
        # `<` only. `<=3.11` admits 3.11.0 and nothing above it, so naming
        # python3.11 would send someone to install a 3.11.9 that still fails --
        # worse than saying nothing.
        if match and match.group(1) == "<":
            ceilings.append(parse_release(match.group(2)))
    if not ceilings:
        return ""
    bound = min(ceilings)
    if len(bound) < 2 or bound[1] < 1:
        return ""
    return f"python3.{bound[1] - 1}"


def python_from_path(executable: str) -> str:
    """Normalise for display without losing which interpreter it was."""
    return Path(executable).name

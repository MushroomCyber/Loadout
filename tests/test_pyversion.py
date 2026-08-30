"""Interpreter constraints, checked before an install rather than after.

The case that produced this module: `pipx install modelscan` on a current Kali
prints forty lines of pip output ending in "Ignored the following versions that
require a different python version". Everything needed to predict that is known
before the network is touched.
"""

from __future__ import annotations

import pytest

from loadout.pyversion import (
    InvalidSpecifier,
    describe,
    explain_gap,
    find_interpreter,
    parse_release,
    satisfies,
    suggested_package,
    validate_specifier,
)

# ---------------------------------------------------------------------------
# The specifier subset
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "specifier", "expected"),
    [
        # The real modelscan case, both sides of its boundary.
        ("3.13.12", ">=3.10,<3.13", False),
        ("3.12.8", ">=3.10,<3.13", True),
        ("3.10.0", ">=3.10,<3.13", True),
        ("3.9.18", ">=3.10,<3.13", False),
        # Boundaries are exclusive/inclusive as written, at the precision written.
        ("3.13.0", "<3.13", False),
        ("3.12.99", "<3.13", True),
        ("3.13.0", "<=3.13", True),
        # PEP 440 orders 3.13.1 above 3.13, so `<=3.13` excludes it. This
        # is why `<=` is a poor way to pin a series and `<3.14` is the
        # form real packages use.
        ("3.13.1", "<=3.13", False),
        # Other requires_python strings live in this catalog.
        ("3.13.12", "<4.0,>=3.12", True),
        ("3.13.12", "<3.14,>=3.10", True),
        ("3.13.12", "<3.15,>=3.10", True),
        ("3.13.12", ">=3.11", True),
        ("3.11.9", ">=3.12", False),
        # == and != compare only as precisely as the bound is written.
        ("3.11.9", "==3.11", True),
        ("3.12.0", "==3.11", False),
        ("3.9.7", "!=3.9.7", False),
        ("3.9.8", "!=3.9.7", True),
        # ~= is a compatible release: >=3.10.1, <3.11.
        ("3.10.4", "~=3.10.1", True),
        ("3.11.0", "~=3.10.1", False),
        ("3.10.0", "~=3.10.1", False),
    ],
)
def test_specifier_matching(version, specifier, expected):
    assert satisfies(version, specifier) is expected


def test_a_shorter_bound_compares_at_its_own_precision():
    """3.13.2 is inside `<3.14` and outside `<3.13`. Comparing raw tuples of
    different lengths gets one of those two wrong."""
    assert satisfies("3.13.2", "<3.14")
    assert not satisfies("3.13.2", "<3.13")
    assert satisfies("3.13", ">=3.13")


@pytest.mark.parametrize(
    "specifier",
    ["", "   ", ">= 3.10 or 3.11", "3.10", ">=3.10;<3.13", "==3.11.*", ">=abc"],
)
def test_unparseable_specifiers_are_rejected_not_guessed(specifier):
    """A specifier this module cannot read must fail in CI. Silently matching
    nothing would turn a typo into "no route available" on a user's machine."""
    with pytest.raises(InvalidSpecifier):
        validate_specifier(specifier)


def test_the_specifiers_this_catalog_actually_uses_all_validate():
    for specifier in (">=3.10,<3.13", "<4.0,>=3.12", "<3.14,>=3.10", ">=3.11", "~=3.10.1"):
        validate_specifier(specifier)


def test_parse_and_describe_round_trip():
    assert parse_release("3.13.12") == (3, 13, 12)
    assert describe((3, 13, 12)) == "3.13.12"


# ---------------------------------------------------------------------------
# Choosing an interpreter
# ---------------------------------------------------------------------------


def test_the_default_interpreter_wins_when_it_fits(monkeypatch):
    """Naming an interpreter that is already the default adds noise to the argv
    and a failure mode when the path moves."""
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr("loadout.pyversion.interpreter_version", lambda _p: (3, 12, 1))
    assert find_interpreter(">=3.10,<3.13") == "/usr/bin/python3"


def test_an_alternative_is_found_when_the_default_is_too_new(monkeypatch):
    """The whole point: a 3.13 box with 3.12 alongside can still install a
    package pinned below 3.13."""
    versions = {"/usr/bin/python3": (3, 13, 12), "/usr/bin/python3.12": (3, 12, 8)}
    monkeypatch.setattr(
        "shutil.which", lambda n: f"/usr/bin/{n}" if f"/usr/bin/{n}" in versions else None
    )
    monkeypatch.setattr("loadout.pyversion.interpreter_version", versions.get)
    assert find_interpreter(">=3.10,<3.13") == "/usr/bin/python3.12"


def test_the_newest_satisfying_interpreter_is_preferred(monkeypatch):
    """Picking 3.10 when 3.12 also fits means redoing the install when 3.10
    goes end-of-life."""
    versions = {
        "/usr/bin/python3": (3, 13, 12),
        "/usr/bin/python3.10": (3, 10, 14),
        "/usr/bin/python3.12": (3, 12, 8),
    }
    monkeypatch.setattr(
        "shutil.which", lambda n: f"/usr/bin/{n}" if f"/usr/bin/{n}" in versions else None
    )
    monkeypatch.setattr("loadout.pyversion.interpreter_version", versions.get)
    assert find_interpreter(">=3.10,<3.13") == "/usr/bin/python3.12"


def test_nothing_is_returned_when_the_machine_cannot_satisfy_it(monkeypatch):
    """Stock Kali: 3.13 and nothing else. modelscan cannot be installed here at
    all, and saying so is the correct answer."""
    monkeypatch.setattr(
        "shutil.which", lambda n: "/usr/bin/python3" if n == "python3" else None
    )
    monkeypatch.setattr("loadout.pyversion.interpreter_version", lambda _p: (3, 13, 12))
    assert find_interpreter(">=3.10,<3.13") is None


def test_a_lying_or_broken_interpreter_is_skipped(monkeypatch):
    """interpreter_version returns None when the binary will not run. Treating
    that as a match would produce a venv built by something unknown."""
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr("loadout.pyversion.interpreter_version", lambda _p: None)
    assert find_interpreter(">=3.10,<3.13") is None


def test_the_version_is_asked_of_the_interpreter_not_read_off_its_name():
    """`python3.11` in a venv can be a shim for something else, and `python3`
    is a symlink whose target changes with the distribution."""
    import inspect

    from loadout import pyversion

    source = inspect.getsource(pyversion.interpreter_version)
    assert "sys.version_info" in source
    assert "subprocess.run" in source


# ---------------------------------------------------------------------------
# Explaining the gap
# ---------------------------------------------------------------------------


def test_the_explanation_names_both_sides(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/python3" if n == "python3" else None)
    monkeypatch.setattr("loadout.pyversion.interpreter_version", lambda _p: (3, 13, 12))
    message = explain_gap(">=3.10,<3.13")
    assert ">=3.10,<3.13" in message
    assert "3.13.12" in message
    # And the fix, or the user is left to work out which release satisfies it.
    assert "python3.12" in message


def test_the_suggested_interpreter_is_the_newest_one_that_would_work():
    assert suggested_package(">=3.10,<3.13") == "python3.12"
    assert suggested_package(">=3.9,<3.11") == "python3.10"


def test_no_interpreter_is_suggested_when_there_is_no_exclusive_upper_bound():
    """"Install python3.x" is bad advice for `>=3.10`: any modern release
    already satisfies it, so the real problem is something else.

    `<=3.11` gets no suggestion either -- it admits only 3.11.0, so naming
    python3.11 would send someone to install a 3.11.9 that still fails.
    """
    assert suggested_package(">=3.10") == ""
    assert suggested_package(">=3.12,!=3.13.0") == ""
    assert suggested_package("<=3.11") == ""

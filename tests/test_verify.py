"""`loadout verify` -- smoke-testing installed tools.

These drive real subprocesses rather than mocking `subprocess.run`, because
what is being tested is precisely how this code behaves against a real command:
that a shell metacharacter stays literal, that a hung tool is cut off, and that
a version banner is pulled out of whichever stream the tool chose.
"""

from __future__ import annotations

import shlex
import sys

import pytest

from loadout.model import Tool
from loadout.verify import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PRESENT,
    STATUS_UNCHECKED,
    verify_all,
    verify_tool,
)

#: A verify command that is guaranteed to exist wherever the tests run.
PY = sys.executable


def python_command(code: str, *args: str) -> str:
    """A catalog-style verify string that runs this interpreter.

    `shlex.join` round-trips through the `shlex.split` the module uses, on
    Windows paths included -- asserted below so a platform where it does not
    fails loudly instead of silently skewing every test in this file.
    """
    return shlex.join([PY, "-c", code, *args])


def test_the_command_helper_round_trips():
    joined = python_command("print(1)")
    assert shlex.split(joined) == [PY, "-c", "print(1)"]


def tool(**kwargs) -> Tool:
    kwargs.setdefault("id", "sample")
    return Tool(**kwargs)


# ---------------------------------------------------------------------------
# Running the catalog's verify command
# ---------------------------------------------------------------------------


def test_a_passing_command_reports_the_version_line():
    result = verify_tool(tool(verify=python_command("print('nmap 7.99')")))
    assert result.status == STATUS_OK
    assert result.detail == "nmap 7.99"


def test_a_version_printed_to_stderr_is_still_found():
    """Tools split version output across stdout and stderr with no
    consistency, so both are searched."""
    result = verify_tool(
        tool(verify=python_command("import sys; sys.stderr.write('ffuf v2.1.0\\n')"))
    )
    assert result.status == STATUS_OK
    assert result.detail == "ffuf v2.1.0"


def test_a_leading_blank_line_or_banner_is_skipped():
    result = verify_tool(tool(verify=python_command("print(); print(); print('real')")))
    assert result.detail == "real"


def test_a_nonzero_exit_is_a_failure_and_says_the_code():
    result = verify_tool(
        tool(verify=python_command("import sys; print('broken'); sys.exit(3)"))
    )
    assert result.status == STATUS_FAILED
    assert "exited 3" in result.detail
    assert "broken" in result.detail


def test_a_hanging_tool_is_cut_off_rather_than_hanging_the_run():
    result = verify_tool(
        tool(verify=python_command("import time; time.sleep(30)")), timeout=1
    )
    assert result.status == STATUS_FAILED
    assert "timed out" in result.detail


def test_a_missing_binary_fails_without_raising():
    result = verify_tool(tool(verify="definitely-not-a-real-binary-xyz --version"))
    assert result.status == STATUS_FAILED
    assert "not found on PATH" in result.detail


def test_an_unparseable_command_blames_the_catalog_not_the_tool():
    """An unbalanced quote is a catalog bug. Reporting it as a broken install
    would send someone reinstalling a tool that is fine."""
    result = verify_tool(tool(verify='nmap --version "unclosed'))
    assert result.status == STATUS_FAILED
    assert "catalog" in result.detail.lower()


def test_shell_metacharacters_are_not_interpreted():
    """The command comes from the catalog. If it reached a shell, a catalog
    entry would be arbitrary code execution on every machine that ran it.

    The payload is deliberately inert -- a substitution that would only echo,
    not a destructive one. Someone testing this property by flipping the code
    to `shell=True` should not be able to damage their machine doing it.
    """
    payload = "$(echo pwned); echo also-pwned"
    result = verify_tool(
        tool(verify=python_command("import sys; print(sys.argv[1])", payload))
    )
    assert result.status == STATUS_OK
    assert result.detail == payload, "the shell expanded a catalog-supplied string"


# ---------------------------------------------------------------------------
# Falling back when the catalog has no verify command
# ---------------------------------------------------------------------------


def test_a_known_binary_on_path_is_present_not_ok():
    """Finding a file is a weaker claim than running it, and the report must
    not blur the two."""
    from pathlib import Path

    result = verify_tool(tool(binaries=(Path(PY).name,)))
    assert result.status == STATUS_PRESENT
    assert "no verify command" in result.detail


def test_a_known_binary_that_is_missing_is_a_failure():
    """The catalog named this binary, so its absence is real."""
    result = verify_tool(tool(binaries=("definitely-not-a-real-binary-xyz",)))
    assert result.status == STATUS_FAILED
    assert "not found on PATH" in result.detail


def test_an_unknown_binary_is_inferred_from_the_tool_id():
    """Most entries record no binaries, which would leave verify silent about
    the majority of an installed set."""
    from pathlib import Path

    result = verify_tool(tool(id=Path(PY).stem))
    assert result.status == STATUS_PRESENT
    assert "inferred" in result.detail


def test_a_wrong_inference_is_unchecked_never_failed():
    """metasploit-framework ships msfconsole. Guessing the binary from the id
    is only acceptable while a wrong guess cannot accuse a working install of
    being broken."""
    result = verify_tool(tool(id="definitely-not-a-real-binary-xyz"))
    assert result.status == STATUS_UNCHECKED
    assert result.status != STATUS_FAILED


# ---------------------------------------------------------------------------
# Content has no command, and never will
# ---------------------------------------------------------------------------


def content(**kwargs) -> Tool:
    kwargs.setdefault("id", "seclists")
    kwargs.setdefault("kind", "content")
    return Tool(**kwargs)


def test_content_is_checked_by_its_paths_not_by_path_lookup(tmp_path):
    """A wordlist ships no binary, so the PATH fallback called every content
    entry `failed` -- accusing a good 1.8 GB install of being broken."""
    (tmp_path / "seclists").mkdir()
    result = verify_tool(content(paths=(str(tmp_path / "seclists"),)))
    assert result.status == STATUS_PRESENT
    assert "exists" in result.detail


def test_content_whose_directory_is_absent_is_a_real_failure(tmp_path):
    result = verify_tool(content(paths=(str(tmp_path / "not-there"),)))
    assert result.status == STATUS_FAILED
    assert "missing" in result.detail


def test_content_with_no_paths_is_unchecked_not_failed():
    """The catalog knows nothing about it, which is not the same as it being
    broken -- the same rule the binary fallback already follows."""
    result = verify_tool(content())
    assert result.status == STATUS_UNCHECKED


def test_content_never_falls_back_to_guessing_a_binary_from_the_id():
    """`wordlists` on Kali does ship /usr/bin/wordlists, so the id guess would
    sometimes pass -- reporting a directory of files as verified because an
    unrelated command shares its name."""
    from pathlib import Path as _Path

    result = verify_tool(content(id=_Path(PY).stem))
    assert result.status == STATUS_UNCHECKED
    assert "inferred" not in result.detail


def test_a_content_entry_may_still_carry_a_verify_command(tmp_path):
    """`paths:` is the fallback, not a replacement: an entry that can prove
    itself properly still gets to."""
    result = verify_tool(
        content(verify=python_command("print('seclists 2025.3')"), paths=("/nope",))
    )
    assert result.status == STATUS_OK


# ---------------------------------------------------------------------------
# Running a set
# ---------------------------------------------------------------------------


def test_results_come_back_in_id_order_not_completion_order():
    """Two runs on the same machine should produce diffable output."""
    tools = [
        tool(id="slow", verify=python_command("import time; time.sleep(0.3)")),
        tool(id="fast", verify=python_command("pass")),
        tool(id="middle", verify=python_command("import time; time.sleep(0.1)")),
    ]
    results = verify_all(tools, jobs=4)
    assert [r.tool_id for r in results] == ["fast", "middle", "slow"]


def test_an_empty_set_is_not_an_error():
    assert verify_all([]) == []


def test_ok_covers_verified_and_present_but_not_failed():
    from loadout.verify import VerifyResult

    assert VerifyResult("a", STATUS_OK).ok
    assert VerifyResult("a", STATUS_PRESENT).ok
    assert not VerifyResult("a", STATUS_FAILED).ok
    assert not VerifyResult("a", STATUS_UNCHECKED).ok


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("json_mode", [False, True])
def test_a_failure_exits_nonzero_so_scripts_can_gate_on_it(catalog, monkeypatch, json_mode):
    """This is meant to be the last line of a pre-engagement check."""
    import argparse
    import io
    from contextlib import redirect_stdout

    from loadout.ui.cli import Context, cmd_verify

    args = argparse.Namespace(
        as_json=json_mode,
        catalog=None,
        prefer=[],
        tools=["nmap"],
        timeout=5,
        jobs=2,
        quiet=False,
    )
    ctx = Context(args=args)
    ctx._catalog = catalog
    monkeypatch.setattr("loadout.verify.shutil.which", lambda _name: None)

    with redirect_stdout(io.StringIO()):
        code = cmd_verify(ctx)
    assert code == 1


def test_an_unknown_tool_id_is_rejected_with_suggestions(catalog):
    import argparse

    from loadout.errors import ToolNotFound
    from loadout.ui.cli import Context, cmd_verify

    args = argparse.Namespace(
        as_json=False, catalog=None, prefer=[], tools=["nmpa"],
        timeout=5, jobs=2, quiet=False,
    )
    ctx = Context(args=args)
    ctx._catalog = catalog
    with pytest.raises(ToolNotFound):
        cmd_verify(ctx)

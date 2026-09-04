"""Shell completion generated from the parser that actually runs.

The point of generating rather than hand-writing these is that a hand-written
script goes stale the first time someone adds a flag and nothing fails when it
does. So the tests assert the generator tracks the live parser, not that it
emits some fixed text.
"""

from __future__ import annotations

import argparse

import pytest

from loadout.completions import SHELLS, render, walk
from loadout.ui.cli import build_parser


@pytest.fixture
def parser():
    return build_parser()


class TestWalkingTheParser:
    def test_every_subcommand_is_found(self, parser):
        names = {c.name for c in walk(parser).children}
        assert {"install", "remove", "search", "bundle", "lock", "verify"} <= names

    def test_nested_subcommands_are_found(self, parser):
        bundle = next(c for c in walk(parser).children if c.name == "bundle")
        assert {g.name for g in bundle.children} == {
            "create", "inspect", "verify", "install"
        }

    def test_a_commands_own_flags_are_collected(self, parser):
        install = next(c for c in walk(parser).children if c.name == "install")
        assert "--dry-run" in install.options
        assert "--allow-unverified" in install.options

    def test_the_subcommand_action_is_not_mistaken_for_a_flag(self, parser):
        root = walk(parser)
        assert all(o.startswith("-") for o in root.options)


class TestRendering:
    @pytest.mark.parametrize("shell", SHELLS)
    def test_every_shell_emits_something_mentioning_the_program(self, shell, parser):
        script = render(shell, parser)
        assert "loadout" in script
        assert script.endswith("\n")

    @pytest.mark.parametrize("shell", SHELLS)
    def test_every_subcommand_reaches_every_script(self, shell, parser):
        script = render(shell, parser)
        for command in walk(parser).children:
            assert command.name in script, (shell, command.name)

    def test_an_unknown_shell_is_refused_by_name(self, parser):
        with pytest.raises(ValueError, match="unknown shell"):
            render("powershell", parser)

    def test_the_shell_name_is_case_insensitive(self, parser):
        assert render("BASH", parser) == render("bash", parser)

    def test_bash_registers_a_completion_function(self, parser):
        assert "complete -F _loadout loadout" in render("bash", parser)

    def test_zsh_declares_itself_a_compdef(self, parser):
        assert render("zsh", parser).startswith("#compdef loadout")

    def test_fish_marks_the_command_as_taking_no_files(self, parser):
        assert "complete -c loadout -f" in render("fish", parser)

    def test_nested_subcommands_are_offered_after_their_parent(self, parser):
        """`loadout bundle <TAB>` has to offer create/inspect/verify/install,
        which needs the second level in the script, not just the first."""
        for shell in SHELLS:
            script = render(shell, parser)
            assert "inspect" in script, shell

    def test_a_help_string_with_a_quote_cannot_break_the_script(self):
        """Help text is written by whoever adds a subcommand, and zsh and fish
        embed it inside single quotes. An apostrophe reaching that verbatim
        would end the string and leave the rest as stray shell code."""
        p = argparse.ArgumentParser(prog="loadout")
        sub = p.add_subparsers()
        sub.add_parser("odd", help="it's got a quote: and a colon")

        for shell in ("zsh", "fish"):
            script = render(shell, p)
            assert "odd" in script, shell
            assert "'s got" not in script, shell

        # bash embeds no help text at all, so there is nothing to escape.
        assert "odd" in render("bash", p)


class TestTheCommand:
    @pytest.mark.parametrize("shell", SHELLS)
    def test_it_prints_the_script_to_stdout(self, shell, capsys):
        from loadout.ui.cli import main

        assert main(["completions", shell]) == 0
        assert "loadout" in capsys.readouterr().out

    def test_an_unknown_shell_is_rejected_by_the_parser(self, capsys):
        from loadout.ui.cli import main

        with pytest.raises(SystemExit):
            main(["completions", "powershell"])

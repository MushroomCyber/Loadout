"""Probing for `verify:` commands.

The runner is injected throughout. Executing real binaries is what the module
does in production and exactly what a test must not do: the candidate list
exists because some flags start tools rather than describe them, and a test
suite that discovered that the hard way would be finding it on the machine of
whoever ran it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from loadout.catalog import probe_verify
from loadout.catalog.probe_verify import (
    STATUS_FOUND,
    STATUS_NO_ANSWER,
    STATUS_NOT_INSTALLED,
    Probe,
    candidates_from,
    probe_source_tree,
    probe_tool,
    probe_tools,
)
from loadout.model import InstallMethod, Tool


def _tool(tool_id: str, *, binaries=("thing",), verify: str = "") -> Tool:
    return Tool(
        id=tool_id,
        summary=f"{tool_id} summary",
        binaries=tuple(binaries),
        verify=verify,
        install=(InstallMethod(provider="apt", spec={"package": tool_id}),),
    )


def _answers(mapping: dict[str, tuple[int, str]]):
    """A runner that replies only to the argv strings it was given."""

    def run(argv: list[str], _timeout: float) -> tuple[int, str]:
        return mapping.get(" ".join(argv), (1, ""))

    return run


def _installed(monkeypatch, *names: str) -> None:
    monkeypatch.setattr(
        probe_verify.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in names else None,
    )


class TestCandidates:
    def test_an_entry_that_already_states_a_verify_command_is_left_alone(self):
        """A hand-written command was chosen by someone who knew the tool; a
        flag that exits 0 is not evidence enough to overrule it."""
        tools = [
            _tool("nmap", verify="nmap --version"),
            _tool("ffuf"),
        ]
        assert [t.id for t in candidates_from(tools)] == ["ffuf"]

    def test_an_entry_with_no_binary_has_nothing_to_probe(self):
        assert candidates_from([_tool("mythic", binaries=())]) == []


class TestRanking:
    def test_a_version_answer_wins(self, monkeypatch):
        _installed(monkeypatch, "thing")
        run = _answers({"thing --version": (0, "thing 4.10.1")})
        probe = probe_tool(_tool("thing"), runner=run)
        assert probe.status == STATUS_FOUND
        assert probe.command == "thing --version"
        assert probe.versioned is True

    def test_help_is_a_fallback_and_is_marked_as_the_weaker_answer(self, monkeypatch):
        """`--help` exits 0 for nearly everything, so an entry recorded from
        it claims the binary starts, not that it works."""
        _installed(monkeypatch, "thing")
        run = _answers({"thing --help": (0, "usage: thing [options]")})
        probe = probe_tool(_tool("thing"), runner=run)
        assert probe.command == "thing --help"
        assert probe.versioned is False

    def test_a_version_flag_that_exits_zero_without_a_version_loses_to_one_that_does(
        self, monkeypatch
    ):
        """Some tools accept `--version` and print their usage banner. `-V`
        naming an actual version is the better record of the two."""
        _installed(monkeypatch, "thing")
        run = _answers(
            {
                "thing --version": (0, "usage: thing [options]"),
                "thing -V": (0, "thing version 2.4"),
            }
        )
        probe = probe_tool(_tool("thing"), runner=run)
        assert probe.command == "thing -V"
        assert probe.versioned is True

    def test_probing_stops_once_a_version_answer_is_found(self, monkeypatch):
        """Every extra candidate is another execution of someone's binary."""
        _installed(monkeypatch, "thing")
        seen: list[list[str]] = []

        def run(argv, _timeout):
            seen.append(argv)
            return (0, "thing 1.2.3")

        probe_tool(_tool("thing"), runner=run)
        assert seen == [["thing", "--version"]]

    def test_v_lowercase_is_never_tried(self, monkeypatch):
        """`-v` is verbose for enough tools that probing with it would start
        them -- `tcpdump -v` begins capturing."""
        _installed(monkeypatch, "thing")
        seen: list[list[str]] = []

        def run(argv, _timeout):
            seen.append(argv)
            return (1, "")

        probe_tool(_tool("thing"), runner=run)
        assert ["thing", "-v"] not in seen

    def test_nothing_answering_is_reported_rather_than_guessed(self, monkeypatch):
        _installed(monkeypatch, "thing")
        probe = probe_tool(_tool("thing"), runner=_answers({}))
        assert probe.status == STATUS_NO_ANSWER
        assert probe.command == ""

    def test_a_tool_that_is_not_installed_is_not_a_failure(self, monkeypatch):
        """The catalog is 842 entries and no machine has them all; "absent"
        must not read as "this entry is broken"."""
        _installed(monkeypatch)
        probe = probe_tool(_tool("thing"), runner=_answers({}))
        assert probe.status == STATUS_NOT_INSTALLED
        assert probe.found is False

    def test_the_first_installed_binary_is_the_one_probed(self, monkeypatch):
        _installed(monkeypatch, "second")
        run = _answers({"second --version": (0, "second 1.0")})
        probe = probe_tool(
            _tool("thing", binaries=("first", "second")), runner=run
        )
        assert probe.binary == "second"

    def test_a_runner_that_cannot_exec_is_not_an_answer(self, monkeypatch):
        """A binary on PATH can still fail to start -- a wrong-architecture
        release archive is the case this project already installs around."""
        _installed(monkeypatch, "thing")

        def run(argv, _timeout):
            raise OSError("Exec format error")

        assert probe_tool(_tool("thing"), runner=run).status == STATUS_NO_ANSWER


class TestBatch:
    def test_an_empty_batch_starts_no_threads(self):
        assert probe_tools([]) == []

    def test_every_tool_gets_a_result(self, monkeypatch):
        _installed(monkeypatch, "thing")
        run = _answers({"thing --version": (0, "thing 1.0")})
        probes = probe_tools([_tool(f"t{i}") for i in range(5)], runner=run)
        assert len(probes) == 5
        assert {p.tool_id for p in probes} == {f"t{i}" for i in range(5)}


class TestSourceTree:
    def _tree(self, tmp_path: Path) -> Path:
        root = tmp_path / "catalog"
        (root / "recon").mkdir(parents=True)
        (root / "recon" / "thing.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": "thing",
                    "summary": "A thing",
                    "categories": ["recon"],
                    "binaries": ["thing"],
                    "install": [{"provider": "apt", "package": "thing"}],
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_a_dry_run_changes_nothing_on_disk(self, tmp_path, monkeypatch):
        root = self._tree(tmp_path)
        before = (root / "recon" / "thing.yaml").read_text(encoding="utf-8")
        _installed(monkeypatch, "thing")

        report = probe_source_tree(
            root, runner=_answers({"thing --version": (0, "thing 1.2")})
        )

        assert report.found == 1
        assert report.written == 0
        assert (root / "recon" / "thing.yaml").read_text(encoding="utf-8") == before

    def test_writing_records_the_command_in_the_yaml(self, tmp_path, monkeypatch):
        root = self._tree(tmp_path)
        _installed(monkeypatch, "thing")

        report = probe_source_tree(
            root,
            write=True,
            runner=_answers({"thing --version": (0, "thing 1.2")}),
        )

        assert report.written == 1
        written = yaml.safe_load(
            (root / "recon" / "thing.yaml").read_text(encoding="utf-8")
        )
        assert written["verify"] == "thing --version"

    def test_a_help_only_answer_is_not_written_by_default(self, tmp_path, monkeypatch):
        """The default is conservative on purpose: `verify:` is read as "this
        tool works", and a help screen does not show that."""
        root = self._tree(tmp_path)
        _installed(monkeypatch, "thing")

        report = probe_source_tree(
            root,
            write=True,
            runner=_answers({"thing --help": (0, "usage: thing")}),
        )

        assert report.found == 1
        assert report.versioned == 0
        assert report.written == 0

    def test_accepting_help_is_opt_in(self, tmp_path, monkeypatch):
        root = self._tree(tmp_path)
        _installed(monkeypatch, "thing")

        report = probe_source_tree(
            root,
            write=True,
            versioned_only=False,
            runner=_answers({"thing --help": (0, "usage: thing")}),
        )

        assert report.written == 1

    def test_an_annotated_entry_is_reported_rather_than_regenerated(
        self, tmp_path, monkeypatch
    ):
        """cosign and syft both answer a probe and both carry the comments
        explaining their signature pins. Writing over them would trade a
        reviewed explanation for a `verify:` line."""
        root = self._tree(tmp_path)
        entry = root / "recon" / "thing.yaml"
        entry.write_text(
            "# Read out of the real certificate.\n"
            + entry.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        _installed(monkeypatch, "thing")

        report = probe_source_tree(
            root,
            write=True,
            runner=_answers({"thing --version": (0, "thing 1.2")}),
        )

        assert report.found == 1
        assert report.written == 0
        assert report.annotated == 1
        assert "verify" not in entry.read_text(encoding="utf-8")

    def test_the_report_separates_absent_tools_from_unanswered_ones(
        self, tmp_path, monkeypatch
    ):
        """The gap between candidates and installed is the ceiling on what any
        one machine can contribute, and reporting it as failure would hide
        that."""
        root = self._tree(tmp_path)
        _installed(monkeypatch)

        report = probe_source_tree(root, runner=_answers({}))

        assert report.candidates == 1
        assert report.installed == 0
        assert report.no_answer == 0


def test_the_report_serialises_without_the_probe_objects():
    """`--json` is consumed by CI; the counts are the stable part."""
    report = probe_verify.ProbeReport(
        candidates=10, installed=4, found=3, versioned=2, no_answer=1, written=3,
        probes=(Probe("thing", STATUS_FOUND, command="thing --version"),),
    )
    payload = report.to_dict()
    assert payload["candidates"] == 10
    assert "probes" not in payload

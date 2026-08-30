"""Offline bundles.

A bundle is the one place in this program where the archive is untrusted input
and the machine reading it was chosen precisely because it is isolated — no
network to re-fetch from, and often nobody to ask. So the tests that matter
here are the ones about refusing a bad bundle, not the ones about writing a
good one.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from loadout import bundle
from loadout.errors import BundleError


def make_payload(root: Path, contents: bytes = b"pretend this is a .deb") -> Path:
    target = root / bundle.PAYLOAD_DIR / "apt" / "nmap" / "nmap_7.99_amd64.deb"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(contents)
    return target


def make_bundle(tmp_path: Path, *, name: str = "kit.tar") -> tuple[Path, Path]:
    """A real bundle, built the way the command builds one."""
    root = tmp_path / "staging"
    root.mkdir()
    payload = make_payload(root)
    tool = bundle.BundledTool(
        tool_id="nmap",
        provider="apt",
        files=[
            bundle.BundledFile(
                path=str(payload.relative_to(root).as_posix()), sha256="", size=0
            )
        ],
        spec={"package": "nmap"},
    )
    manifest = bundle.build_manifest([tool], [], root)
    archive = tmp_path / name
    bundle.write(archive, manifest, root)
    return archive, root


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_a_bundle_round_trips(tmp_path):
    archive, _ = make_bundle(tmp_path)
    extracted = tmp_path / "out"
    manifest = bundle.extract(archive, extracted)

    assert [t.tool_id for t in manifest.tools] == ["nmap"]
    assert manifest.tools[0].provider == "apt"
    assert (extracted / manifest.files[0].path).read_bytes() == b"pretend this is a .deb"


def test_the_manifest_is_readable_without_extracting(tmp_path):
    """Someone holding a bundle should be able to see what is in it before
    committing to unpacking it."""
    archive, _ = make_bundle(tmp_path)
    manifest = bundle.read_manifest(archive)
    assert manifest.format == bundle.FORMAT_VERSION
    assert manifest.arch


def test_a_gzipped_bundle_works_too(tmp_path):
    archive, _ = make_bundle(tmp_path, name="kit.tar.gz")
    assert tarfile.is_tarfile(archive)
    assert bundle.read_manifest(archive).tools


def test_every_payload_file_gets_a_checksum_and_size(tmp_path):
    archive, _ = make_bundle(tmp_path)
    entry = bundle.read_manifest(archive).files[0]
    assert len(entry.sha256) == 64
    assert entry.size == len(b"pretend this is a .deb")


# ---------------------------------------------------------------------------
# Refusing a bad bundle
# ---------------------------------------------------------------------------


def test_a_tampered_payload_is_rejected(tmp_path):
    """The artifacts inside were checksummed and signature-checked when they
    were fetched. That means nothing if the tar can be edited afterwards."""
    archive, _ = make_bundle(tmp_path)
    manifest = bundle.read_manifest(archive)

    # Extract through the real path (which verifies), then tamper: this is the
    # bundle-edited-in-transit case, not a malformed-archive one.
    extracted = tmp_path / "out"
    bundle.extract(archive, extracted)
    (extracted / manifest.files[0].path).write_bytes(b"malicious replacement")

    with pytest.raises(BundleError, match="sha256 mismatch"):
        bundle.verify_payload(manifest, extracted)


def test_a_file_missing_from_the_payload_is_rejected(tmp_path):
    archive, _ = make_bundle(tmp_path)
    manifest = bundle.read_manifest(archive)
    extracted = tmp_path / "out"
    extracted.mkdir()
    with pytest.raises(BundleError, match="not in the bundle"):
        bundle.verify_payload(manifest, extracted)


def test_a_manifest_entry_with_no_checksum_is_rejected(tmp_path):
    """An entry with an empty sha256 must not be treated as "nothing to check"."""
    _, root = make_bundle(tmp_path)
    manifest = bundle.Manifest(
        tools=[
            bundle.BundledTool(
                tool_id="nmap",
                provider="apt",
                files=[
                    bundle.BundledFile(
                        path="payload/apt/nmap/nmap_7.99_amd64.deb", sha256="", size=1
                    )
                ],
            )
        ]
    )
    with pytest.raises(BundleError, match="no checksum recorded"):
        bundle.verify_payload(manifest, root)


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "../../etc/cron.d/backdoor",
        "/etc/shadow",
        "payload/../../escape",
        "C:/Windows/System32/evil",
    ],
)
def test_paths_that_would_write_outside_the_target_are_refused(name):
    """A tar can name anything and most extractors oblige."""
    assert not bundle.safe_member(name)


@pytest.mark.parametrize(
    "name", ["manifest.json", "payload/apt/nmap/x.deb", "payload/github/ffuf/ffuf.tar.gz"]
)
def test_ordinary_paths_are_allowed(name):
    assert bundle.safe_member(name)


def test_a_traversing_member_stops_the_whole_extraction(tmp_path):
    """Refuse the archive, rather than skipping the bad member and carrying on
    with the rest -- an archive containing one is not one to trust the rest of."""
    archive = tmp_path / "evil.tar"
    payload = tmp_path / "evil.txt"
    payload.write_text("pwned", encoding="utf-8")
    manifest = {"format": 1, "tools": [], "skipped": []}
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

    with tarfile.open(archive, "w") as tar:
        tar.add(manifest_file, arcname="manifest.json")
        tar.add(payload, arcname="../escaped.txt")

    with pytest.raises(BundleError, match="unsafe path"):
        bundle.extract(archive, tmp_path / "out")
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_a_symlink_member_is_refused(tmp_path):
    """A symlink in the archive can redirect a later write anywhere."""
    archive = tmp_path / "link.tar"
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(
        json.dumps({"format": 1, "tools": [], "skipped": []}), encoding="utf-8"
    )
    with tarfile.open(archive, "w") as tar:
        tar.add(manifest_file, arcname="manifest.json")
        info = tarfile.TarInfo("payload/sneaky")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)

    with pytest.raises(BundleError, match="link"):
        bundle.extract(archive, tmp_path / "out")


def test_a_non_bundle_tar_says_so(tmp_path):
    archive = tmp_path / "random.tar"
    other = tmp_path / "hello.txt"
    other.write_text("hi", encoding="utf-8")
    with tarfile.open(archive, "w") as tar:
        tar.add(other, arcname="hello.txt")
    with pytest.raises(BundleError, match="is it a Loadout bundle"):
        bundle.read_manifest(archive)


def test_a_missing_bundle_says_so(tmp_path):
    with pytest.raises(BundleError, match="no such bundle"):
        bundle.read_manifest(tmp_path / "nope.tar")


def test_a_future_format_is_refused_with_advice(tmp_path):
    """Better to refuse than to misread a format this version predates."""
    with pytest.raises(BundleError, match="newer than this Loadout"):
        bundle.Manifest.from_dict({"format": bundle.FORMAT_VERSION + 1})


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


def test_an_architecture_mismatch_is_reported(monkeypatch):
    """Debs for the wrong architecture will not install, and finding that out
    on the isolated machine is too late."""
    monkeypatch.setattr(bundle, "current_platform", lambda: ("kali", "arm64"))
    manifest = bundle.Manifest(distro="kali", arch="amd64")
    warnings = " ".join(bundle.platform_warnings(manifest))
    assert "amd64" in warnings and "arm64" in warnings


def test_a_matching_platform_is_quiet(monkeypatch):
    monkeypatch.setattr(bundle, "current_platform", lambda: ("kali", "amd64"))
    assert bundle.platform_warnings(bundle.Manifest(distro="kali", arch="amd64")) == []


def test_a_distro_difference_warns_but_does_not_shout_about_arch(monkeypatch):
    monkeypatch.setattr(bundle, "current_platform", lambda: ("debian", "amd64"))
    warnings = bundle.platform_warnings(bundle.Manifest(distro="kali", arch="amd64"))
    assert len(warnings) == 1
    assert "usually fine" in warnings[0]


# ---------------------------------------------------------------------------
# What can and cannot travel
# ---------------------------------------------------------------------------


def test_package_manager_control_files_never_travel(tmp_path):
    """apt creates `lock` and `partial/` in whatever archives directory it is
    pointed at. Shipping them puts a zero-byte lock file on the far side."""
    root = tmp_path / "staging"
    dest = root / bundle.PAYLOAD_DIR / "apt" / "nmap"
    dest.mkdir(parents=True)
    (dest / "nmap.deb").write_bytes(b"real")
    (dest / "lock").write_bytes(b"")
    (dest / "partial").mkdir()
    (dest / "partial" / "half.deb").write_bytes(b"incomplete")

    from types import SimpleNamespace

    action = SimpleNamespace(
        provider="apt",
        tool=SimpleNamespace(id="nmap"),
        method=SimpleNamespace(spec={"package": "nmap"}),
    )
    collected = bundle.collect(root, SimpleNamespace(actions=[action]))
    names = [Path(f.path).name for f in collected[0].files]
    assert names == ["nmap.deb"]


def test_providers_that_cannot_travel_raise_rather_than_pretend():
    """A bundle that silently held less than it claimed would be discovered on
    the isolated machine, which is the worst place to discover it."""
    from loadout.model import InstallMethod, Tool
    from loadout.providers import get_provider

    for name in ("go", "cargo", "pipx", "npm", "gem"):
        provider = get_provider(name)
        with pytest.raises(BundleError, match="cannot be bundled"):
            provider.plan_fetch(
                Tool(id="x"), InstallMethod(provider=name, spec={}), Path("/tmp/x")
            )


def test_apt_and_github_are_the_bundleable_set():
    assert set(bundle.BUNDLEABLE) == {"apt", "github"}


def test_apt_fetch_downloads_dependencies_and_does_not_install(tmp_path):
    """`apt-get download` fetches one package and nothing it needs, which on an
    isolated machine means stopping at the first missing dependency."""
    from loadout.model import InstallMethod, Tool
    from loadout.providers import get_provider

    steps = get_provider("apt").plan_fetch(
        Tool(id="nmap"), InstallMethod(provider="apt", spec={"package": "nmap"}), tmp_path
    )
    argv = next(s.argv for s in steps if hasattr(s, "argv"))
    assert "--download-only" in argv
    assert "--reinstall" in argv, "without this, apt skips packages already installed here"
    assert f"Dir::Cache::archives={tmp_path}" in argv
    assert "remove" not in argv


def test_apt_local_install_never_reaches_the_network(tmp_path):
    """The whole point: this runs where there is no network."""
    from loadout.model import InstallMethod, Tool
    from loadout.providers import get_provider

    deb = tmp_path / "nmap.deb"
    deb.write_bytes(b"x")
    steps = get_provider("apt").plan_install_local(
        Tool(id="nmap"), InstallMethod(provider="apt", spec={"package": "nmap"}), [deb]
    )
    argv = steps[0].argv
    assert "--no-download" in argv
    assert str(deb) in argv


def test_apt_local_install_refuses_a_bundle_with_no_debs(tmp_path):
    from loadout.errors import ProviderError
    from loadout.model import InstallMethod, Tool
    from loadout.providers import get_provider

    with pytest.raises(ProviderError, match=r"no \.deb files"):
        get_provider("apt").plan_install_local(
            Tool(id="nmap"),
            InstallMethod(provider="apt", spec={"package": "nmap"}),
            [tmp_path / "notes.txt"],
        )


def test_a_bundleable_route_is_preferred_over_a_higher_priority_one():
    """A tool with both a go and an apt route resolves to go for a live
    install. Bundling it that way would drop it for no reason."""
    from loadout.model import InstallMethod, Tool

    tool = Tool(
        id="nuclei",
        install=(
            InstallMethod(provider="go", spec={"module": "x"}, priority=10),
            InstallMethod(provider="apt", spec={"package": "nuclei"}, priority=90),
        ),
    )

    class FakePlanner:
        def viable_methods(self, tool):
            return [(m.provider, m) for m in sorted(tool.install, key=lambda m: m.priority)]

    name, _ = bundle.choose_bundleable_method(FakePlanner(), tool)
    assert name == "apt"


def test_fetching_is_not_recorded_as_installed():
    """Building a bundle installs nothing, so `list --installed` on the build
    machine must not grow entries for it."""
    from loadout.planner import ACTION_FETCH

    assert ACTION_FETCH != "install"
    from loadout.executor import past_tense

    assert past_tense(ACTION_FETCH) == "fetched"

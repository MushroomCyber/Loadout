from kalitools import profiles


def test_bundled_profiles_load():
    loaded = profiles.load_profiles()
    assert "pentester-web" in loaded
    assert "forensics-starter" in loaded
    assert "osint-minimal" in loaded
    assert "bug-bounty" in loaded
    assert "ctf-basics" in loaded


def test_profile_shape():
    prof = profiles.get_profile("pentester-web")
    assert prof is not None
    assert prof.name
    assert prof.packages
    assert all(isinstance(p, str) and p for p in prof.packages)


def test_resolve_packages_filters_unknown():
    prof = profiles.get_profile("pentester-web")
    resolved = profiles.resolve_packages(prof, known_packages={"nmap", "ffuf"})
    assert resolved == ["ffuf"]


def test_missing_profile_returns_none():
    assert profiles.get_profile("does-not-exist") is None

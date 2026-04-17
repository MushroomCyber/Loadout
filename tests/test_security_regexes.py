from kalitools.manager import (
    _CONTROL_CHARS_RE,
    _LAUNCH_LEADING_TOKEN_RE,
    _LAUNCH_METACHARS,
    _PACKAGE_NAME_RE,
    _SAFE_ABS_PATH_RE,
)


def test_package_name_allowlist():
    assert _PACKAGE_NAME_RE.match("nmap")
    assert _PACKAGE_NAME_RE.match("kali-tools-web")
    assert _PACKAGE_NAME_RE.match("lib32z1")
    assert _PACKAGE_NAME_RE.match("python3.11")
    assert not _PACKAGE_NAME_RE.match("; rm -rf /")
    assert not _PACKAGE_NAME_RE.match("pkg with space")
    assert not _PACKAGE_NAME_RE.match("Package")


def test_safe_abs_path_rejects_crlf():
    assert _SAFE_ABS_PATH_RE.match("/srv/mirror")
    assert not _SAFE_ABS_PATH_RE.match("/srv\nmirror")
    assert not _SAFE_ABS_PATH_RE.match("relative/path")


def test_control_chars_detected():
    assert _CONTROL_CHARS_RE.search("hello\n")
    assert _CONTROL_CHARS_RE.search("hello\x00there")
    assert not _CONTROL_CHARS_RE.search("plain text")


def test_launch_leading_token():
    assert _LAUNCH_LEADING_TOKEN_RE.match("nmap")
    assert _LAUNCH_LEADING_TOKEN_RE.match("/usr/bin/nmap")
    assert not _LAUNCH_LEADING_TOKEN_RE.match("rm -rf /")
    assert not _LAUNCH_LEADING_TOKEN_RE.match("$PATH")


def test_launch_metachars_set_nonempty():
    assert _LAUNCH_METACHARS
    assert all(isinstance(c, str) for c in _LAUNCH_METACHARS)

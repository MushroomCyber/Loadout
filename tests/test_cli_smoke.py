import subprocess
import sys


def test_help_runs():
    result = subprocess.run(
        [sys.executable, "-m", "kalitools", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "kalitools" in result.stdout.lower()
    assert "install" in result.stdout
    assert "profile" in result.stdout
    assert "catalog" in result.stdout


def test_version_flag():
    result = subprocess.run(
        [sys.executable, "-m", "kalitools", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip().startswith("kalitools ")


def test_profile_list_runs():
    result = subprocess.run(
        [sys.executable, "-m", "kalitools", "profile", "list"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # May fail on systems without apt; accept either success or controlled
    # failure, but help text must not crash with a traceback.
    assert "Traceback" not in result.stderr

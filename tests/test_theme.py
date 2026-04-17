from kalitools.theme import EMOJI_TO_ASCII, THEMES, get_theme, strip_emojis


def test_theme_registry_contains_defaults():
    assert "default" in THEMES
    assert "mono" in THEMES
    assert "solarized-dark" in THEMES
    assert "high-contrast" in THEMES


def test_get_theme_falls_back_to_default():
    assert get_theme("not-a-real-theme") is THEMES["default"]


def test_strip_emojis_replaces_known_glyphs():
    src = "✓ success ⚠️ warning 🛡️"
    out = strip_emojis(src)
    assert "✓" not in out
    assert "⚠️" not in out
    assert "[ok]" in out
    assert "[!]" in out


def test_emoji_map_is_non_empty():
    assert EMOJI_TO_ASCII

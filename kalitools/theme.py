"""Rich theme registry and ASCII fallback helpers."""

from __future__ import annotations

from rich.theme import Theme

THEMES: dict[str, Theme] = {
    "default": Theme({
        "info": "cyan",
        "success": "bold green",
        "warning": "yellow",
        "danger": "bold red",
        "muted": "dim",
    }),
    "mono": Theme({
        "info": "white",
        "success": "bold white",
        "warning": "white",
        "danger": "bold white",
        "muted": "dim",
    }),
    "solarized-dark": Theme({
        "info": "#268bd2",
        "success": "#859900",
        "warning": "#b58900",
        "danger": "#dc322f",
        "muted": "#586e75",
    }),
    "high-contrast": Theme({
        "info": "bright_cyan",
        "success": "bright_green",
        "warning": "bright_yellow",
        "danger": "bright_red",
        "muted": "bright_white",
    }),
}


# Emoji -> ASCII fallback mapping used when `--no-emoji` is active.
EMOJI_TO_ASCII: dict[str, str] = {
    # status / marks
    "✓": "[ok]",
    "✗": "[x]",
    "✅": "[ok]",
    "❌": "[x]",
    "⚠️": "[!]",
    "⚠": "[!]",
    "⭕": "[ ]",
    "🟢": "[ok]",
    "🔴": "[x]",
    "🟡": "[!]",
    "★": "*",
    "☆": "-",
    "🏁": ">",
    "🎯": ">",
    "💡": "i",
    "ℹ️": "i",
    "🔎": "?",
    "🔍": "?",
    "❓": "?",
    # category icons
    "🛡️": "[sec]",
    "🛡": "[sec]",
    "🔧": "[tool]",
    "🌐": "[web]",
    "📡": "[wifi]",
    "🧪": "[vuln]",
    "💥": "[expl]",
    "🔐": "[crypto]",
    "🎣": "[phish]",
    "🗄️": "[db]",
    "🗄": "[db]",
    "🧰": "[util]",
    "🔓": "[pw]",
    "📦": "[pkg]",
    "📁": "[dir]",
    "📋": "[list]",
    "🔄": "[sync]",
    "⬆️": "[up]",
    "⬇️": "[dn]",
    "🔒": "[locked]",
    "⏱️": "[time]",
    "⏳": "[wait]",
    "🚀": "[run]",
    "💾": "[save]",
}


def strip_emojis_from_text(text: str) -> str:
    """Public alias kept for external callers."""
    return strip_emojis(text)


def get_theme(name: str) -> Theme:
    return THEMES.get(name, THEMES["default"])


def strip_emojis(text: str) -> str:
    out = text
    for glyph, repl in EMOJI_TO_ASCII.items():
        if glyph in out:
            out = out.replace(glyph, repl)
    return out

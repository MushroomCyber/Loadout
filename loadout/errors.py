"""Exception hierarchy.

Every error a user can trigger carries a remediation string, so the CLI can
always tell them what to do next instead of printing a traceback.
"""

from __future__ import annotations


class LoadoutError(Exception):
    """Base class. Carries an optional remediation hint."""

    exit_code = 1

    def __init__(self, message: str, *, remediation: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation

    def __str__(self) -> str:
        return self.message


class CatalogError(LoadoutError):
    """The catalog is missing, unreadable, or invalid."""

    exit_code = 3


class CatalogMissing(CatalogError):
    def __init__(self) -> None:
        super().__init__(
            "No tool catalog found.",
            remediation="Run `loadout catalog update` to download one.",
        )


class ToolNotFound(LoadoutError):
    exit_code = 4

    def __init__(self, tool_id: str, *, suggestions: list[str] | None = None) -> None:
        hint = ""
        if suggestions:
            hint = f"Did you mean: {', '.join(suggestions[:5])}?"
        super().__init__(f"Unknown tool: {tool_id!r}", remediation=hint)
        self.tool_id = tool_id
        self.suggestions = suggestions or []


class NoViableProvider(LoadoutError):
    """The tool exists but nothing on this machine can install it."""

    exit_code = 5

    def __init__(self, tool_id: str, *, tried: list[str] | None = None) -> None:
        tried = tried or []
        detail = f" (catalog offers: {', '.join(tried)})" if tried else ""
        super().__init__(
            f"No available installer for {tool_id!r}{detail}",
            remediation="Install one of those package managers, or run "
            "`loadout providers` to see what was detected.",
        )
        self.tool_id = tool_id
        self.tried = tried


class ProviderError(LoadoutError):
    exit_code = 6


class VerificationError(LoadoutError):
    """A downloaded artifact failed checksum or signature verification."""

    exit_code = 7

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            remediation="Refusing to install unverified code. Pass "
            "--allow-unverified only if you have checked the artifact yourself.",
        )


class PrivilegeError(LoadoutError):
    exit_code = 8


class BundleError(LoadoutError):
    """An offline bundle could not be built, read, or trusted."""

    exit_code = 10


class UnsafeArgument(LoadoutError):
    """Something that would have reached an argv failed validation."""

    exit_code = 9

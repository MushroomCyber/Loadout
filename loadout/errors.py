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

    def __init__(
        self,
        tool_id: str,
        *,
        tried: list[str] | None = None,
        unusable: list[str] | None = None,
    ) -> None:
        tried = tried or []
        unusable = unusable or []
        detail = f" (catalog offers: {', '.join(tried)})" if tried else ""
        # A route ruled out for a specific, knowable reason is far more useful
        # than the generic "install one of these package managers", which is
        # wrong advice when the manager is present and the package is the
        # problem.
        if unusable:
            remediation = "; ".join(unusable)
        else:
            remediation = (
                "Install one of those package managers, or run "
                "`loadout providers` to see what was detected."
            )
        super().__init__(
            f"No available installer for {tool_id!r}{detail}",
            remediation=remediation,
        )
        self.tool_id = tool_id
        self.tried = tried
        self.unusable = unusable


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


class NothingToVerifyAgainst(VerificationError):
    """The upstream published nothing to check the download against.

    Kept apart from a *failed* check, which this must never be confused with:
    a digest mismatch means the bytes are not the ones that were published,
    and no flag makes that installable. This means there was never anything to
    compare against, which a user can knowingly accept -- so it is the one
    verification failure a caller may offer to waive.
    """


class PrivilegeError(LoadoutError):
    exit_code = 8


class BundleError(LoadoutError):
    """An offline bundle could not be built, read, or trusted."""

    exit_code = 10


class UnsafeArgument(LoadoutError):
    """Something that would have reached an argv failed validation."""

    exit_code = 9

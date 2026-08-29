"""Validation for hand-written catalog entries.

Catalog entries are YAML files reviewed by pull request, so validation is a
contributor-facing feature: every error names the file, the field and the fix.
CI runs :func:`validate_entry` over the whole ``catalog/`` tree and fails the
build on any error, which is what keeps a community-editable catalog trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..model import TOOL_ID_RE, InstallMethod, Tool

#: Categories a tool may claim. Deliberately wider than the previous
#: offence-only taxonomy -- "all good security tools" has to include the blue
#: and purple team or the name overpromises.
CATEGORIES: dict[str, str] = {
    # offensive
    "recon": "Reconnaissance & OSINT",
    "web": "Web application testing",
    "network": "Network utilities",
    "wireless": "Wireless & radio",
    "exploitation": "Exploitation frameworks",
    "post-exploitation": "Post-exploitation & lateral movement",
    "password": "Password attacks & cracking",
    "vuln-scan": "Vulnerability scanning",
    "fuzzing": "Fuzzing",
    "database": "Database assessment",
    "social": "Social engineering",
    "mobile": "Mobile security",
    "cloud": "Cloud & container security",
    "hardware": "Hardware & embedded",
    # defensive / analytical
    "forensics": "Digital forensics",
    "incident-response": "Incident response & DFIR",
    "detection": "Detection engineering & hunting",
    "monitoring": "Monitoring & IDS",
    "sniffing": "Traffic capture & analysis",
    "reverse": "Reverse engineering",
    "malware": "Malware analysis",
    "crypto": "Cryptography & steganography",
    "threat-intel": "Threat intelligence",
    # supporting
    "reporting": "Reporting & collaboration",
    "utility": "General utilities",
    "other": "Uncategorised",
}

#: Engagement stages, so `loadout phase lateral-movement` can work. Aligned with
#: PTES and the ATT&CK tactics practitioners already plan around.
PHASES: dict[str, str] = {
    "reconnaissance": "Passive and active information gathering",
    "resource-development": "Infrastructure and payload preparation",
    "initial-access": "Getting the first foothold",
    "execution": "Running code on a target",
    "persistence": "Maintaining access",
    "privilege-escalation": "Gaining higher permissions",
    "defense-evasion": "Avoiding detection",
    "credential-access": "Harvesting credentials",
    "discovery": "Enumerating the environment",
    "lateral-movement": "Moving between hosts",
    "collection": "Gathering target data",
    "command-and-control": "Operator channels",
    "exfiltration": "Moving data out",
    "impact": "Disruption and destruction",
    "analysis": "Post-hoc analysis of artifacts",
    "reporting": "Documenting findings",
}

_REQUIRED = ("id",)
_KNOWN_FIELDS = {
    "id", "summary", "description", "categories", "tags", "phases", "binaries",
    "homepage", "repo", "license", "install", "alternatives", "requires_root",
    "verify", "size", "version", "deprecated_by", "metadata",
}


@dataclass
class ValidationResult:
    tool: Tool | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.tool is not None and not self.errors


def validate_entry(data: Any, *, origin: str = "<entry>") -> ValidationResult:
    """Check one catalog entry. Never raises -- collects everything at once so a
    contributor sees all their mistakes in one run, not one per iteration."""
    result = ValidationResult()

    if not isinstance(data, dict):
        result.errors.append(f"{origin}: entry must be a YAML mapping")
        return result

    for key in _REQUIRED:
        if not data.get(key):
            result.errors.append(f"{origin}: missing required field '{key}'")

    unknown = set(data) - _KNOWN_FIELDS
    for key in sorted(unknown):
        result.warnings.append(f"{origin}: unknown field '{key}' (ignored)")

    tool_id = str(data.get("id", "")).strip().lower()
    if tool_id and not TOOL_ID_RE.match(tool_id):
        result.errors.append(
            f"{origin}: id {tool_id!r} must match [a-z0-9][a-z0-9._-]*"
        )

    for value in data.get("categories") or []:
        if str(value).strip().lower() not in CATEGORIES:
            result.errors.append(
                f"{origin}: unknown category {value!r}. "
                f"Valid: {', '.join(sorted(CATEGORIES))}"
            )
    for value in data.get("phases") or []:
        if str(value).strip().lower() not in PHASES:
            result.errors.append(
                f"{origin}: unknown phase {value!r}. Valid: {', '.join(sorted(PHASES))}"
            )

    install = data.get("install") or []
    if not isinstance(install, list):
        result.errors.append(f"{origin}: 'install' must be a list of methods")
        install = []
    if not install:
        result.warnings.append(
            f"{origin}: no install methods -- the tool can be browsed but not installed"
        )

    from ..providers import known_provider_names

    valid_providers = known_provider_names()
    for index, method in enumerate(install):
        label = f"{origin}: install[{index}]"
        if not isinstance(method, dict):
            result.errors.append(f"{label}: must be a mapping")
            continue
        provider = str(method.get("provider", "")).strip().lower()
        if not provider:
            result.errors.append(f"{label}: missing 'provider'")
            continue
        if provider not in valid_providers:
            result.errors.append(
                f"{label}: unknown provider {provider!r}. "
                f"Valid: {', '.join(sorted(valid_providers))}"
            )
            continue
        errors = _validate_method_spec(provider, method, label)
        result.errors.extend(errors)

    if not data.get("summary"):
        result.warnings.append(f"{origin}: no summary -- it will show as a blank row")
    elif len(str(data["summary"])) > 120:
        result.warnings.append(f"{origin}: summary over 120 chars will be truncated")

    if not data.get("binaries"):
        result.warnings.append(
            f"{origin}: no 'binaries' -- `loadout run` and `--help` cannot work for it"
        )

    if result.errors:
        return result

    try:
        result.tool = Tool.from_dict(data)
    except Exception as exc:
        result.errors.append(f"{origin}: {exc}")
    return result


def _validate_method_spec(provider: str, method: dict[str, Any], label: str) -> list[str]:
    """Ask the provider itself which spec keys it requires."""
    from ..providers import get_provider

    try:
        impl = get_provider(provider)
    except KeyError:
        return [f"{label}: unknown provider {provider!r}"]

    spec = InstallMethod.from_dict(method).spec
    missing = [key for key in impl.required_spec_keys if key not in spec]
    if missing:
        return [
            f"{label}: provider {provider!r} requires {', '.join(missing)}"
        ]

    # A signature block is checked at catalog-validate time so a typo fails
    # review rather than an install on someone else's machine -- by which
    # point the artifact is already downloaded.
    if "signature" in spec:
        from ..signature import validate_spec as validate_signature

        return [f"{label}: {error}" for error in validate_signature(spec["signature"])]
    return []


def category_label(slug: str) -> str:
    return CATEGORIES.get(slug.lower(), slug.replace("-", " ").title())


def phase_label(slug: str) -> str:
    return PHASES.get(slug.lower(), slug.replace("-", " ").title())

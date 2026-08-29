"""Turn a request into an ordered, inspectable plan.

Planning is pure: it reads the catalog and the provider detection results and
returns data. Nothing is installed, nothing is printed, no subprocess runs. That
makes ``--dry-run`` free (it is just "print the plan and stop") and lets the
whole resolution layer be tested without a Debian box.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import NoViableProvider, ToolNotFound
from .model import InstallMethod, Tool
from .providers import (
    ProviderStatus,
    Step,
    available_providers,
    detect_distro,
    get_provider,
)

ACTION_INSTALL = "install"
ACTION_REMOVE = "remove"


@dataclass
class PlannedAction:
    tool: Tool
    action: str
    provider: str
    method: InstallMethod
    steps: list[Step] = field(default_factory=list)

    @property
    def needs_root(self) -> bool:
        return any(getattr(step, "elevate", False) for step in self.steps)

    def render(self) -> list[str]:
        return [step.render() for step in self.steps]


@dataclass
class SkippedTool:
    tool_id: str
    reason: str


@dataclass
class Plan:
    actions: list[PlannedAction] = field(default_factory=list)
    skipped: list[SkippedTool] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.actions)

    @property
    def needs_root(self) -> bool:
        return any(action.needs_root for action in self.actions)

    @property
    def providers_used(self) -> list[str]:
        seen: list[str] = []
        for action in self.actions:
            if action.provider not in seen:
                seen.append(action.provider)
        return seen

    @property
    def tool_ids(self) -> list[str]:
        return [action.tool.id for action in self.actions]

    def to_dict(self) -> dict[str, object]:
        return {
            "actions": [
                {
                    "tool": action.tool.id,
                    "action": action.action,
                    "provider": action.provider,
                    "needs_root": action.needs_root,
                    "steps": action.render(),
                }
                for action in self.actions
            ],
            "skipped": [{"tool": s.tool_id, "reason": s.reason} for s in self.skipped],
            "needs_root": self.needs_root,
        }


class Planner:
    """Resolves tools to concrete install routes."""

    def __init__(
        self,
        catalog,  # CatalogStore; untyped to avoid a hard import cycle
        *,
        distro: str = "",
        statuses: dict[str, ProviderStatus] | None = None,
        preferred: list[str] | None = None,
        installed: dict[str, set[str]] | None = None,
    ) -> None:
        self.catalog = catalog
        self.distro = distro or detect_distro()
        self.statuses = statuses if statuses is not None else available_providers()
        #: User-configured provider preference; earlier wins over catalog priority.
        self.preferred = [p.strip().lower() for p in (preferred or []) if p.strip()]
        #: Optional pre-fetched "what each provider already has" map.
        self.installed = installed or {}

    # -- resolution --------------------------------------------------------

    def viable_methods(self, tool: Tool) -> list[tuple[str, InstallMethod]]:
        """Every route that could work here, best first.

        Ordering: explicit user preference, then the catalog's per-method
        priority, then the provider's own default. Ties break on provider name
        so the result is deterministic and testable.
        """
        candidates: list[tuple[tuple[int, int, int, str], str, InstallMethod]] = []
        for method in tool.install:
            if not method.applies_to(self.distro):
                continue
            status = self.statuses.get(method.provider)
            if status is None or not status.available:
                continue
            try:
                provider = get_provider(method.provider)
            except KeyError:
                continue
            preference = (
                self.preferred.index(method.provider)
                if method.provider in self.preferred
                else len(self.preferred) + 1
            )
            sort_key = (
                preference,
                method.priority,
                provider.default_priority,
                method.provider,
            )
            candidates.append((sort_key, method.provider, method))
        candidates.sort(key=lambda item: item[0])
        return [(name, method) for _key, name, method in candidates]

    def choose_method(self, tool: Tool) -> tuple[str, InstallMethod]:
        viable = self.viable_methods(tool)
        if not viable:
            raise NoViableProvider(
                tool.id, tried=[m.provider for m in tool.install]
            )
        return viable[0]

    def resolve(self, tool_id: str) -> Tool:
        tool = self.catalog.get(tool_id)
        if tool is None:
            raise ToolNotFound(tool_id, suggestions=self.catalog.suggest(tool_id))
        return tool

    def is_installed(self, tool: Tool, provider_name: str, method: InstallMethod) -> bool:
        known = self.installed.get(provider_name)
        if known is None:
            return False
        for key in ("package", "formula", "crate", "gem", "image"):
            value = method.spec.get(key)
            if value and str(value) in known:
                return True
        return bool(tool.primary_binary and tool.primary_binary in known)

    # -- plan building -----------------------------------------------------

    def plan(
        self,
        tool_ids: list[str],
        *,
        action: str = ACTION_INSTALL,
        skip_installed: bool = True,
        provider_override: str = "",
    ) -> Plan:
        plan = Plan()
        seen: set[str] = set()

        for raw_id in tool_ids:
            tool_id = raw_id.strip().lower()
            if not tool_id or tool_id in seen:
                continue
            seen.add(tool_id)

            try:
                tool = self.resolve(tool_id)
            except ToolNotFound as exc:
                hint = f" {exc.remediation}" if exc.remediation else ""
                plan.skipped.append(SkippedTool(tool_id, f"not in catalog.{hint}"))
                continue

            try:
                if provider_override:
                    methods = tool.methods_for(provider_override)
                    if not methods:
                        plan.skipped.append(
                            SkippedTool(
                                tool_id,
                                f"no {provider_override} install method in the catalog",
                            )
                        )
                        continue
                    provider_name, method = provider_override, methods[0]
                else:
                    provider_name, method = self.choose_method(tool)
            except NoViableProvider as exc:
                plan.skipped.append(SkippedTool(tool_id, exc.message))
                continue

            already = self.is_installed(tool, provider_name, method)
            if action == ACTION_INSTALL and skip_installed and already:
                plan.skipped.append(SkippedTool(tool_id, "already installed"))
                continue
            if action == ACTION_REMOVE and self.installed and not already:
                plan.skipped.append(SkippedTool(tool_id, "not installed"))
                continue

            provider = get_provider(provider_name)
            try:
                steps = (
                    provider.plan_install(tool, method)
                    if action == ACTION_INSTALL
                    else provider.plan_remove(tool, method)
                )
            except NotImplementedError as exc:
                plan.skipped.append(SkippedTool(tool_id, str(exc)))
                continue
            except Exception as exc:
                plan.skipped.append(SkippedTool(tool_id, f"cannot plan: {exc}"))
                continue

            plan.actions.append(
                PlannedAction(
                    tool=tool,
                    action=action,
                    provider=provider_name,
                    method=method,
                    steps=steps,
                )
            )

        return _coalesce_apt(plan, action)


def _coalesce_apt(plan: Plan, action: str) -> Plan:
    """Merge consecutive apt actions into one transaction.

    Installing eighteen packages from a loadout should be one ``apt-get
    install`` with eighteen names, not eighteen sudo calls each re-reading the
    package lists. Non-apt actions are left untouched and keep their order.
    """
    from .providers.apt import AptProvider
    from .providers.base import CommandStep

    apt_actions = [a for a in plan.actions if a.provider == "apt"]
    if len(apt_actions) < 2:
        return plan

    packages: list[str] = []
    for act in apt_actions:
        package = str(act.method.spec.get("package", "")).strip()
        if package and package not in packages:
            packages.append(package)
    if len(packages) < 2:
        return plan

    provider = AptProvider()
    verb = "install" if action == ACTION_INSTALL else "remove"
    argv = [*provider._base_argv(), verb, "-y"]
    if action == ACTION_INSTALL:
        argv += ["-o", "Dpkg::Options::=--force-confold"]
    argv += ["--", *packages]

    merged_step = CommandStep(
        argv=argv,
        description=f"apt-get {verb} {len(packages)} packages",
        elevate=True,
    )

    first = apt_actions[0]
    first.steps = [merged_step]
    for act in apt_actions[1:]:
        act.steps = []
    return plan

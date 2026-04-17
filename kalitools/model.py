"""Core data models for Kali tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Tool:
    """Typed representation of a Kali tool entry."""

    name: str
    commands: list[str] = field(default_factory=list)
    installed: bool = False
    category: str = 'other'
    subcategory: str = ''
    description: str = ''
    size: int = 0
    subpackages: list[str] = field(default_factory=list)
    source: str = ''
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = str(self.name or '').strip()
        self.commands = self._normalize_list(self.commands)
        if self.name and not any(cmd.lower() == self.name.lower() for cmd in self.commands):
            self.commands.insert(0, self.name)
        self.installed = bool(self.installed)
        self.category = (self.category or 'other').strip().lower() or 'other'
        self.subcategory = (self.subcategory or '').strip()
        self.description = (self.description or '').strip()
        self.size = int(self.size or 0)
        self.subpackages = self._normalize_list(self.subpackages)
        self.source = (self.source or '').strip()
        self.metadata = dict(self.metadata or {})

    @staticmethod
    def _normalize_list(values: Any) -> list[str]:
        if isinstance(values, str):
            values = [values]
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            text = str(value or '').strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'commands': list(self.commands),
            'installed': self.installed,
            'category': self.category,
            'subcategory': self.subcategory,
            'description': self.description,
            'size': self.size,
            'subpackages': list(self.subpackages),
            'source': self.source,
            'metadata': dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tool:
        return cls(
            name=data.get('name', ''),
            commands=data.get('commands') or [],
            installed=data.get('installed', False),
            category=data.get('category', 'other'),
            subcategory=data.get('subcategory', ''),
            description=data.get('description', ''),
            size=data.get('size', 0),
            subpackages=data.get('subpackages') or [],
            source=data.get('source', ''),
            metadata=data.get('metadata') or {},
        )

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str):
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any):
        setattr(self, key, value)

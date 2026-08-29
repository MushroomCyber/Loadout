#!/usr/bin/env python3
"""One-shot import of the kalitools 0.3 JSON catalog into the YAML source tree.

Run once, review the diff, commit. After this the JSON is dead: the YAML tree in
``catalog/`` is the source of truth and ``loadout catalog build`` compiles it.

Curated entries always win. Anything already present in ``catalog/`` is left
exactly as written -- this only fills in the long tail of names the old scraper
found, so hand-written metadata is never overwritten by a re-run.

    python tools/seed_from_legacy.py --json legacy/tools_merged.json --out catalog/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from loadout.catalog.compile import dump_tool, load_source_tree  # noqa: E402
from loadout.catalog.schema import CATEGORIES  # noqa: E402
from loadout.model import InstallMethod, Tool  # noqa: E402

#: Old category slug -> new. The old vocabulary was offence-only; everything it
#: had still exists, so this is mostly identity plus a couple of renames.
CATEGORY_MAP = {
    "web": "web",
    "wireless": "wireless",
    "forensics": "forensics",
    "exploitation": "exploitation",
    "password": "password",
    "recon": "recon",
    "sniffing": "sniffing",
    "reverse": "reverse",
    "social": "social",
    "database": "database",
    "crypto": "crypto",
    "network": "network",
    "vuln-scan": "vuln-scan",
    "other": "other",
}

APT_DISTROS = ("kali", "debian", "parrot", "ubuntu")


def convert(entry: dict) -> Tool | None:
    name = str(entry.get("name") or "").strip().lower()
    if not name:
        return None

    old_category = str(entry.get("category") or "other").strip().lower()
    category = CATEGORY_MAP.get(old_category, "other")
    if category not in CATEGORIES:
        category = "other"

    # The old model synthesised commands[0] from the package name, so those
    # values are not evidence of a real binary. Drop them rather than carry a
    # wrong `binaries` entry forward -- an empty field is honest, a wrong one
    # sends `loadout run` at a command that does not exist.
    commands = [c for c in entry.get("commands") or [] if c and c.lower() != name]

    tags: list[str] = []
    subcategory = str(entry.get("subcategory") or "").strip().lower()
    if subcategory and subcategory not in ("general", "misc", ""):
        tags.append(subcategory.replace("/", "-").replace(" ", "-"))

    return Tool(
        id=name,
        summary=str(entry.get("description") or "").strip(),
        categories=(category,),
        tags=tuple(tags),
        binaries=tuple(commands),
        size=int(entry.get("size") or 0),
        install=(
            InstallMethod(
                provider="apt", spec={"package": name}, distros=APT_DISTROS
            ),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, required=True, help="legacy tools_merged.json")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "catalog")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing entries instead of skipping them",
    )
    args = parser.parse_args()

    payload = json.loads(args.json.read_text(encoding="utf-8"))
    entries = payload.get("tools", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        print("error: unrecognised JSON shape", file=sys.stderr)
        return 2

    existing: set[str] = set()
    if args.out.is_dir():
        report = load_source_tree(args.out, strict=False)
        existing = {tool.id for tool in report.tools}
        print(f"{len(existing)} entry(ies) already curated -- leaving them alone")

    written = skipped = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        tool = convert(entry)
        if tool is None:
            continue
        if tool.id in existing and not args.overwrite:
            skipped += 1
            continue
        destination = args.out / tool.category / f"{tool.id}.yaml"
        dump_tool(tool, destination)
        written += 1

    print(f"wrote {written} entry(ies), skipped {skipped} curated one(s) -> {args.out}")
    print("next: python -m loadout catalog build --strict")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

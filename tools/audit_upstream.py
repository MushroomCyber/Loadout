"""Ask GitHub which catalogued tools have stopped moving.

    python tools/audit_upstream.py          # writes audit.json, prints a report

Public repository metadata only: the `archived` flag, the last push, and
whether the repository still resolves. A catalog that quietly recommends dead
tools is worse than one with fewer entries, and 790 entries is more than anyone
re-checks by hand.

`archived` is the signal worth acting on: it is a flag the project's own owners
set, so it is a statement rather than an inference. Age is reported but is NOT
deprecation -- macchanger and maskprocessor have not been touched in five years
because they are finished.

Needs `gh auth login` for the API budget; 382 repositories is well inside the
authenticated hourly limit and nowhere near the unauthenticated one.
"""
import concurrent.futures as cf
import json
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

TOKEN = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "loadout-catalog-audit",
}


def targets():
    found = {}
    for path in sorted(pathlib.Path("catalog").rglob("*.yaml")):
        import yaml

        entry = yaml.safe_load(path.read_text(encoding="utf-8"))
        slug = entry.get("repo")
        if not slug:
            match = re.match(
                r"https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)", entry.get("homepage") or ""
            )
            if match:
                slug = f"{match.group(1)}/{match.group(2).removesuffix('.git')}"
        if slug:
            found[entry["id"]] = (slug, entry.get("deprecated_by"), str(path))
    return found


def fetch(item):
    tool_id, (slug, deprecated, path) = item
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"https://api.github.com/repos/{slug}", headers=HEADERS),
            timeout=30,
        ) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        return {"id": tool_id, "slug": slug, "status": exc.code, "deprecated": deprecated, "path": path}
    except Exception as exc:  # one unreachable repo must not abort 382 lookups
        return {"id": tool_id, "slug": slug, "status": f"err:{exc}", "deprecated": deprecated, "path": path}
    return {
        "id": tool_id,
        "slug": slug,
        "status": 200,
        "archived": data.get("archived"),
        "disabled": data.get("disabled"),
        "pushed_at": data.get("pushed_at"),
        "stars": data.get("stargazers_count"),
        "full_name": data.get("full_name"),
        "deprecated": deprecated,
        "path": path,
    }


items = list(targets().items())
print(f"querying {len(items)} repositories...", file=sys.stderr)
with cf.ThreadPoolExecutor(max_workers=12) as pool:
    results = list(pool.map(fetch, items))

now = datetime.now(timezone.utc)
for row in results:
    if row.get("pushed_at"):
        pushed = datetime.fromisoformat(row["pushed_at"].replace("Z", "+00:00"))
        row["years"] = round((now - pushed).days / 365.25, 1)

pathlib.Path("audit.json").write_text(json.dumps(results, indent=1), encoding="utf-8")

reachable = [r for r in results if r["status"] == 200]
archived = [r for r in reachable if r.get("archived")]
gone = [r for r in results if r["status"] != 200]
quiet = [r for r in reachable if not r.get("archived") and (r.get("years") or 0) >= 4]

print(
    f"{len(results)} repositories: {len(archived)} archived, {len(gone)} unreachable, "
    f"{len(quiet)} with no push in 4+ years"
)
print()
print("ARCHIVED -- upstream itself says unmaintained")
for row in sorted(archived, key=lambda r: -(r.get("years") or 0)):
    flag = "" if row["deprecated"] else "   <-- no deprecated_by"
    print(f"  {row['id']:26s} {row['slug']:40s} {row.get('years')}y{flag}")
print()
print("UNREACHABLE -- repository moved or deleted")
for row in gone:
    print(f"  {row['id']:26s} {row['slug']:40s} {row['status']}")
print()
print("QUIET 4y+ -- review, but age alone is not deprecation")
for row in sorted(quiet, key=lambda r: -(r.get("years") or 0)):
    print(f"  {row['id']:26s} {row['slug']:40s} {row.get('years')}y  {row.get('stars')} stars")

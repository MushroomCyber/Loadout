#!/usr/bin/env bash
# Exercise the paths that unit tests can only assert on, never execute:
# real apt-cache/dpkg output, meta-package discovery, APT status-fd progress,
# and a genuine install/remove round trip.
#
# Run on a Debian-family host (Kali gives the best coverage -- it is the only
# one with kali-tools-* meta-packages, which is where categories come from).
#
#   bash tools/verify_linux.sh            # everything except the real install
#   bash tools/verify_linux.sh --install  # also install and remove a package
#
# Safe to run repeatedly. The only system change is the optional install, which
# is removed again at the end.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${LOADOUT_VENV:-$HOME/.loadout-verify}"
DO_INSTALL=0
[[ "${1:-}" == "--install" ]] && DO_INSTALL=1

pass=0; fail=0; skip=0
say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=$((fail+1)); }
meh()  { printf '  \033[33mSKIP\033[0m %s\n' "$*"; skip=$((skip+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }

say "Environment"
. /etc/os-release 2>/dev/null || true
echo "  distro:  ${PRETTY_NAME:-unknown} (ID=${ID:-?})"
echo "  python:  $(python3 --version 2>&1)"
echo "  repo:    $REPO_DIR"
[[ "$REPO_DIR" == /mnt/* ]] && echo "  note:    running from a Windows mount; expect slow file I/O"

say "Prerequisites"
for binary in apt-get dpkg dpkg-query apt-cache python3; do
  if command -v "$binary" >/dev/null; then ok "$binary present"; else bad "$binary MISSING"; fi
done

if [[ ! -d "$VENV" ]]; then
  say "Creating virtualenv at $VENV"
  python3 -m venv "$VENV" || { echo "venv creation failed -- apt install python3-venv"; exit 1; }
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

say "Installing loadout from source"
# Git refuses to introspect a repo owned by another uid, which is every repo on
# a /mnt/c mount. Harmless here and unrelated to the code under test.
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true
pip install -q --upgrade pip
if pip install -q -e "$REPO_DIR[dev,tui]"; then ok "editable install"; else bad "editable install"; exit 1; fi

say "Unit suite on Linux"
if (cd "$REPO_DIR" && pytest -q 2>&1 | tail -3); then ok "pytest"; else bad "pytest"; fi

say "CLI reachable"
check "loadout --version"            "loadout --version"
check "catalog info"                 "loadout catalog info --json"
check "doctor exits 0 or 2"          "loadout doctor --json; [[ \$? -le 2 ]]"
check "providers detects apt"        "loadout providers --json | grep -q '\"provider\": \"apt\"'"
check "apt reports available"        "loadout providers --json | python3 -c \"import json,sys; d=json.load(sys.stdin); sys.exit(0 if any(p['provider']=='apt' and p['available']=='yes' for p in d['providers']) else 1)\""

say "Real dpkg / apt-cache integration"
python3 - <<'PY'
from loadout.providers.apt import AptProvider, dpkg_binaries
from loadout.catalog.seed_apt import discover_meta_membership

p = AptProvider()
installed = p.list_installed()
print(f"  dpkg-query reports {len(installed)} installed packages")
assert len(installed) > 20, "dpkg-query returned implausibly few packages"
print("  PASS bulk installed query")

# The fix for "run the package name, not the binary".
for pkg in ("dpkg", "coreutils", "grep"):
    if pkg in installed:
        found = dpkg_binaries(pkg)
        # The package-named binary must be promoted to first, not left wherever
        # dpkg's alphabetical listing happened to put it.
        # Only assert promotion when the package actually ships a binary of
        # that name; coreutils legitimately ships none called "coreutils".
        primary_ok = found[0] == pkg if pkg in found else True
        status = "PASS" if found and primary_ok else "FAIL"
        print(f"  {status} dpkg_binaries({pkg}) -> {found[:6]}")

ver = p.installed_version(type("T", (), {"binaries": ()})(), type("M", (), {"spec": {"package": "coreutils"}})())
print(f"  coreutils version via dpkg-query: {ver!r}")

size = p.package_size("nmap")
print(f"  apt-cache Installed-Size for nmap: {size} bytes")

membership = discover_meta_membership()
print(f"  kali-tools-* meta-package membership: {len(membership)} packages")
if membership:
    sample = list(membership.items())[:5]
    for name, (cat, tags) in sample:
        print(f"      {name:<24} -> {cat} {list(tags)}")
    print("  PASS meta-package discovery")
else:
    print("  SKIP meta-package discovery (not a Kali host?)")
PY

say "Catalog enrichment from APT metadata"
echo "  (this is the step that fills in descriptions and categories)"
before=$(loadout catalog info --json | python3 -c "import json,sys; print(json.load(sys.stdin)['tools'])")
echo "  before: $before tools"
if loadout catalog update --json > /tmp/catalog-update.json 2>&1; then
  python3 - <<'PY'
import json
data = json.load(open("/tmp/catalog-update.json"))
print(f"  tools: {data['tools']}  new: {data['added']}  "
      f"descriptions filled: {data['descriptions_filled']}  pruned: {data['state_rows_pruned']}")
PY
  ok "catalog update"
else
  bad "catalog update"; tail -5 /tmp/catalog-update.json
fi

python3 - <<'PY'
from loadout.catalog import open_catalog
with open_catalog() as store:
    tools = list(store.iter_all())
    described = sum(1 for t in tools if t.summary)
    categorised = sum(1 for t in tools if t.category != "other")
    with_binaries = sum(1 for t in tools if t.binaries)
    sized = sum(1 for t in tools if t.size)
    total = len(tools)
    print(f"  described:   {described}/{total} ({described/total:.0%})   was 4% in 0.3")
    print(f"  categorised: {categorised}/{total} ({categorised/total:.0%})  was 14% in 0.3")
    print(f"  binaries:    {with_binaries}/{total} ({with_binaries/total:.0%})")
    print(f"  sized:       {sized}/{total} ({sized/total:.0%})      was 0% in 0.3")
    print("  top categories:", store.facet_values("category")[:8])
PY

say "Search and resolution against the enriched catalog"
check "search returns hits"    "loadout search scanner --json | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin) else 1)'"
check "tag filter works"       "loadout list --tag kali --json | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin) else 1)'"
check "installed is truthful"  "loadout list --installed --json | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin) else 1)'"
check "plan resolves to apt"   "loadout install nmap --dry-run --json | grep -q '\"provider\": \"apt\"'"

say "APT status-fd progress (real apt output)"
python3 - <<'PY'
import os, subprocess, sys
from loadout.providers.apt import AptProvider
r, w = os.pipe()
proc = subprocess.run(
    ["apt-get", "install", "--simulate", "-o", f"APT::Status-Fd={w}", "--", "nmap"],
    pass_fds=(w,), capture_output=True, text=True,
)
os.close(w)
lines = os.fdopen(r).read().splitlines()
parsed = [AptProvider.parse_status_line(line) for line in lines]
good = [p for p in parsed if p]
print(f"  apt emitted {len(lines)} status records, {len(good)} parsed")
if good:
    for percent, message in good[:4]:
        print(f"      {percent:5.1f}%  {message[:50]}")
    print("  PASS status-fd parsing against real apt")
else:
    print("  SKIP no status records (simulate mode may not emit them)")
PY

if [[ $DO_INSTALL -eq 1 ]]; then
  say "End-to-end install / remove"
  TARGET="${LOADOUT_TEST_PKG:-hashid}"
  echo "  target: $TARGET"
  loadout install "$TARGET" --dry-run
  if loadout install "$TARGET" --yes; then ok "install $TARGET"; else bad "install $TARGET"; fi
  check "shows as installed"   "loadout list --installed --json | grep -q '\"id\": \"$TARGET\"'"
  check "provenance recorded"  "loadout show $TARGET --json | python3 -c \"import json,sys; d=json.load(sys.stdin); sys.exit(0 if d['installed'] and d['installed_via'] else 1)\""
  loadout report --since 1h --format text | head -12
  if loadout remove "$TARGET" --yes; then ok "remove $TARGET"; else bad "remove $TARGET"; fi
else
  meh "end-to-end install (pass --install to enable)"
fi

say "Result"
printf '  %d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[[ $fail -eq 0 ]] || exit 1

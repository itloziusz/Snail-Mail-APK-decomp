#!/usr/bin/env bash
# Download and verify the pinned analysis tools listed in tools.lock.json.
# Usage: tools/bootstrap/bootstrap_tools.sh [TOOLS_DIR]   (default /opt/re-tools)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-/opt/re-tools}"
mkdir -p "$DEST"
python3 - "$HERE/tools.lock.json" "$DEST" <<'PY'
import hashlib, json, os, subprocess, sys, zipfile
lock, dest = sys.argv[1], sys.argv[2]
for t in json.load(open(lock))["tools"]:
    path = os.path.join(dest, t["file"])
    if not os.path.exists(path):
        subprocess.check_call(["curl", "-fsSL", "-o", path, t["url"]])
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if h != t["sha256"]:
        sys.exit(f"SHA-256 mismatch for {path}: {h} != {t['sha256']}")
    print(f"ok  {t['name']} {t['version']}  {h}")
    if path.endswith(".zip"):
        out = os.path.join(dest, t["file"][:-4]) if t["name"] == "jadx" else dest
        marker = os.path.join(dest, f".{t['name']}-{t['version']}.unpacked")
        if not os.path.exists(marker):
            zipfile.ZipFile(path).extractall(out)
            open(marker, "w").close()
PY
chmod +x "$DEST"/jadx-1.5.3/bin/jadx 2>/dev/null || true
find "$DEST"/ghidra_11.4.2_PUBLIC -name '*.sh' -o -name 'analyzeHeadless' -o -name 'launch.sh' 2>/dev/null | xargs -r chmod +x
if command -v apt-get >/dev/null; then
  echo "apt packages (install manually if missing):"
  python3 -c "import json;print(' '.join(json.load(open('$HERE/tools.lock.json'))['apt']))"
fi

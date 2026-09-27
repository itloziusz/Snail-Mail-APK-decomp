#!/usr/bin/env python3
"""Fetch arm64 libpng / libjpeg-turbo / zlib (Ubuntu 24.04 "noble" ports) into a
local sysroot, WITHOUT changing the system's apt configuration, so the host
runner can be cross-compiled for AArch64 and run under qemu-aarch64.

Usage: tools/aot/fetch_arm64_host_deps.py work/arm64-deps
Writes <dir>/sysroot (extracted .debs) and <dir>/manifest.json (URLs + SHA-256).
Analysis/test tooling only.
"""
import gzip
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

MIRROR = "http://ports.ubuntu.com/ubuntu-ports"
SUITES = ["noble-updates", "noble-security", "noble"]
PACKAGES = ["libpng16-16t64", "libpng-dev", "libjpeg-turbo8", "libjpeg-turbo8-dev", "libjpeg8", "libjpeg8-dev",
            "libjpeg-dev", "zlib1g", "zlib1g-dev"]


def index(suite):
    url = f"{MIRROR}/dists/{suite}/main/binary-arm64/Packages.gz"
    data = gzip.decompress(urllib.request.urlopen(url, timeout=120).read()).decode("utf-8", "replace")
    pk = {}
    for stanza in data.split("\n\n"):
        f = {}
        for line in stanza.splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                f[k] = v.strip()
        if "Package" in f:
            pk.setdefault(f["Package"], f)
    return pk


def main(out):
    os.makedirs(os.path.join(out, "debs"), exist_ok=True)
    sysroot = os.path.join(out, "sysroot")
    os.makedirs(sysroot, exist_ok=True)
    idx = [index(s) for s in SUITES]
    manifest = []
    for p in PACKAGES:
        rec = next((i[p] for i in idx if p in i), None)
        if rec is None:
            sys.exit(f"package {p} not found")
        url = f"{MIRROR}/{rec['Filename']}"
        dest = os.path.join(out, "debs", os.path.basename(rec["Filename"]))
        if not os.path.exists(dest):
            urllib.request.urlretrieve(url, dest)
        h = hashlib.sha256(open(dest, "rb").read()).hexdigest()
        if rec.get("SHA256") and rec["SHA256"] != h:
            sys.exit(f"hash mismatch for {p}")
        subprocess.check_call(["dpkg-deb", "-x", dest, sysroot])
        manifest.append({"package": p, "version": rec["Version"], "url": url, "sha256": h})
        print(f"ok {p} {rec['Version']}")
    # absolute symlinks inside the extracted tree -> relative to sysroot
    for root, _, files in os.walk(sysroot):
        for fn in files:
            path = os.path.join(root, fn)
            if os.path.islink(path) and os.readlink(path).startswith("/"):
                target = sysroot + os.readlink(path)
                os.unlink(path)
                os.symlink(os.path.relpath(target, root), path)
    json.dump(manifest, open(os.path.join(out, "manifest.json"), "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "work/arm64-deps")

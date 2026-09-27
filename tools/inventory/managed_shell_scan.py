#!/usr/bin/env python3
"""Deterministic scan of the managed (DEX) side of the Snail Mail APK.

Produces a JSON summary used by docs/APK_AUDIT.md and docs/JNI_MAP.json:

* DEX header identity and counts (parsed directly from classes.dex bytes).
* Class counts per package and per component group (game shell, OpenFeint,
  Jackson, commons-codec, google-api-client escape).
* Every `native` method (from `dexdump` access flags, cross-checked against
  the apktool smali `.method ... native` declarations) and every smali
  invoke site of each native method.
* References to code-loading / reflection / process APIs anywhere in the DEX
  (System.loadLibrary, System.load, Runtime.load*, DexClassLoader,
  PathClassLoader, Class.forName, Class.getMethod, Method.invoke,
  Runtime.exec, ...), with smali file:line.
* A nested-payload check of every APK entry that is not an Ogg asset:
  trailing bytes after PNG IEND / JPEG EOI, and embedded ZIP/ELF/DEX magics.

Inputs are read-only.  Usage (from the repo root):

    tools/inventory/managed_shell_scan.py \
        --apk original/com.sandlotgames.snailmail-1.00.apk \
        --dex work/apk_unzip/classes.dex \
        --smali work/apktool/smali \
        > analysis/dex/managed_shell_scan.json

Requires `dexdump` on PATH.  Output is sorted and contains no timestamps.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict

GROUPS = [
    ("com/sandlotgames/snailmail", "game_shell"),
    ("com/openfeint", "openfeint_sdk"),
    ("org/codehaus/jackson", "jackson_json"),
    ("org/apache/commons/codec", "apache_commons_codec"),
    ("com/google/api/client/escape", "google_api_client_escape"),
]

# API references that can load code, reflect, or spawn processes.
SENSITIVE_PATTERNS = [
    r"Ljava/lang/System;->loadLibrary\(",
    r"Ljava/lang/System;->load\(",
    r"Ljava/lang/Runtime;->loadLibrary\(",
    r"Ljava/lang/Runtime;->load\(",
    r"Ljava/lang/Runtime;->exec\(",
    r"Ldalvik/system/DexClassLoader;",
    r"Ldalvik/system/PathClassLoader;",
    r"Ldalvik/system/DexFile;",
    r"Ljava/lang/ClassLoader;->loadClass\(",
    r"Ljava/lang/Class;->forName\(",
    r"Ljava/lang/Class;->newInstance\(",
    r"Ljava/lang/Class;->getMethod\(",
    r"Ljava/lang/Class;->getDeclaredMethod\(",
    r"Ljava/lang/reflect/Method;->invoke\(",
    r"Ljava/lang/reflect/Constructor;->newInstance\(",
    r"Ljava/lang/ProcessBuilder;",
]


def group_of(pkg: str) -> str:
    for prefix, name in GROUPS:
        if pkg == prefix or pkg.startswith(prefix + "/"):
            return name
    return "unclassified"


def dex_header(data: bytes) -> dict:
    names = ["string_ids", "type_ids", "proto_ids", "field_ids", "method_ids", "class_defs"]
    out = {"magic": data[:8].decode("latin-1").replace("\n", "\\n").replace("\x00", "\\0"),
           "file_size": struct.unpack_from("<I", data, 32)[0]}
    for i, n in enumerate(names):
        out[n + "_size"] = struct.unpack_from("<I", data, 56 + 8 * i)[0]
    return out


def parse_dexdump(dex_path: str):
    """Return (classes, methods) from `dexdump` (no -d)."""
    txt = subprocess.run(["dexdump", dex_path], check=True, capture_output=True,
                         text=True).stdout
    classes = []
    methods = []
    cls = None
    kind = None
    cur = None
    for line in txt.splitlines():
        m = re.match(r"\s*Class descriptor\s*: '(L[^']+;)'", line)
        if m:
            cls = m.group(1)
            classes.append(cls)
            kind = None
            continue
        if re.match(r"\s*Direct methods\s*-", line):
            kind = "direct"
            continue
        if re.match(r"\s*Virtual methods\s*-", line):
            kind = "virtual"
            continue
        if re.match(r"\s*(Static|Instance) fields\s*-", line):
            kind = None
            continue
        if kind is None:
            continue
        m = re.match(r"\s*name\s*: '([^']+)'", line)
        if m:
            cur = {"class": cls, "kind": kind, "name": m.group(1)}
            continue
        m = re.match(r"\s*type\s*: '([^']+)'", line)
        if m and cur is not None:
            cur["descriptor"] = m.group(1)
            continue
        m = re.match(r"\s*access\s*: 0x([0-9a-f]+) \(([^)]*)\)", line)
        if m and cur is not None:
            cur["access_flags"] = "0x%04x" % int(m.group(1), 16)
            cur["access"] = m.group(2)
            methods.append(cur)
            cur = None
    return classes, methods


def smali_index(smali_root: str):
    """Yield (relpath, lineno, line) for every smali line, sorted by path."""
    files = []
    for dp, _dn, fn in os.walk(smali_root):
        for f in fn:
            if f.endswith(".smali"):
                files.append(os.path.join(dp, f))
    for path in sorted(files):
        rel = os.path.relpath(path)
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                yield rel, i, line.rstrip("\n")


def png_check(data: bytes) -> dict:
    res = {"valid_signature": data[:8] == b"\x89PNG\r\n\x1a\n"}
    if not res["valid_signature"]:
        return res
    off = 8
    chunks = []
    while off + 8 <= len(data):
        ln, typ = struct.unpack_from(">I4s", data, off)
        chunks.append(typ.decode("latin-1"))
        off += 12 + ln
        if typ == b"IEND":
            break
    res["chunk_types"] = sorted(set(chunks))
    res["ends_with_iend"] = bool(chunks) and chunks[-1] == "IEND"
    res["trailing_bytes_after_iend"] = max(0, len(data) - off)
    if data[16:24] and len(data) >= 24:
        w, h = struct.unpack_from(">II", data, 16)
        res["width"], res["height"] = w, h
    return res


def jpeg_check(data: bytes) -> dict:
    last = data.rfind(b"\xff\xd9")
    return {"valid_signature": data[:3] == b"\xff\xd8\xff",
            "trailing_bytes_after_last_eoi": (len(data) - (last + 2)) if last >= 0 else None}


def embedded_magics(data: bytes, skip_offset0: bool = True) -> list:
    hits = []
    for name, magic in (("ZIP local header", b"PK\x03\x04"), ("ELF", b"\x7fELF"),
                        ("DEX", b"dex\n0"), ("Ogg", b"OggS")):
        start = 1 if skip_offset0 else 0
        i = data.find(magic, start)
        while i >= 0:
            hits.append({"magic": name, "offset": i})
            i = data.find(magic, i + 1)
    return sorted(hits, key=lambda h: (h["offset"], h["magic"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk", required=True)
    ap.add_argument("--dex", required=True)
    ap.add_argument("--smali", required=True)
    a = ap.parse_args()

    dex = open(a.dex, "rb").read()
    out = {"schema": "snailmail.managed_shell_scan/1",
           "dex": {"path": a.dex, "sha256": hashlib.sha256(dex).hexdigest(),
                   "header": dex_header(dex)}}

    classes, methods = parse_dexdump(a.dex)
    per_pkg = Counter()
    per_group = Counter()
    for c in classes:
        pkg = c[1:-1].rsplit("/", 1)[0]
        per_pkg[pkg] += 1
        per_group[group_of(pkg)] += 1
    out["class_count"] = len(classes)
    out["classes_per_package"] = dict(sorted(per_pkg.items()))
    out["classes_per_group"] = dict(sorted(per_group.items()))
    out["game_shell_classes"] = sorted(c for c in classes
                                       if group_of(c[1:-1].rsplit("/", 1)[0]) == "game_shell")

    natives = [m for m in methods if "NATIVE" in m["access"]]
    out["game_shell_methods"] = sorted(
        ({"class": m["class"], "name": m["name"], "descriptor": m["descriptor"],
          "access": m["access"], "kind": m["kind"]}
         for m in methods if group_of(m["class"][1:-1].rsplit("/", 1)[0]) == "game_shell"),
        key=lambda x: (x["class"], x["name"], x["descriptor"]))
    # Smali cross-check: declaration line + invoke sites.
    decl = {}
    invokes = defaultdict(list)
    sens = []
    sens_re = [re.compile(p) for p in SENSITIVE_PATTERNS]
    native_keys = {(m["class"], m["name"], m["descriptor"]) for m in natives}
    ref_re = re.compile(r"(L[^;\s]+;)->([^(\s]+)(\([^)]*\)\S+)")
    cur_class = None
    for rel, ln, line in smali_index(a.smali):
        s = line.strip()
        if s.startswith(".class"):
            cur_class = s.split()[-1]
        if s.startswith(".method") and " native " in (" " + s + " "):
            m = re.match(r"\.method\s+(.*?)\s*([^\s(]+)(\([^)]*\)\S+)$", s)
            if m:
                decl[(cur_class, m.group(2), m.group(3))] = {"file": rel, "line": ln,
                                                             "modifiers": m.group(1)}
        if s.startswith("invoke-"):
            m = ref_re.search(s)
            if m and (m.group(1), m.group(2), m.group(3)) in native_keys:
                invokes[(m.group(1), m.group(2), m.group(3))].append(
                    {"file": rel, "line": ln, "insn": s.split()[0]})
        for rx in sens_re:
            if rx.search(s):
                sens.append({"file": rel, "line": ln, "pattern": rx.pattern, "text": s})

    nat_out = []
    for m in sorted(natives, key=lambda x: (x["class"], x["name"], x["descriptor"])):
        key = (m["class"], m["name"], m["descriptor"])
        nat_out.append({
            "class": m["class"], "name": m["name"], "descriptor": m["descriptor"],
            "static": "STATIC" in m["access"], "access_flags": m["access_flags"],
            "access": m["access"], "dexdump_kind": m["kind"],
            "smali_declaration": decl.get(key),
            "invoke_sites": sorted(invokes.get(key, []), key=lambda x: (x["file"], x["line"])),
        })
    out["native_methods"] = nat_out
    out["native_method_count"] = len(nat_out)
    out["smali_native_declarations_without_dexdump_match"] = sorted(
        "%s->%s%s" % k for k in decl if k not in native_keys)
    out["sensitive_api_references"] = sens

    # APK nested-payload check for every non-Ogg entry.
    apk_checks = []
    with zipfile.ZipFile(a.apk) as z:
        for info in z.infolist():
            data = z.read(info)
            name = info.filename
            if data[:4] == b"OggS":
                continue
            rec = {"path": name, "size": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()}
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                rec["png"] = png_check(data)
            elif data[:3] == b"\xff\xd8\xff":
                rec["jpeg"] = jpeg_check(data)
            if name.endswith((".htm", ".lbi", ".MF", ".SF")):
                bad = sum(1 for c in data if not (32 <= c < 127 or c in (9, 10, 13)))
                rec["non_printable_ascii_bytes"] = bad
            # Only report embedded magics for entries that are not themselves
            # containers/executables (the DEX/ELF/asm.mp3 are analysed elsewhere).
            if not name.startswith(("lib/", "assets/")) and name != "classes.dex":
                rec["embedded_magics"] = embedded_magics(data)
            apk_checks.append(rec)
    out["apk_non_ogg_entry_checks"] = sorted(apk_checks, key=lambda r: r["path"])
    json.dump(out, sys.stdout, indent=1, sort_keys=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

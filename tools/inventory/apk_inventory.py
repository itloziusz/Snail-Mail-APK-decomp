#!/usr/bin/env python3
"""Deterministic inventory of every entry in the original APK.

Records, per ZIP entry: path, stored/uncompressed size, compression method,
CRC-32, ZIP timestamp, SHA-256 of the uncompressed bytes, and a content-based
type (magic bytes), deliberately independent of the filename extension so that
disguised payloads (e.g. assets/asm.mp3) are flagged.

Usage:
    tools/inventory/apk_inventory.py original/com.sandlotgames.snailmail-1.00.apk \
        > analysis/apk/inventory.json
"""
import hashlib
import json
import struct
import sys
import zipfile

METHODS = {0: "stored", 8: "deflate"}


def sniff(data: bytes) -> str:
    """Classify bytes by magic numbers only."""
    if data[:4] == b"\x7fELF":
        cls = {1: "ELF32", 2: "ELF64"}.get(data[4], "ELF?")
        mach = struct.unpack_from("<H", data, 18)[0] if len(data) > 20 else -1
        machine = {40: "ARM", 183: "AArch64", 3: "x86", 62: "x86-64"}.get(mach, f"e_machine={mach}")
        return f"{cls} {machine}"
    if data[:4] == b"dex\n":
        return "DEX " + data[4:7].decode("ascii", "replace")
    if data[:4] == b"OggS":
        return "Ogg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:4] == b"PK\x03\x04":
        return "ZIP"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "MPEG audio (probable)"
    if data[:4] == b"\x03\x00\x08\x00":
        return "Android binary XML"
    if data[:4] == b"\x02\x00\x0c\x00":
        return "Android resource table (ARSC)"
    if data[:1] == b"<":
        return "text/markup"
    if data[:2] == b"\x30\x82":
        return "DER (PKCS#7/X.509)"
    if all(32 <= c < 127 or c in (9, 10, 13) for c in data[:256]):
        return "text"
    return "unknown-binary"


def main(apk_path: str) -> None:
    raw = open(apk_path, "rb").read()
    out = {
        "schema": "snailmail.apk_inventory/1",
        "apk": {
            "path": apk_path,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "entries": [],
    }
    with zipfile.ZipFile(apk_path) as z:
        for info in z.infolist():
            data = z.read(info)
            ext = info.filename.rsplit(".", 1)[-1].lower() if "." in info.filename else ""
            kind = sniff(data)
            ext_expect = {"ogg": "Ogg", "png": "PNG", "jpg": "JPEG", "so": "ELF", "dex": "DEX", "mp3": "MPEG"}
            mismatch = ext in ext_expect and not kind.startswith(ext_expect[ext])
            out["entries"].append({
                "path": info.filename,
                "compress": METHODS.get(info.compress_type, str(info.compress_type)),
                "stored_size": info.compress_size,
                "size": info.file_size,
                "crc32": f"{info.CRC:08x}",
                "zip_time": "%04d-%02d-%02dT%02d:%02d:%02d" % info.date_time,
                "local_header_offset": info.header_offset,
                "sha256": hashlib.sha256(data).hexdigest(),
                "content_type": kind,
                "extension_mismatch": mismatch,
            })
    out["summary"] = {
        "entry_count": len(out["entries"]),
        "by_content_type": {},
        "extension_mismatches": [e["path"] for e in out["entries"] if e["extension_mismatch"]],
    }
    for e in out["entries"]:
        k = e["content_type"]
        out["summary"]["by_content_type"][k] = out["summary"]["by_content_type"].get(k, 0) + 1
    json.dump(out, sys.stdout, indent=1, sort_keys=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])

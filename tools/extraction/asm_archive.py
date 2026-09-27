#!/usr/bin/env python3
"""Deterministic extractor for Snail Mail's assets/asm.mp3 archive.

Analysis-only tool. The format is the one recovered from the consuming code in
libsnailmail.so v7a (sha256 e43bc913...a466); see docs/ASSET_FORMATS.md for
the byte-level spec and analysis/evidence/assets.jsonl for evidence IDs.

    0x00         u32 count                          (JNIDatInit v7a:0x153b0-0x153b8)
    0x08         u32 dir_size (== record 0 data_offset; read from a 244-byte
                 probe at sp+0x14, v7a:0x15354-0x1536c)
    0x04+24*i    record i: name_offset, data_offset, decoded_size,
                 stored_size, codec, dims(u16 w | u16 h << 16)
    codec        0 raw / 1 zip (JAVAUnZip) / 2 jpeg (JAVAUnJpg) / 3 png (JAVAUnPng)
                 (PfmLoadFileDat switch, v7a:0x14cb4-0x14ccc)

Outputs
  * a JSON manifest (default analysis/assets/asm_manifest.json): metadata only,
    safe to commit;
  * stored bytes (and zip-decoded bytes) under analysis/assets/extracted/
    (gitignored; proprietary content).

Everything is derived from the bytes and the recovered format; nothing is
tuned to expected counts. Unknown values are kept and labelled "unknown".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import zlib
from pathlib import Path

TOOL_VERSION = 1
REPO = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = REPO / "work/apk_unzip/assets/asm.mp3"
DEFAULT_MANIFEST = REPO / "analysis/assets/asm_manifest.json"
DEFAULT_OUT = REPO / "analysis/assets/extracted"
EXPECTED_SHA256 = "59740ec3a2cd1f7e9ff250e3c6128ffff922193925e3d0316db22a4118861b6a"
REFERENCE_BINARY_SHA256 = "e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466"

RECORD_BASE = 4
RECORD_SIZE = 24
DIR_SIZE_OFFSET = 8
MIN_HEADER = 12
U32 = 0xFFFFFFFF
CODEC_NAMES = {0: "raw", 1: "zip", 2: "jpeg", 3: "png"}
CODEC_JAVA = {1: "JAVAUnZip", 2: "JAVAUnJpg", 3: "JAVAUnPng"}
FIELD_NAMES = ("name_offset", "data_offset", "decoded_size", "stored_size", "codec", "dims")


class ArchiveError(Exception):
    """Structural error; carries a stable code mirroring sm_asm_status."""

    def __init__(self, code: str, msg: str, index: int | None = None):
        super().__init__(f"{code}: {msg}" + (f" (record {index})" if index is not None else ""))
        self.code = code
        self.index = index


# ---------------------------------------------------------------------------
# Original semantics (cRHash, v7a:0x7d114 / 0x7d1b8)
# ---------------------------------------------------------------------------

def rhash_calc(name: bytes) -> int:
    """cRHash::Calc: sum(byte | 0x20) & 0xff, bytes unsigned."""
    return sum(b | 0x20 for b in name) & 0xFF


def _fold(c: int) -> int:
    return (c - 0x20) & 0xFF if ((c - 0x61) & 0xFF) <= 0x19 else c


def rhash_name_equal(a: bytes, b: bytes) -> bool:
    """cRHash::Search comparison: same length, equal after folding a-z only."""
    return len(a) == len(b) and all(_fold(x) == _fold(y) for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Directory parsing (mirrors reconstructed/assets/src/asm_archive.c)
# ---------------------------------------------------------------------------

def u32(buf: bytes, off: int) -> int:
    if off < 0 or off + 4 > len(buf):
        raise ArchiveError("TRUNCATED", f"u32 read at {off:#x} outside {len(buf)} bytes")
    return struct.unpack_from("<I", buf, off)[0]


def parse_directory(buf: bytes) -> dict:
    if len(buf) < MIN_HEADER:
        raise ArchiveError("TRUNCATED_HEADER", f"{len(buf)} bytes < {MIN_HEADER}")
    dir_size = u32(buf, DIR_SIZE_OFFSET)
    if dir_size < 4 or dir_size > len(buf):
        raise ArchiveError("DIR_SIZE_RANGE", f"dir_size {dir_size:#x} vs buffer {len(buf):#x}")
    count = u32(buf, 0)
    if count > 0x7FFFFFFF:
        raise ArchiveError("NEGATIVE_COUNT", f"count {count:#x} is negative as int32")
    table_end = RECORD_BASE + RECORD_SIZE * count
    if table_end > dir_size:
        raise ArchiveError("COUNT_OVERFLOW", f"table end {table_end:#x} > dir_size {dir_size:#x}")
    records = []
    for i in range(count):
        base = RECORD_BASE + RECORD_SIZE * i
        raw = struct.unpack_from("<6I", buf, base)
        name_off = raw[0]
        if name_off < table_end or name_off >= dir_size:
            raise ArchiveError("NAME_OFFSET_RANGE", f"name_offset {name_off:#x}", i)
        nul = buf.find(b"\0", name_off, dir_size)
        if nul < 0:
            raise ArchiveError("NAME_UNTERMINATED", f"no NUL in [{name_off:#x},{dir_size:#x})", i)
        records.append({"index": i, "raw": raw, "record_offset": base, "name": buf[name_off:nul],
                        "name_end": nul + 1})
    return {"count": count, "dir_size": dir_size, "table_end": table_end, "records": records}


def read_span(raw: tuple) -> tuple[int, int] | None:
    """Bytes PfmLoadFileDat reads (v7a:0x14c74): codec 0 reads decoded_size,
    codecs 1-3 read stored_size; other codecs read nothing."""
    codec = raw[4]
    if codec == 0:
        return raw[1], raw[2]
    if codec in (1, 2, 3):
        return raw[1], raw[3]
    return None


def build_lookup(records: list) -> list[int]:
    """Result of RShellDatFind(name_i) for each i under the original hash:
    Add appends in index order, Search returns the first equal name in the
    bucket chain, so the lowest index with an equal name wins."""
    buckets: dict[int, list[int]] = {}
    for r in records:
        buckets.setdefault(rhash_calc(r["name"]), []).append(r["index"])
    out = []
    for r in records:
        chain = buckets[rhash_calc(r["name"])]
        out.append(next(j for j in chain if rhash_name_equal(r["name"], records[j]["name"])))
    return out


# ---------------------------------------------------------------------------
# Content sniffing (magic/structure based, never name based)
# ---------------------------------------------------------------------------

def png_dims(b: bytes):
    if len(b) >= 24 and b[:8] == b"\x89PNG\r\n\x1a\n" and b[12:16] == b"IHDR":
        return struct.unpack(">II", b[16:24])
    return None


def jpeg_dims(b: bytes):
    if b[:3] != b"\xff\xd8\xff":
        return None
    i = 2
    while i + 4 <= len(b):
        if b[i] != 0xFF:
            return None
        m = b[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        if m == 0xD9:
            return None
        seglen = struct.unpack(">H", b[i + 2:i + 4])[0]
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 > len(b):
                return None
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return (w, h)
        i += 2 + seglen
    return None


def tga_info(b: bytes):
    """Uncompressed/RLE TGA header plausibility; footer is the only magic."""
    if len(b) < 18:
        return None
    idl, cmt, itype = b[0], b[1], b[2]
    w, h, bpp, desc = struct.unpack_from("<HHBB", b, 12)
    footer = len(b) >= 26 and b[-18:] == b"TRUEVISION-XFILE.\0"
    if itype not in (1, 2, 3, 9, 10, 11) or cmt not in (0, 1) or bpp not in (8, 15, 16, 24, 32) or not w or not h:
        return None
    pix = w * h * ((bpp + 7) // 8)
    if itype in (1, 2, 3) and len(b) < 18 + idl + pix:
        return None
    return {"image_type": itype, "width": w, "height": h, "bpp": bpp, "descriptor": desc,
            "id_length": idl, "colormap_type": cmt, "tga2_footer": footer,
            "bytes_after_pixels": len(b) - 18 - idl - pix if itype in (1, 2, 3) else None}


def is_text(b: bytes) -> bool:
    if not b:
        return False
    allowed = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D, 0x0C, 0x1A}
    return all(c in allowed for c in b)


def sniff(b: bytes) -> dict:
    if b[:4] == b"PK\x03\x04":
        return {"type": "zip"}
    d = png_dims(b)
    if d:
        return {"type": "png", "width": d[0], "height": d[1]}
    if b[:3] == b"\xff\xd8\xff":
        d = jpeg_dims(b)
        return {"type": "jpeg", **({"width": d[0], "height": d[1]} if d else {})}
    if b[:4] == b"OggS":
        return {"type": "ogg"}
    if b[:4] == b"xof " and b[8:12] in (b"txt ", b"bin ", b"tzip", b"bzip"):
        return {"type": "directx-x", "variant": b[4:12].decode("ascii", "replace")}
    t = tga_info(b)
    if t and t["tga2_footer"]:
        return {"type": "tga", **{k: t[k] for k in ("width", "height", "bpp", "descriptor", "image_type")}}
    body = b.rstrip(b"\0")
    if is_text(body):
        extra = {"trailing_nul_bytes": len(b) - len(body)} if len(body) != len(b) else {}
        s = body.lstrip()
        if s.startswith(b"<?xml") or s.startswith(b"<"):
            return {"type": "xml-like-text", **extra}
        return {"type": "text", **extra}
    if t:
        return {"type": "tga-heuristic", **{k: t[k] for k in ("width", "height", "bpp", "descriptor", "image_type")}}
    return {"type": "unknown-binary"}


# ---------------------------------------------------------------------------
# Zip (codec 1) decoding with java.util.zip.ZipInputStream first-entry semantics
# ---------------------------------------------------------------------------

def decode_zip_first_entry(b: bytes) -> dict:
    """JAVAUnZip: ZipInputStream.getNextEntry() then read() until -1; only the
    first local entry is used. Local header parsed explicitly (no central
    directory needed), deflate via raw zlib."""
    res: dict = {"ok": False}
    if len(b) < 30 or b[:4] != b"PK\x03\x04":
        res["error"] = "no local file header"
        return res
    (_sig, ver, flag, method, _mt, _md, crc, csize, usize, fnl, exl) = struct.unpack_from("<IHHHHHIIIHH", b, 0)
    hdr_end = 30 + fnl + exl
    if hdr_end > len(b):
        res["error"] = "local header exceeds payload"
        return res
    res.update({"version_needed": ver, "flags": flag, "method": method, "crc32_header": f"{crc:#010x}",
                "compressed_size_header": csize, "uncompressed_size_header": usize,
                "entry_name": b[30:30 + fnl].decode("latin-1")})
    body = b[hdr_end:]
    if method == 8:
        dobj = zlib.decompressobj(-15)
        try:
            out = dobj.decompress(body)
        except zlib.error as e:
            res["error"] = f"inflate: {e}"
            return res
        if not dobj.eof:
            res["error"] = "deflate stream not terminated"
            return res
        consumed = len(body) - len(dobj.unused_data)
        rest = dobj.unused_data
    elif method == 0 and not (flag & 8):
        if csize > len(body):
            res["error"] = "stored entry exceeds payload"
            return res
        out = body[:csize]
        consumed = csize
        rest = body[csize:]
    else:
        res["error"] = f"unsupported method {method} flags {flag:#x}"
        return res
    if flag & 8:
        # data descriptor follows the data (optional signature)
        dd = rest[4:16] if rest[:4] == b"PK\x07\x08" else rest[:12]
        rest = rest[16:] if rest[:4] == b"PK\x07\x08" else rest[12:]
        if len(dd) == 12:
            crc, csize, usize = struct.unpack("<III", dd)
    crc_actual = zlib.crc32(out) & U32
    res.update({"ok": True, "decoded": out, "compressed_consumed": consumed,
                "crc32_ok": crc_actual == crc, "size_matches_header": len(out) == usize,
                "compressed_size_matches_header": consumed == csize,
                "bytes_after_entry": len(rest),
                "after_entry_starts_with_central_directory": rest[:4] == b"PK\x01\x02",
                "has_end_of_central_directory": b"PK\x05\x06" in rest[-22 - 0xFFFF:] if rest else False})
    return res


# ---------------------------------------------------------------------------
# Output path sanitisation
# ---------------------------------------------------------------------------

_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_name(name: bytes) -> str:
    """Archive name -> safe relative POSIX path. Never absolute, never '..',
    only [A-Za-z0-9._-] per component; original name is kept in the manifest."""
    text = name.decode("latin-1")
    comps = []
    for part in re.split(r"[/\\]+", text):
        if part in ("", "."):
            continue
        if part == "..":
            part = "__"
        part = _SAFE.sub("_", part)
        if part.startswith("."):
            part = "_" + part[1:]
        if part.split(".")[0].upper() in _WIN_RESERVED:
            part = "_" + part
        part = part.rstrip(". ") or "_"
        comps.append(part[:120])
    return "/".join(comps) if comps else "_unnamed"


def unique_paths(records: list) -> list[str]:
    """Deterministic, case-insensitively unique output paths."""
    used: set[str] = set()
    out = []
    for r in records:
        p = sanitize_name(r["name"])
        if p.lower() in used:
            stem, dot, ext = p.rpartition(".")
            if not dot or "/" in ext:
                stem, ext = p, ""
            p = f"{stem}__dup{r['index']}" + (f".{ext}" if ext else "")
        n = 1
        while p.lower() in used:
            p = f"{p}_{n}"
            n += 1
        used.add(p.lower())
        out.append(p)
    return out


def safe_join(root: Path, rel: str) -> Path:
    root_r = root.resolve()
    p = (root_r / rel).resolve()
    if p != root_r and root_r not in p.parents:
        raise ArchiveError("PATH_TRAVERSAL", f"{rel!r} escapes {root}")
    return p


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def hx(v: int) -> str:
    return f"{v:#010x}"


def analyse(buf: bytes, archive_label: str) -> tuple[dict, dict]:
    """Returns (manifest, blobs) where blobs maps index -> (stored, decoded|None)."""
    d = parse_directory(buf)
    recs = d["records"]
    n = len(recs)
    lookup = build_lookup(recs)
    paths = unique_paths(recs)
    blobs: dict[int, tuple[bytes, bytes | None]] = {}
    entries = []
    anomalies: list[str] = []
    file_len = len(buf)
    for r, resolves, outp in zip(recs, lookup, paths):
        i = r["index"]
        raw = r["raw"]
        name_off, data_off, dec_size, st_size, codec, dims = raw
        w, h = dims & 0xFFFF, dims >> 16
        e_anom: list[str] = []
        try:
            name_str = r["name"].decode("ascii")
            name_ascii = all(0x20 <= c < 0x7F for c in r["name"])
        except UnicodeDecodeError:
            name_str = r["name"].decode("latin-1")
            name_ascii = False
        if not name_ascii:
            e_anom.append("name has non-printable/non-ASCII bytes")
        if b"\\" in r["name"]:
            e_anom.append("name contains backslash")
        span = read_span(raw)
        stored_end = data_off + st_size
        in_bounds = stored_end <= file_len and data_off >= d["dir_size"]
        if stored_end > U32:
            e_anom.append("data_offset + stored_size overflows 32 bits")
        if not in_bounds:
            e_anom.append("stored span outside archive data area")
        if span is None:
            e_anom.append(f"codec {codec} not handled by PfmLoadFileDat (loader does nothing)")
        elif span[0] + span[1] > file_len:
            e_anom.append("read span outside archive")
        stored = buf[data_off:stored_end] if in_bounds else b""
        entry = {
            "index": i,
            "name": name_str,
            "record_offset": hx(r["record_offset"]),
            "raw_fields": {k: hx(v) for k, v in zip(FIELD_NAMES, raw)},
            "interpreted": {
                "name_offset": name_off,
                "data_offset": data_off,
                "decoded_size": dec_size,
                "stored_size": st_size,
                "codec": CODEC_NAMES.get(codec, f"unknown({codec})"),
                "codec_consumer": CODEC_JAVA.get(codec, "fread" if codec == 0 else None),
                "dims_width": w,
                "dims_height": h,
                "read_span": list(span) if span else None,
            },
            "hash_bucket": rhash_calc(r["name"]),
            "lookup": {"resolves_to_index": resolves, "shadowed": resolves != i},
            "output_path": outp,
            "stored": {"sha256": sha256(stored) if in_bounds else None, "sniff": sniff(stored) if in_bounds else None},
        }
        if resolves != i:
            e_anom.append(f"unreachable by name: lookup resolves to record {resolves}")
        decoded = None
        dec_info: dict | None = None
        if in_bounds and codec == 0:
            decoded = buf[data_off:data_off + dec_size]
            if dec_size != st_size:
                e_anom.append(f"raw record decoded_size {dec_size} != stored_size {st_size} (loader reads decoded_size)")
            dec_info = {"method": "identity (fread of decoded_size bytes)", "size": len(decoded),
                        "sha256": sha256(decoded), "sniff": sniff(decoded)}
        elif in_bounds and codec == 1:
            z = decode_zip_first_entry(stored)
            if z["ok"]:
                decoded = z.pop("decoded")
                z.pop("ok")
                if not z["crc32_ok"]:
                    e_anom.append("zip CRC mismatch")
                if len(decoded) != dec_size:
                    e_anom.append(f"zip decoded {len(decoded)} bytes != decoded_size {dec_size}")
                dec_info = {"method": "zip first entry (ZipInputStream semantics)", "size": len(decoded),
                            "sha256": sha256(decoded), "matches_decoded_size": len(decoded) == dec_size,
                            "sniff": sniff(decoded), "zip": z}
                s = dec_info["sniff"]
                if s.get("type") in ("tga", "tga-heuristic") and dims and (s["width"], s["height"]) != (w, h):
                    e_anom.append("TGA header dims != record dims")
            else:
                e_anom.append(f"zip decode failed: {z.get('error')}")
                dec_info = {"method": "zip first entry", "error": z.get("error")}
        elif in_bounds and codec in (2, 3):
            s = entry["stored"]["sniff"]
            want = "jpeg" if codec == 2 else "png"
            if s.get("type") != want:
                e_anom.append(f"codec {codec} payload sniffs as {s.get('type')}")
            if (s.get("width"), s.get("height")) != (w, h):
                e_anom.append("image header dims != record dims")
            row_bytes = dec_size // h if h else None
            dec_info = {
                "method": f"not decoded: Android BitmapFactory ({CODEC_JAVA[codec]}) output is not reproduced here",
                "java_buffer_size": dec_size,
                "pixel_bytes_w_h_4": w * h * 4,
                "decoded_size_minus_w_h_4": dec_size - w * h * 4,
                "row_bytes_used_by_loader": row_bytes,
                "row_bytes_equals_w_4": row_bytes == w * 4 if h else None,
                "output_extent": 18 + row_bytes * h if h else None,
            }
            if h == 0:
                e_anom.append("image height 0: loader divides by zero (v7a:0x14584)")
            elif row_bytes != w * 4:
                e_anom.append(f"loader row size {row_bytes} != width*4 {w * 4}: rows would be sheared")
        entry["decoded"] = dec_info
        entry["classification"] = classify(codec, entry["stored"]["sniff"], dec_info)
        entry["anomalies"] = e_anom
        for a in e_anom:
            anomalies.append(f"record {i} ({name_str}): {a}")
        blobs[i] = (stored, decoded if codec == 1 else None)
        entries.append(entry)

    layout = layout_analysis(d, recs, file_len)
    for g in layout["gaps"]:
        anomalies.append(f"gap {g}")
    for o in layout["overlaps"]:
        anomalies.append(f"overlap {o}")

    def count_by(key):
        c: dict[str, int] = {}
        for e in entries:
            k = key(e)
            c[k] = c.get(k, 0) + 1
        return dict(sorted(c.items()))

    manifest = {
        "manifest_version": 1,
        "tool": "tools/extraction/asm_archive.py",
        "tool_version": TOOL_VERSION,
        "format_spec": "docs/ASSET_FORMATS.md",
        "reference_binary_sha256": REFERENCE_BINARY_SHA256,
        "archive": {"path": archive_label, "size": file_len, "sha256": sha256(buf),
                    "expected_sha256": EXPECTED_SHA256, "sha256_matches_expected": sha256(buf) == EXPECTED_SHA256},
        "header": {"count_raw": hx(u32(buf, 0)), "count": d["count"], "dir_size_field_offset": DIR_SIZE_OFFSET,
                   "dir_size": d["dir_size"], "record_table": [RECORD_BASE, d["table_end"]],
                   "record_size": RECORD_SIZE},
        "summary": {
            "records_observed": n,
            "by_codec": count_by(lambda e: e["interpreted"]["codec"]),
            "by_stored_sniff": count_by(lambda e: (e["stored"]["sniff"] or {}).get("type", "out-of-bounds")),
            "by_classification": count_by(lambda e: e["classification"]),
            "by_top_directory": count_by(lambda e: e["name"].split("/")[0] if "/" in e["name"] else "(root)"),
            "duplicates_case_insensitive": dup_groups(recs),
            "shadowed_records": [e["index"] for e in entries if e["lookup"]["shadowed"]],
            "hash_buckets_used": len({e["hash_bucket"] for e in entries}),
            "hash_max_chain": max(count_by(lambda e: str(e["hash_bucket"])).values()) if entries else 0,
            "anomalies": anomalies,
        },
        "layout": layout,
        "entries": entries,
    }
    return manifest, blobs


def classify(codec: int, st: dict | None, dec: dict | None) -> str:
    if st is None:
        return "invalid-span"
    if codec == 3:
        return "image/png"
    if codec == 2:
        return "image/jpeg"
    if codec == 1:
        t = ((dec or {}).get("sniff") or {}).get("type", "undecodable")
        return f"zip/{t}"
    if codec == 0:
        return f"raw/{st.get('type')}"
    return f"unknown-codec/{st.get('type')}"


def dup_groups(recs: list) -> list:
    groups: dict[bytes, list[int]] = {}
    for r in recs:
        groups.setdefault(bytes(_fold(c) for c in r["name"]), []).append(r["index"])
    return [{"indices": v, "names": [recs[i]["name"].decode("latin-1") for i in v]}
            for v in groups.values() if len(v) > 1]


def layout_analysis(d: dict, recs: list, file_len: int) -> dict:
    regions = [(0, 4, "count"), (4, d["table_end"], "record table")]
    if recs:
        name_lo = min(r["raw"][0] for r in recs)
        name_hi = max(r["name_end"] for r in recs)
        regions.append((name_lo, name_hi, "names"))
        packed = all(recs[k + 1]["raw"][0] == recs[k]["name_end"] for k in range(len(recs) - 1))
    else:
        name_lo = name_hi = d["table_end"]
        packed = True
    for r in recs:
        off, size = r["raw"][1], r["raw"][3]
        regions.append((off, off + size, f"data {r['index']}"))
    regions.sort(key=lambda t: (t[0], t[1]))
    gaps, overlaps = [], []
    pos = 0
    for lo, hi, what in regions:
        if lo > pos:
            gaps.append({"start": pos, "end": lo, "size": lo - pos, "before": what})
        elif lo < pos:
            overlaps.append({"start": lo, "end": min(pos, hi), "region": what})
        pos = max(pos, hi)
    data_order = [r["index"] for r in sorted(recs, key=lambda r: (r["raw"][1], r["index"]))]
    return {
        "file_size": file_len,
        "directory": {"start": 0, "end": d["dir_size"], "names_start": name_lo, "names_end": name_hi,
                      "slack_after_names": d["dir_size"] - name_hi,
                      "gap_between_table_and_names": name_lo - d["table_end"],
                      "names_packed_in_record_order": packed},
        "data_start": min((r["raw"][1] for r in recs), default=d["dir_size"]),
        "data_order_equals_record_order": data_order == list(range(len(recs))),
        "record0_data_offset_equals_dir_size": bool(recs) and recs[0]["raw"][1] == d["dir_size"],
        "end_of_last_region": pos,
        "trailing_bytes": max(0, file_len - pos),
        "regions_beyond_file": [what for lo, hi, what in regions if hi > file_len],
        "gaps": gaps,
        "overlaps": overlaps,
    }


def write_outputs(manifest: dict, blobs: dict, manifest_path: Path | None, out_dir: Path | None) -> None:
    if out_dir is not None:
        for e in manifest["entries"]:
            stored, decoded = blobs[e["index"]]
            if e["stored"]["sha256"] is None:
                continue
            p = safe_join(out_dir / "stored", e["output_path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(stored)
            if decoded is not None:
                q = safe_join(out_dir / "decoded", e["output_path"])
                q.parent.mkdir(parents=True, exist_ok=True)
                q.write_bytes(decoded)
    if manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(dump_manifest(manifest))


def dump_manifest(m: dict) -> str:
    """Deterministic JSON: header sections indented, one line per entry."""
    head = {k: v for k, v in m.items() if k != "entries"}
    text = json.dumps(head, indent=1, ensure_ascii=True)
    lines = [json.dumps(e, ensure_ascii=True, separators=(",", ":")) for e in m["entries"]]
    return text[:-2] + ',\n "entries": [\n' + ",\n".join(lines) + "\n ]\n}\n"


# ---------------------------------------------------------------------------
# Self test (used by CTest on the synthetic fixture)
# ---------------------------------------------------------------------------

def self_test(fixture: Path) -> int:
    fails = []

    def check(cond, what):
        if not cond:
            fails.append(what)

    buf = fixture.read_bytes()
    m, blobs = analyse(buf, fixture.name)
    s = m["summary"]
    check(s["records_observed"] == m["header"]["count"] == len(m["entries"]), "record count consistency")
    check(m["layout"]["gaps"] == [] and m["layout"]["overlaps"] == [], "fixture layout contiguous")
    check(m["layout"]["trailing_bytes"] == 0, "no trailing bytes")
    zips = [e for e in m["entries"] if e["interpreted"]["codec"] == "zip"]
    check(all(e["decoded"]["zip"]["crc32_ok"] and e["decoded"]["matches_decoded_size"] for e in zips), "zip decode")
    check(any((e["decoded"] or {}).get("sniff", {}).get("type") == "tga" for e in zips), "zip->tga sniff")
    check(s["shadowed_records"] == [4], f"shadowed records {s['shadowed_records']}")
    check(any("not handled by PfmLoadFileDat" in a for a in s["anomalies"]), "unknown codec flagged")
    check(any(e["stored"]["sniff"]["type"] == "png" for e in m["entries"]), "png sniff")
    check(any(e["stored"]["sniff"]["type"] == "jpeg" for e in m["entries"]), "jpeg sniff")
    paths = [e["output_path"] for e in m["entries"]]
    check(len({p.lower() for p in paths}) == len(paths), "unique output paths")
    # sanitiser on hostile names
    for bad in (b"../../etc/passwd", b"/abs/x", b"C:\\win\\x", b"a/./b/../c", b"CON.TXT", b"", b".hidden",
                b"x\x01y", b"..", b"a//b\\\\c", b"trail. "):
        p = sanitize_name(bad)
        check(not p.startswith("/") and ".." not in p.split("/") and p != "", f"sanitize {bad!r} -> {p!r}")
        check(re.fullmatch(r"[A-Za-z0-9._/-]+", p) is not None, f"charset {bad!r} -> {p!r}")
    with tempfile.TemporaryDirectory() as td:
        try:
            safe_join(Path(td), "../x")
            fails.append("safe_join accepted ../x")
        except ArchiveError:
            pass
        write_outputs(m, blobs, Path(td) / "m.json", Path(td) / "out")
        check((Path(td) / "out/stored/DATA/README.TXT").is_file(), "extraction wrote file")
        m2 = json.loads((Path(td) / "m.json").read_text())
        check(m2["summary"]["records_observed"] == s["records_observed"], "manifest round trip")
        a1 = (Path(td) / "m.json").read_bytes()
        write_outputs(analyse(buf, fixture.name)[0], blobs, Path(td) / "m2.json", None)
        check(a1 == (Path(td) / "m2.json").read_bytes(), "deterministic manifest")
    # malformed inputs
    cases = {
        "TRUNCATED_HEADER": buf[:11],
        "DIR_SIZE_RANGE": buf[:u32(buf, 8) - 1],
        "NEGATIVE_COUNT": struct.pack("<I", 0x80000000) + buf[4:],
        "COUNT_OVERFLOW": struct.pack("<I", 0x0AAAAAAB) + buf[4:],
        "NAME_OFFSET_RANGE": buf[:4] + struct.pack("<I", u32(buf, 8)) + buf[8:],
        "NAME_UNTERMINATED": buf[:u32(buf, 8) - 1] + b"Z" + buf[u32(buf, 8):],
    }
    for code, data in cases.items():
        try:
            parse_directory(data)
            fails.append(f"{code} not detected")
        except ArchiveError as e:
            check(e.code == code, f"{code}: got {e.code}")
    for f in fails:
        print("FAIL:", f, file=sys.stderr)
    print(f"self-test on {fixture}: {'FAILED' if fails else 'passed'} ({s['records_observed']} records)")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--no-extract", action="store_true", help="write the manifest only")
    ap.add_argument("--self-test", type=Path, metavar="FIXTURE", help="run checks on a synthetic fixture and exit")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test(a.self_test)
    buf = a.archive.read_bytes()
    try:
        label = str(a.archive.resolve().relative_to(REPO))
    except ValueError:
        label = a.archive.name
    try:
        manifest, blobs = analyse(buf, label)
    except ArchiveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    write_outputs(manifest, blobs, a.manifest, None if a.no_extract else a.out_dir)
    s = manifest["summary"]
    print(f"archive {label}: {manifest['archive']['size']} bytes sha256 {manifest['archive']['sha256']}"
          f" (expected: {manifest['archive']['sha256_matches_expected']})")
    print(f"records observed: {s['records_observed']}; directory {manifest['header']['dir_size']} bytes")
    print("by codec:", s["by_codec"])
    print("by classification:", s["by_classification"])
    print(f"layout: gaps={len(manifest['layout']['gaps'])} overlaps={len(manifest['layout']['overlaps'])} "
          f"trailing={manifest['layout']['trailing_bytes']}")
    print(f"shadowed records: {s['shadowed_records']}; anomalies: {len(s['anomalies'])}")
    for x in s["anomalies"][:20]:
        print("  -", x)
    print(f"manifest: {a.manifest}" + ("" if a.no_extract else f"; bytes under {a.out_dir}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

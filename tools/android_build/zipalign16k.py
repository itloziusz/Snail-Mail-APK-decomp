#!/usr/bin/env python3
"""Re-align an unsigned APK: stored `lib/**.so` entries to 16 KiB, other stored
entries to 4 bytes. Compressed entries are copied byte-for-byte.

Equivalent in intent to `zipalign -P 16 4` from build-tools >= 35, which is not
available on the analysis machine (the Debian zipalign 10.0.0 only knows 4 KiB
page alignment). Padding is written as zipalign's alignment extra field
(header ID 0xD935: u16 alignment + zero padding), which apksigner reads back to
preserve the alignment when it rewrites the APK during signing. The result is
always re-checked by tools/validation/platform/check_elf_alignment.py after
signing.

Usage: zipalign16k.py in.apk out.apk
"""
import struct
import sys
import zipfile

ALIGN_EXTRA_ID = 0xD935
LFH_SIG = 0x04034B50
CDH_SIG = 0x02014B50
EOCD_SIG = 0x06054B50


def alignment_for(info: zipfile.ZipInfo) -> int:
    if info.compress_type != zipfile.ZIP_STORED:
        return 1
    if info.filename.startswith("lib/") and info.filename.endswith(".so"):
        return 16384
    return 4


def main(src: str, dst: str) -> None:
    raw = open(src, "rb").read()
    zin = zipfile.ZipFile(src)
    out = bytearray()
    central = bytearray()
    for info in zin.infolist():
        off = info.header_offset
        (sig, ver, flags, method, mtime, mdate, crc, csize, usize, nlen, xlen) = struct.unpack_from(
            "<IHHHHHIIIHH", raw, off)
        if sig != LFH_SIG:
            sys.exit(f"bad local header for {info.filename}")
        if flags & 0x08:
            sys.exit(f"data descriptor not supported: {info.filename}")
        name = raw[off + 30: off + 30 + nlen]
        data_start = off + 30 + nlen + xlen
        data = raw[data_start: data_start + info.compress_size]
        align = alignment_for(info)
        local_off = len(out)
        extra = b""
        if align > 1:
            base = local_off + 30 + nlen
            # Minimal alignment field is 6 bytes (id, size, u16 alignment).
            pad = (-(base + 6)) % align
            extra = struct.pack("<HHH", ALIGN_EXTRA_ID, 2 + pad, align) + b"\0" * pad
        out += struct.pack("<IHHHHHIIIHH", LFH_SIG, ver, flags, method, mtime, mdate, crc,
                           info.compress_size, info.file_size, nlen, len(extra))
        out += name + extra
        assert align == 1 or len(out) % align == 0, info.filename
        out += data
        central += struct.pack("<IHHHHHHIIIHHHHHII", CDH_SIG, info.create_version | (info.create_system << 8),
                               ver, flags, method, mtime, mdate, crc, info.compress_size,
                               info.file_size, nlen, 0, 0, 0, 0, info.external_attr, local_off)
        central += name
    cd_off = len(out)
    out += central
    n = len(zin.infolist())
    out += struct.pack("<IHHHHIIH", EOCD_SIG, 0, 0, n, n, len(central), cd_off, 0)
    open(dst, "wb").write(out)
    # Self-check with the standard library reader.
    with zipfile.ZipFile(dst) as z:
        bad = z.testzip()
        if bad:
            sys.exit(f"CRC failure after realignment: {bad}")
    print(f"aligned {n} entries -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

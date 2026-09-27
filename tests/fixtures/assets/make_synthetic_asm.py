#!/usr/bin/env python3
"""Build tests/fixtures/assets/synthetic_small.asm from the recovered format.

Synthetic content only (no bytes from the game). The layout follows
docs/ASSET_FORMATS.md section 2:

    u32 count | count x 24-byte records | NUL-terminated names | data

with record 0's data_offset == directory size (the word the original reads at
archive offset 8). Deterministic: fixed zip timestamps, fixed payloads.

Usage: make_synthetic_asm.py [output_path]
"""
import io
import struct
import sys
import zipfile
import zlib
from pathlib import Path


def zip_single(name: str, data: bytes) -> bytes:
    """A one-entry deflate zip, like the archive's codec-1 payloads."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo(name, date_time=(2010, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0
        info.create_system = 0
        z.writestr(info, data)
    return buf.getvalue()


def png(width: int, height: int) -> bytes:
    """Minimal valid RGBA PNG (synthetic gradient)."""
    def chunk(t: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)
    rows = b"".join(
        b"\x00" + b"".join(bytes(((x * 60) & 0xFF, (y * 90) & 0xFF, 7, 255)) for x in range(width))
        for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def jpeg_stub(width: int, height: int) -> bytes:
    """JPEG marker skeleton (SOI, APP0, SOF0 with dimensions, EOI). Not decodable;
    enough for magic sniffing and dimension cross-checks."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def tga(width: int, height: int) -> bytes:
    hdr = bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0]) + struct.pack("<HHBB", width, height, 32, 8)
    return hdr + bytes(range(width * height * 4)) + b"\x00" * 8 + b"TRUEVISION-XFILE.\x00"


def build() -> bytes:
    text = b"// synthetic text\r\nLine 2\r\n"
    tga_bytes = tga(2, 2)
    recs = [
        # name, codec, decoded_size, dims(w,h), stored bytes
        ("DATA/README.TXT", 0, len(text), (0, 0), text),
        ("OBJECTS/T/TEX.TGA", 1, len(tga_bytes), (2, 2), zip_single("objects_t_tex.tga", tga_bytes)),
        ("SPRITES/P.PNG", 3, 4 * 2 * 4 + 0x13, (4, 2), png(4, 2)),
        ("BACKGROUNDS/J.JPG", 2, 8 * 8 * 4 + 0x13, (8, 8), jpeg_stub(8, 8)),
        ("data/readme.txt", 0, 3, (0, 0), b"dup"),          # case-insensitive duplicate of record 0
        ("X/ODD.BIN", 7, 4, (0, 0), b"\x00\x01\x02\x03"),   # codec outside 0..3
        ("DATA/SHORT.BIN", 0, 2, (0, 0), b"ABCDE"),         # raw: decoded_size < stored_size
    ]
    n = len(recs)
    table_end = 4 + 24 * n
    names = b"".join(r[0].encode("ascii") + b"\x00" for r in recs)
    dir_size = table_end + len(names)
    out = bytearray(struct.pack("<I", n))
    name_pos = table_end
    data_pos = dir_size
    for name, codec, dec, (w, h), stored in recs:
        out += struct.pack("<6I", name_pos, data_pos, dec, len(stored), codec, (h << 16) | w)
        name_pos += len(name) + 1
        data_pos += len(stored)
    out += names
    assert len(out) == dir_size
    for r in recs:
        out += r[4]
    return bytes(out)


def main() -> int:
    dst = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("synthetic_small.asm")
    data = build()
    dst.write_bytes(data)
    print(f"wrote {dst} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

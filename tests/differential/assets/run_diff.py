#!/usr/bin/env python3
"""Differential test: reconstructed sm_assets (C, host) vs the ORIGINAL v7a
ARM32 machine code run in Unicorn (tools/validation/arm32_ref/armref.py).

Suites
  calc            cRHash::Calc (v7a:0x7d114) vs sm_rhash_calc on real names and
                  synthetic edge cases (every single byte, byte pairs, >=0x80,
                  empty, random, long, and one 16.8 MB string that wraps the
                  32-bit accumulator)
  table           cRHash::Init/Add/Search (v7a:0x7d340/0x7d144/0x7d190) vs
                  sm_rhash_* on a synthetic collision-heavy name set: search
                  results AND the full bucket/chain structure
  jnidatinit      Java_..._JNIDatInit (v7a:0x15244) + RShellDatFind (v7a:0x1b920)
                  on the real archive behind a fake fd with a non-zero start
                  offset vs sm_asm_directory_parse/build_index/find_index:
                  directory image incl. in-place fix-ups, lookups, chains
  loader          RShellLoadFile (v7a:0x1b980) -> PfmLoadFileDat (v7a:0x14c74)
                  -> JAVAC_UnZip/UnJpg/UnPng (JNI into Python stand-ins) vs the
                  reconstructed field semantics (read span, sizes, codec,
                  TGA header + row placement), for every record

Writes tests/differential/assets/result.json. Exit 0 iff no mismatches.
The jnidatinit/loader suites are skipped (recorded as such) when the archive
is absent; exit 77 if the reference binary itself is absent.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SO = REPO / "work/apk_unzip/lib/armeabi-v7a/libsnailmail.so"
ARCHIVE = REPO / "work/apk_unzip/assets/asm.mp3"
V7A_SHA256 = "e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466"
ARCHIVE_SHA256 = "59740ec3a2cd1f7e9ff250e3c6128ffff922193925e3d0316db22a4118861b6a"
SOURCES = ["reconstructed/assets/src/rhash.c", "reconstructed/assets/src/asm_archive.c",
           "reconstructed/assets/include/sm_assets/rhash.h",
           "reconstructed/assets/include/sm_assets/asm_archive.h",
           "tests/differential/assets/sm_assets_cli.c"]

sys.path.insert(0, str(REPO / "tools/validation/arm32_ref"))
sys.path.insert(0, str(REPO / "tools/extraction"))
from armref import ArmRef, GuestFault  # noqa: E402
import asm_archive as X  # noqa: E402  (zip first-entry decoding for the Java stand-in)

M32 = 0xFFFFFFFF


def s32(v: int) -> int:
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


class Suite:
    def __init__(self, name: str, original: list[str], reconstructed: list[str]):
        self.d = {"name": name, "original_functions": original, "reconstructed_functions": reconstructed,
                  "inputs": 0, "comparisons": 0, "mismatches": 0, "first_divergence": None, "notes": []}

    def cmp(self, what: str, orig, recon, ctx=None) -> bool:
        self.d["comparisons"] += 1
        if orig == recon:
            return True
        self.d["mismatches"] += 1
        if self.d["first_divergence"] is None:
            self.d["first_divergence"] = {"what": what, "original": repr(orig)[:400],
                                          "reconstructed": repr(recon)[:400], "context": ctx}
        return False


# ---------------------------------------------------------------- host side
def build_cli(tmp: Path) -> tuple[Path, str]:
    cc = os.environ.get("CC", "cc")
    out = tmp / "sm_assets_cli"
    # SM_DIFF_RECON_DIR: alternative copy of reconstructed/assets (used only for
    # mutation-testing this harness; results are then NOT written to result.json)
    rdir = Path(os.environ.get("SM_DIFF_RECON_DIR", REPO / "reconstructed/assets"))
    cmd = [cc, "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-I", str(rdir / "include"),
           str(rdir / "src/rhash.c"), str(rdir / "src/asm_archive.c"),
           str(HERE / "sm_assets_cli.c"), "-o", str(out)]
    subprocess.run(cmd, check=True)
    ver = subprocess.run([cc, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    return out, ver


def write_hex(path: Path, items: list[bytes]) -> None:
    path.write_text("".join(x.hex() + "\n" for x in items))


def run_cli(cli: Path, *args) -> list[str]:
    r = subprocess.run([str(cli), *map(str, args)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"cli {args[0]} failed rc={r.returncode}: {r.stdout[-400:]} {r.stderr[-400:]}")
    return r.stdout.splitlines()


def parse_table_out(lines: list[str]):
    search, chains, pool, other = [], {}, None, []
    for ln in lines:
        p = ln.split()
        if p[0] == "S":
            search.append(int(p[1]))
        elif p[0] == "B":
            chains[int(p[1])] = [int(x) for x in p[2:]]
        elif p[0] == "P":
            pool = int(p[1])
        else:
            other.append(p)
    return search, chains, pool, other


# --------------------------------------------------------------- guest side
def guest_chains(ref: ArmRef, this: int):
    chains = {}
    for b in range(256):
        v = s32(ref.read_u32(this + 8 * b))
        if v == -1:
            continue
        chain = [v]
        nxt = ref.read_u32(this + 8 * b + 4)
        while nxt:
            chain.append(s32(ref.read_u32(nxt)))
            nxt = ref.read_u32(nxt + 4)
            if len(chain) > 1 << 16:
                raise GuestFault("chain loop")
        chains[b] = chain
    pool = (ref.read_u32(this + 0x804) - ref.read_u32(this + 0x800)) // 8
    return chains, pool


def calc_inputs(names: list[bytes]) -> list[bytes]:
    rnd = random.Random(20260927)
    keys = [b""] + [bytes([c]) for c in range(1, 256)]
    special = [0x01, 0x1F, 0x20, 0x2E, 0x2F, 0x3F, 0x40, 0x41, 0x5A, 0x5B, 0x5C, 0x5F, 0x60, 0x61, 0x7A, 0x7B,
               0x7E, 0x7F, 0x80, 0x9F, 0xA0, 0xBF, 0xC0, 0xC1, 0xDA, 0xDF, 0xE0, 0xE1, 0xFA, 0xFE, 0xFF]
    keys += [bytes([a, b]) for a in special for b in special]
    keys += names + [n.lower() for n in names] + [n.swapcase() for n in names]
    for _ in range(3000):
        keys.append(bytes(rnd.randrange(1, 256) for _ in range(rnd.randrange(0, 81))))
    keys += [b"z" * 1000, b"\xff" * 4096, bytes(rnd.randrange(1, 256) for _ in range(65536))]
    return keys


def suite_calc(ref: ArmRef, cli: Path, tmp: Path, names: list[bytes]) -> dict:
    s = Suite("calc", ["cRHash::Calc(char*) v7a:0x7d114"], ["sm_rhash_calc"])
    keys = calc_inputs(names)
    buf = ref.alloc(max(len(k) for k in keys) + 1)
    orig = []
    for k in keys:
        ref.write(buf, k + b"\0")
        orig.append(ref.call("cRHash::Calc(char*)", 0, buf, max_insns=8 * len(k) + 64))
    write_hex(tmp / "calc.hex", keys)
    recon = [int(x) for x in run_cli(cli, "calc", tmp / "calc.hex")]
    s.d["inputs"] = len(keys)
    s.cmp("result count", len(orig), len(recon))
    for i, (o, r) in enumerate(zip(orig, recon)):
        s.cmp("hash", o, r, {"input_index": i, "input_hex": keys[i][:64].hex(), "input_len": len(keys[i])})
    # 32-bit accumulator wrap: N * 0xff > 2^32
    n = (1 << 32) // 0xFF + 2
    big = ref.alloc(n + 1)
    ref.write(big, b"\xff" * n + b"\0")
    o = ref.call("cRHash::Calc(char*)", 0, big, max_insns=6 * n + 64)
    r = int(run_cli(cli, "calcrep", 0xFF, n)[0])
    s.d["inputs"] += 1
    s.cmp("hash of 0xff*%d (accumulator wraps)" % n, o, r)
    s.d["notes"].append(f"includes one {n}-byte string whose 32-bit sum wraps; bytes 0x01-0xff all covered")
    return s.d


def synthetic_table_names() -> list[bytes]:
    rnd = random.Random(1234)
    names = [b"", b"A", b"a", b"@", b"`", b"AB", b"ab", b"BA", b"x\\y", b"X/Y", b"x/y", b"\x80", b"\xe1", b"\xc1",
             b"DATA/LEVEL00.TXT", b"data/level00.txt", b"DATA/LEVEL00.TXT"]
    # many names in the same few buckets
    for k in range(200):
        names.append(("K%03d" % k).encode())
    for k in range(200):
        names.append(bytes(rnd.randrange(1, 256) for _ in range(rnd.randrange(1, 24))))
    return names


def suite_table(ref: ArmRef, cli: Path, tmp: Path) -> dict:
    s = Suite("table", ["cRHash::Init v7a:0x7d340", "cRHash::Add v7a:0x7d144", "cRHash::Search v7a:0x7d190"],
              ["sm_rhash_init", "sm_rhash_add", "sm_rhash_search", "sm_rhash_chain"])
    names = synthetic_table_names()
    rnd = random.Random(99)
    keys = list(names) + [n.swapcase() for n in names] + [n + b"Q" for n in names[:50]] + [n[:-1] for n in names if n]
    keys += [bytes(rnd.randrange(1, 256) for _ in range(rnd.randrange(0, 12))) for _ in range(500)]
    ptrs = [ref.alloc_cstr(n) for n in names]
    calls = {"n": 0}

    def getter(r):
        v = s32(r.arg(0))
        calls["n"] += 1
        if not 0 <= v < len(ptrs):
            raise GuestFault(f"getter called with out-of-range value {v}")
        return ptrs[v]

    cb = ref.make_callback("table_get_name", getter)
    this = ref.alloc(0x80C)
    ref.call("cRHash::Init(int, char* (*)(int))", this, len(names), cb)
    for i, p in enumerate(ptrs):
        ref.call("cRHash::Add(char*, int)", this, p, i)
    kbuf = ref.alloc(64)
    orig_search = []
    for k in keys:
        ref.write(kbuf, k + b"\0")
        orig_search.append(s32(ref.call("cRHash::Search(char*)", this, kbuf)))
    ochains, opool = guest_chains(ref, this)
    write_hex(tmp / "tnames.hex", names)
    write_hex(tmp / "tkeys.hex", keys)
    rs, rchains, rpool, _ = parse_table_out(run_cli(cli, "table", tmp / "tnames.hex", tmp / "tkeys.hex"))
    s.d["inputs"] = len(names) + len(keys)
    s.cmp("search count", len(orig_search), len(rs))
    for i, (o, r) in enumerate(zip(orig_search, rs)):
        s.cmp("search", o, r, {"key_hex": keys[i].hex()})
    s.cmp("non-empty buckets", sorted(ochains), sorted(rchains))
    for b in sorted(set(ochains) | set(rchains)):
        s.cmp("chain", ochains.get(b), rchains.get(b), {"bucket": b})
    s.cmp("pool nodes used", opool, rpool)
    ref.call("cRHash::UnInit()", this)
    s.d["notes"].append(f"{len(names)} names ({len(set(names))} distinct), getter invoked {calls['n']} times")
    return s.d


# ------------------------------------------------------- JNI / file stand-ins
class FakeJava:
    """Just enough JNI for JNIDatInit and the JAVAC_Un* wrappers. Every call
    is checked; anything unexpected raises."""

    def __init__(self, ref: ArmRef, archive: bytes, dims_by_offset: dict):
        self.ref = ref
        self.archive = archive
        self.arrays: dict[int, bytearray] = {}
        self.next_handle = 0x10000
        self.methods: dict[int, str] = {}
        self.fd = None
        self.log: list[tuple] = []
        self.dims_by_offset = dims_by_offset
        self.last_input: bytes | None = None
        h = {
            "FindClass": self.find_class, "NewGlobalRef": self.new_global_ref, "DeleteLocalRef": self.delete_ref,
            "GetFieldID": self.get_field_id, "GetIntField": self.get_int_field, "GetObjectClass": self.get_obj_class,
            "GetMethodID": self.get_method_id, "NewByteArray": self.new_byte_array,
            "SetByteArrayRegion": self.set_region, "GetByteArrayRegion": self.get_region,
            "CallVoidMethodV": self.call_void_v,
        }
        self.env = ref.make_jnienv(h)

    def handle(self) -> int:
        self.next_handle += 4
        return self.next_handle

    def find_class(self, r):
        name = r.read_cstr(r.arg(1))
        if name != b"java/io/FileDescriptor":
            raise GuestFault(f"unexpected FindClass {name!r}")
        return 0xC1A55

    def new_global_ref(self, r):
        return r.arg(1) | 0x100000

    def delete_ref(self, r):
        self.arrays.pop(r.arg(1), None)

    def get_field_id(self, r):
        if (r.read_cstr(r.arg(2)), r.read_cstr(r.arg(3))) != (b"descriptor", b"I") or r.arg(1) != 0xC1A55:
            raise GuestFault("unexpected GetFieldID")
        return 0xF1D

    def get_int_field(self, r):
        if r.arg(1) != 0xFD0B or r.arg(2) != 0xF1D:
            raise GuestFault("unexpected GetIntField")
        return self.fd

    def get_obj_class(self, r):
        return 0xC1A56

    def get_method_id(self, r):
        h = self.handle()
        self.methods[h] = r.read_cstr(r.arg(2)).decode()
        return h

    def new_byte_array(self, r):
        n = s32(r.arg(1))
        if n < 0:
            raise GuestFault("NewByteArray negative size")
        h = self.handle()
        self.arrays[h] = bytearray(n)
        return h

    def _arr(self, h, start, n):
        a = self.arrays.get(h)
        if a is None or start < 0 or n < 0 or start + n > len(a):
            raise GuestFault(f"ArrayIndexOutOfBounds (array {h:#x} start {start} len {n})")
        return a

    def set_region(self, r):
        h, start, n, buf = r.arg(1), s32(r.arg(2)), s32(r.arg(3)), r.arg(4)
        a = self._arr(h, start, n)
        a[start:start + n] = r.read(buf, n)

    def get_region(self, r):
        h, start, n, buf = r.arg(1), s32(r.arg(2)), s32(r.arg(3)), r.arg(4)
        a = self._arr(h, start, n)
        r.write(buf, bytes(a[start:start + n]))

    def call_void_v(self, r):
        mid, va = r.arg(2), r.arg(3)
        name = self.methods.get(mid)
        out_h, in_h = r.read_u32(va), r.read_u32(va + 4)
        out, inp = self.arrays[out_h], bytes(self.arrays[in_h])
        self.last_input = inp
        self.log.append((name, len(out), len(inp)))
        if name == "JAVAUnZip":
            z = X.decode_zip_first_entry(inp)
            if not z["ok"]:
                raise GuestFault(f"stand-in JAVAUnZip: {z.get('error')}")
            data = z["decoded"]
            n = min(len(data), len(out))       # Java loop throws past the end (caught)
            out[:n] = data[:n]
        elif name in ("JAVAUnPng", "JAVAUnJpg"):
            w, h = self.dims_of(inp)
            pix = w * h * 4
            if pix > len(out):
                raise GuestFault("copyPixelsToBuffer would overflow the Java buffer")
            out[:pix] = bytes(((k * 7 + 3) & 0xFF) for k in range(pix))
        else:
            raise GuestFault(f"unexpected Java method {name}")

    def dims_of(self, inp: bytes):
        s = X.sniff(inp)                           # the image's own header, not the record
        if "width" not in s:
            raise GuestFault("stand-in bitmap decode: no dimensions in header")
        return s["width"], s["height"]


def run_jni_suites(ref: ArmRef, cli: Path, tmp: Path, archive: bytes) -> list[dict]:
    s1 = Suite("jnidatinit", ["Java_..._JNIDatInit v7a:0x15244", "DatHashGetString v7a:0x19954",
                              "RShellDatFind v7a:0x1b920", "cRHash::* v7a:0x7d114-0x7d397"],
               ["sm_asm_directory_parse", "sm_asm_directory_build_index", "sm_asm_directory_find_index"])
    pad = bytes((i * 37 + 11) & 0xFF for i in range(0x1235))   # odd, non-zero start offset
    fj = FakeJava(ref, archive, {})
    fj.fd = ref.add_fd(pad + archive + b"\xEE" * 64)
    start, length = len(pad), len(archive)
    ref.call("Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit", fj.env, 0x7415, 0xFD0B, start, length,
             max_insns=50_000_000)
    g = {n: ref.read_u32(ref.sym(n)) for n in ("gDat", "gDatFP", "gJavaAssetStart", "gJavaAssetLength",
                                               "gJavaAssetFid")}
    s1.cmp("gJavaAssetStart", g["gJavaAssetStart"], start)
    s1.cmp("gJavaAssetLength", g["gJavaAssetLength"], length)
    s1.cmp("gDatFP set", g["gDatFP"] != 0, True)
    gdat = g["gDat"]
    # reconstructed directory
    names_hex = tmp / "anames.hex"
    count = struct.unpack_from("<I", archive, 0)[0]
    dir_size = struct.unpack_from("<I", archive, 8)[0]
    # keys: every name + variants + misses
    parsed = X.parse_directory(archive)       # only used to generate probe strings
    names = [r["name"] for r in parsed["records"]]
    rnd = random.Random(7)
    keys = list(names) + [n.lower() for n in names] + [n.swapcase() for n in names]
    keys += [n[:-1] for n in names] + [n + b"X" for n in names[:100]] + [n.replace(b"/", b"\\") for n in names[:100]]
    keys += [b"./" + n for n in names[:50]] + [b"", b"NO/SUCH/FILE.TXT", b"RANDTABLE", b"randtable.bin\x80"]
    keys += [bytes(rnd.randrange(1, 256) for _ in range(rnd.randrange(0, 40))) for _ in range(300)]
    write_hex(names_hex, keys)
    lines = run_cli(cli, "archive", ARCHIVE, names_hex)
    rs, rchains, rpool, other = parse_table_out(lines)
    n_line = [o for o in other if o[0] == "N"][0]
    s1.cmp("record count", count, int(n_line[1]))
    s1.cmp("directory size", dir_size, int(n_line[2]))
    # directory image in guest: identical to file bytes except the fixed-up name pointers
    img = bytearray(ref.read(gdat, dir_size))
    fix_ok = True
    for i in range(count):
        off = 4 + 24 * i
        name_off = struct.unpack_from("<I", archive, off)[0]
        fix_ok &= s1.cmp("name pointer fix-up", struct.unpack_from("<I", img, off)[0], (gdat + name_off) & M32,
                         {"record": i})
        img[off:off + 4] = archive[off:off + 4]
    s1.cmp("directory bytes (fix-ups undone)", bytes(img), archive[:dir_size])
    # lookups
    kbuf = ref.alloc(4096)
    orig = []
    for k in keys:
        ref.write(kbuf, k + b"\0")
        p = ref.call("RShellDatFind(char*)", kbuf)
        if p == 0:
            orig.append(-1)
        else:
            q, rem = divmod(p - gdat - 4, 24)
            orig.append(q if rem == 0 else ("misaligned", p))
    s1.d["inputs"] = len(keys)
    s1.cmp("lookup count", len(orig), len(rs))
    for i, (o, r) in enumerate(zip(orig, rs)):
        s1.cmp("RShellDatFind index", o, r, {"key_hex": keys[i].hex()})
    ochains, opool = guest_chains(ref, ref.sym("gDatHash"))
    s1.cmp("non-empty buckets", sorted(ochains), sorted(rchains))
    for b in sorted(set(ochains) | set(rchains)):
        s1.cmp("chain", ochains.get(b), rchains.get(b), {"bucket": b})
    s1.cmp("pool nodes used", opool, rpool)
    shadow = [i for i in range(count) if orig[i] != i]
    s1.d["notes"].append(f"fd content = {len(pad)} pad bytes + archive; start offset {start:#x}; "
                         f"records {count}; names resolving to another record: {shadow} -> "
                         f"{[orig[i] for i in shadow]}")

    # ---------------------------------------------------------------- loader
    s2 = Suite("loader", ["RShellLoadFile(char*, void*, int*) v7a:0x1b980", "PfmLoadFileDat v7a:0x14c74",
                          "JAVAC_UnZip v7a:0x14b60", "JAVAC_UnJpg v7a:0x149e8", "JAVAC_UnPng v7a:0x144c0",
                          "JAVA_RegisterFunctions v7a:0x13ca4"],
               ["sm_asm_entry_read_span", "sm_asm_entry_output_extent", "sm_asm_build_tga_header",
                "sm_asm_image_place_rows", "sm_asm_record_raw fields"])
    ref.call("JAVA_RegisterFunctions(_JNIEnv*, _jobject*)", fj.env, 0x0B1)
    recs = parsed["records"]
    max_out = max(max(r["raw"][2] for r in recs), 1) + 64
    buf = ref.alloc(max_out)
    psize = ref.alloc(4)
    codec_java = {1: "JAVAUnZip", 2: "JAVAUnJpg", 3: "JAVAUnPng"}
    for i, r in enumerate(recs):
        if orig[i] != i:
            continue  # shadowed name: loading by name reaches another record
        name_off, data_off, dec, stored, codec, dims = r["raw"]
        ref.write(kbuf, r["name"] + b"\0")
        # (a) buf == (void*)-1: returns data_offset, *size = decoded_size
        ref.write_u32(psize, 0xDEADBEEF)
        o = ref.call("RShellLoadFile(char*, void*, int*)", kbuf, M32, psize)
        s2.cmp("LoadFile(-1) return = data_offset", o, data_off, {"record": i})
        s2.cmp("*size = decoded_size", ref.read_u32(psize), dec, {"record": i})
        # (b) real load into a sentinel-filled buffer
        ref.write(buf, b"\xCD" * max_out)
        fj.log.clear()
        fj.last_input = None
        ref.call("RShellLoadFile(char*, void*, int*)", kbuf, buf, psize, max_insns=200_000_000)
        span = {0: (data_off, dec), 1: (data_off, stored), 2: (data_off, stored), 3: (data_off, stored)}[codec]
        expect_in = archive[span[0]:span[0] + span[1]]
        if codec == 0:
            got = ref.read(buf, dec)
            s2.cmp("raw bytes == archive[read_span]", got, expect_in, {"record": i})
            extent = dec
        else:
            s2.cmp("Java method for codec", [x[0] for x in fj.log], [codec_java[codec]], {"record": i})
            s2.cmp("Java input == archive[read_span]", fj.last_input, expect_in, {"record": i})
            s2.cmp("Java output array length == decoded_size", fj.log[0][1] if fj.log else None, dec,
                   {"record": i})
            if codec == 1:
                data = X.decode_zip_first_entry(expect_in)["decoded"]
                extent = dec
                s2.cmp("zip output bytes", ref.read(buf, dec), data[:dec].ljust(dec, b"\0"), {"record": i})
            else:
                w, h = dims & 0xFFFF, dims >> 16
                extent = 18 + (dec // h) * h
                img_path = tmp / "img.bin"
                run_cli(cli, "image", w, h, dec, img_path)
                s2.cmp("TGA header + placed rows", ref.read(buf, extent), img_path.read_bytes(), {"record": i})
        s2.cmp("no write past output extent", ref.read(buf + extent, 16), b"\xCD" * 16, {"record": i})
        s2.d["inputs"] += 1
    s2.d["notes"].append("Java side replaced by Python stand-ins: JAVAUnZip decodes the first zip entry; "
                         "JAVAUnPng/UnJpg write a synthetic w*h*4 pattern (bitmap decoding itself is NOT "
                         "compared). Shadowed-name records skipped (unreachable by name).")
    return [s1.d, s2.d]


def main() -> int:
    if not SO.is_file():
        print(f"SKIP: reference binary {SO} absent")
        return 77
    ref = ArmRef(str(SO), expected_sha256=V7A_SHA256)
    have_archive = ARCHIVE.is_file()
    archive = ARCHIVE.read_bytes() if have_archive else b""
    names = [r["name"] for r in X.parse_directory(archive)["records"]] if have_archive else []
    suites = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cli, ccver = build_cli(tmp)
        suites.append(suite_calc(ref, cli, tmp, names))
        suites.append(suite_table(ref, cli, tmp))
        if have_archive:
            suites += run_jni_suites(ref, cli, tmp, archive)
        else:
            suites.append({"name": "jnidatinit+loader", "skipped": "archive absent"})
    import unicorn
    result = {
        "reference": {"binary": "lib/armeabi-v7a/libsnailmail.so", "sha256": ref.sha256,
                      "harness": "tools/validation/arm32_ref/armref.py", "emulator": f"unicorn {unicorn.__version__}",
                      "load_base": hex(ref.base), "relocations_applied": ref.relocation_counts},
        "reconstructed": {"sources": {p: hashlib.sha256((REPO / p).read_bytes()).hexdigest() for p in SOURCES},
                          "compiler": ccver, "flags": "-std=c11 -O1 -Wall -Wextra -Werror"},
        "archive": {"present": have_archive, "sha256": hashlib.sha256(archive).hexdigest() if have_archive else None,
                    "expected_sha256": ARCHIVE_SHA256},
        "comparison": "exact (integers, byte strings, chain lists)",
        "suites": suites,
        "totals": {"comparisons": sum(s.get("comparisons", 0) for s in suites),
                   "mismatches": sum(s.get("mismatches", 0) for s in suites)},
    }
    if "SM_DIFF_RECON_DIR" in os.environ:
        print("SM_DIFF_RECON_DIR set: result.json not written")
    else:
        (HERE / "result.json").write_text(json.dumps(result, indent=1) + "\n")
    for s in suites:
        print(f"{s['name']:12s} inputs={s.get('inputs')} comparisons={s.get('comparisons')} "
              f"mismatches={s.get('mismatches')} {s.get('skipped', '')}")
        if s.get("first_divergence"):
            print("   first divergence:", s["first_divergence"])
    print("totals:", result["totals"])
    return 0 if result["totals"]["mismatches"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Shared, deterministic ELF helpers for the Snail Mail native pipeline.

Analysis-only. Never linked into the shipping build. Every helper reads the
original libraries read-only and verifies their SHA-256 before use.

Binaries (see docs/CONVENTIONS.md):
    v7a  lib/armeabi-v7a/libsnailmail.so  (primary reference)
    v5   lib/armeabi/libsnailmail.so

Both are ET_DYN with the first PT_LOAD at vaddr 0, so a module-relative
address equals the ELF virtual address.
"""
from __future__ import annotations

import bisect
import hashlib
import io
import shutil
import struct
import subprocess
from collections import namedtuple
from pathlib import Path

from elftools.elf.elffile import ELFFile

REPO = Path(__file__).resolve().parents[2]

BINARIES = {
    "v7a": {
        "path": "work/apk_unzip/lib/armeabi-v7a/libsnailmail.so",
        "apk_path": "lib/armeabi-v7a/libsnailmail.so",
        "sha256": "e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466",
        "ghidra_language": "ARM:LE:32:v7",
        "ghidra_compiler": "default",
    },
    "v5": {
        "path": "work/apk_unzip/lib/armeabi/libsnailmail.so",
        "apk_path": "lib/armeabi/libsnailmail.so",
        "sha256": "96dbeaeb20c60d687301ca769656727467371489db5e3ed744a93248bc8d8136",
        "ghidra_language": "ARM:LE:32:v5t",
        "ghidra_compiler": "default",
    },
}

# ARM relocation type numbers (ELF for the ARM Architecture, IHI0044).
R_ARM_NAMES = {
    0: "R_ARM_NONE", 1: "R_ARM_PC24", 2: "R_ARM_ABS32", 3: "R_ARM_REL32",
    17: "R_ARM_TLS_DTPMOD32", 18: "R_ARM_TLS_DTPOFF32", 19: "R_ARM_TLS_TPOFF32",
    20: "R_ARM_COPY", 21: "R_ARM_GLOB_DAT", 22: "R_ARM_JUMP_SLOT",
    23: "R_ARM_RELATIVE", 24: "R_ARM_GOTOFF32", 25: "R_ARM_BASE_PREL",
    26: "R_ARM_GOT_BREL", 28: "R_ARM_CALL", 29: "R_ARM_JUMP24",
    42: "R_ARM_PREL31", 43: "R_ARM_MOVW_ABS_NC", 44: "R_ARM_MOVT_ABS",
}

Sym = namedtuple("Sym", "table idx name value size type bind vis shndx section")
Reloc = namedtuple("Reloc", "section offset type type_name sym_idx sym_name sym_value sym_shndx")
ExidxEntry = namedtuple("ExidxEntry", "entry_addr fn_addr kind word1 extab_addr")
Region = namedtuple("Region", "start end kind section")
PltStub = namedtuple("PltStub", "addr size got_slot import_name reloc_index")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hx(v: int | None) -> str | None:
    return None if v is None else f"0x{v:x}"


def arm_expand_imm(imm12: int) -> int:
    """ARM modified-immediate (A5.2.4): imm8 rotated right by 2*rot."""
    rot = (imm12 >> 8) & 0xF
    imm8 = imm12 & 0xFF
    r = 2 * rot
    return ((imm8 >> r) | (imm8 << (32 - r))) & 0xFFFFFFFF if r else imm8


def prel31(word: int, place: int) -> int:
    off = word & 0x7FFFFFFF
    if off & 0x40000000:
        off -= 0x80000000
    return (place + off) & 0xFFFFFFFF


_DEMANGLE_CACHE: dict[str, str] = {}


def demangle_tool() -> str:
    return shutil.which("c++filt") or shutil.which("llvm-cxxfilt") or ""


def demangle_many(names) -> dict[str, str]:
    """Demangle via GNU c++filt (batch, deterministic). Unmangled names map to themselves."""
    todo = sorted({n for n in names if n not in _DEMANGLE_CACHE})
    tool = demangle_tool()
    if todo and tool:
        out = subprocess.run([tool], input="\n".join(todo) + "\n", capture_output=True,
                             text=True, check=True).stdout.split("\n")
        for n, d in zip(todo, out):
            _DEMANGLE_CACHE[n] = d
    for n in todo:
        _DEMANGLE_CACHE.setdefault(n, n)
    return {n: _DEMANGLE_CACHE[n] for n in names}


class Binary:
    """Parsed view of one input library. All lists are sorted deterministically."""

    def __init__(self, name: str, verify: bool = True):
        if name not in BINARIES:
            raise SystemExit(f"unknown binary {name!r}; expected one of {sorted(BINARIES)}")
        self.name = name
        self.meta = BINARIES[name]
        self.path = REPO / self.meta["path"]
        self.data = self.path.read_bytes()
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        if verify and self.sha256 != self.meta["sha256"]:
            raise SystemExit(f"{self.path}: sha256 {self.sha256} != expected {self.meta['sha256']}")
        self.elf = ELFFile(io.BytesIO(self.data))
        self._load_sections()
        self._load_segments()
        self.symtab = self._load_syms(".symtab")
        self.dynsym = self._load_syms(".dynsym")
        self._load_relocs()
        self._load_exidx()
        self._load_mapping()
        self._load_plt()

    # ------------------------------------------------------------------ basics
    def _load_sections(self):
        self.sections = []
        for i, s in enumerate(self.elf.iter_sections()):
            self.sections.append({
                "index": i, "name": s.name, "type": s["sh_type"], "addr": s["sh_addr"],
                "offset": s["sh_offset"], "size": s["sh_size"], "flags": s["sh_flags"],
                "link": s["sh_link"], "info": s["sh_info"], "align": s["sh_addralign"],
                "entsize": s["sh_entsize"],
            })
        self.sec_by_name = {s["name"]: s for s in self.sections}

    def _load_segments(self):
        self.segments = []
        for s in self.elf.iter_segments():
            self.segments.append({
                "type": s["p_type"], "offset": s["p_offset"], "vaddr": s["p_vaddr"],
                "paddr": s["p_paddr"], "filesz": s["p_filesz"], "memsz": s["p_memsz"],
                "flags": s["p_flags"], "align": s["p_align"],
            })
        self.loads = [s for s in self.segments if s["type"] == "PT_LOAD"]

    def section_of(self, va: int) -> str | None:
        best = None
        for s in self.sections:
            if s["addr"] and s["flags"] & 2 and s["addr"] <= va < s["addr"] + s["size"]:
                best = s["name"]
        return best

    def vaddr_to_offset(self, va: int) -> int | None:
        for s in self.loads:
            if s["vaddr"] <= va < s["vaddr"] + s["filesz"]:
                return s["offset"] + va - s["vaddr"]
        return None

    def read(self, va: int, n: int) -> bytes:
        off = self.vaddr_to_offset(va)
        if off is None:
            end = va + n
            for s in self.loads:  # zero-initialised (.bss) part of a PT_LOAD
                if s["vaddr"] <= va and end <= s["vaddr"] + s["memsz"]:
                    return b"\x00" * n
            raise ValueError(f"{self.name}: 0x{va:x} not mapped from file")
        return self.data[off:off + n]

    def u32(self, va: int) -> int:
        return struct.unpack("<I", self.read(va, 4))[0]

    def cstring(self, va: int, limit: int = 4096) -> str | None:
        off = self.vaddr_to_offset(va)
        if off is None:
            return None
        end = self.data.find(b"\x00", off, off + limit)
        if end < 0:
            return None
        raw = self.data[off:end]
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    # ------------------------------------------------------------------ symbols
    def _load_syms(self, secname: str) -> list[Sym]:
        sec = self.elf.get_section_by_name(secname)
        out = []
        if sec is None:
            return out
        for i, s in enumerate(sec.iter_symbols()):
            shndx = s["st_shndx"]
            secn = None
            if isinstance(shndx, int) and 0 < shndx < len(self.sections):
                secn = self.sections[shndx]["name"]
            out.append(Sym(secname, i, s.name, s["st_value"], s["st_size"],
                           s["st_info"]["type"], s["st_info"]["bind"],
                           s["st_other"]["visibility"], shndx, secn))
        return out

    def defined(self, sym: Sym) -> bool:
        return isinstance(sym.shndx, int) and sym.shndx != 0

    def func_symbols(self) -> list[Sym]:
        return [s for s in self.symtab if s.type == "STT_FUNC" and self.defined(s)]

    def object_symbols(self) -> list[Sym]:
        return [s for s in self.symtab if s.type == "STT_OBJECT" and self.defined(s)]

    def exported_names(self) -> set[str]:
        return {s.name for s in self.dynsym if s.name and self.defined(s)}

    def imported_syms(self) -> list[Sym]:
        return [s for s in self.dynsym if s.name and s.shndx == "SHN_UNDEF"]

    # ------------------------------------------------------------------ relocations
    def _load_relocs(self):
        self.relocs: list[Reloc] = []
        dynsym = self.dynsym
        for secname in (".rel.dyn", ".rel.plt"):
            sec = self.elf.get_section_by_name(secname)
            if sec is None:
                continue
            for r in sec.iter_relocations():
                t = r["r_info_type"]
                si = r["r_info_sym"]
                sym = dynsym[si] if si and si < len(dynsym) else None
                self.relocs.append(Reloc(secname, r["r_offset"], t, R_ARM_NAMES.get(t, f"R_ARM_{t}"), si,
                                         sym.name if sym else None, sym.value if sym else None,
                                         sym.shndx if sym else None))
        self.reloc_at = {}
        for r in self.relocs:
            self.reloc_at.setdefault(r.offset, r)

    def resolve_word(self, va: int) -> dict:
        """Value of the 32-bit word at `va` after dynamic relocation at load bias 0.

        Returns {"value": int|None, "kind": "plain|relative|abs32|glob_dat|jump_slot|import",
                 "symbol": name|None, "addend": int}.
        """
        raw = self.u32(va)
        r = self.reloc_at.get(va)
        if r is None:
            return {"value": raw, "kind": "plain", "symbol": None, "addend": 0, "raw": raw}
        if r.type == 23:  # RELATIVE: B + A (A = stored word, REL)
            return {"value": raw, "kind": "relative", "symbol": None, "addend": 0, "raw": raw}
        if r.type in (2, 21, 22):  # ABS32 (S+A), GLOB_DAT (S), JUMP_SLOT (S)
            kind = {2: "abs32", 21: "glob_dat", 22: "jump_slot"}[r.type]
            addend = raw if r.type == 2 else 0
            if r.sym_shndx == "SHN_UNDEF" or r.sym_shndx is None:
                return {"value": None, "kind": "import", "symbol": r.sym_name, "addend": addend,
                        "reloc": kind, "raw": raw}
            return {"value": (r.sym_value + addend) & 0xFFFFFFFF, "kind": kind,
                    "symbol": r.sym_name, "addend": addend, "raw": raw}
        return {"value": None, "kind": r.type_name, "symbol": r.sym_name, "addend": 0, "raw": raw}

    # ------------------------------------------------------------------ EXIDX
    def _load_exidx(self):
        self.exidx: list[ExidxEntry] = []
        sec = self.sec_by_name.get(".ARM.exidx")
        if not sec:
            return
        base = sec["addr"]
        for i in range(sec["size"] // 8):
            ea = base + 8 * i
            w0 = self.u32(ea)
            w1 = self.u32(ea + 4)
            fn = prel31(w0, ea)
            if w1 == 1:
                kind, ext = "cantunwind", None
            elif w1 & 0x80000000:
                kind, ext = "inline", None
            else:
                kind, ext = "extab", prel31(w1, ea + 4)
            self.exidx.append(ExidxEntry(ea, fn, kind, w1, ext))
        self.exidx_by_fn = {e.fn_addr: e for e in self.exidx}
        self.exidx_starts = sorted(self.exidx_by_fn)

    # ------------------------------------------------------------------ mapping symbols
    def _load_mapping(self):
        """$a/$t/$d mapping symbols -> ordered regions per executable section."""
        ms = []
        for s in self.symtab:
            if s.name and s.name[:2] in ("$a", "$t", "$d") and (len(s.name) == 2 or s.name[2] == "."):
                if self.defined(s):
                    ms.append((s.value, s.name[1], s.section or ""))
        ms.sort()
        self.mapping_symbols = ms
        self.mapping_conflicts = []
        self.regions: list[Region] = []
        exec_secs = [s for s in self.sections if s["flags"] & 4 and s["size"]]  # SHF_EXECINSTR
        for sec in exec_secs:
            lo, hi = sec["addr"], sec["addr"] + sec["size"]
            pts = {}
            for (a, k, _sn) in ms:
                if lo <= a < hi:
                    if a in pts and pts[a] != k:
                        self.mapping_conflicts.append({"addr": hx(a), "kinds": sorted({pts[a], k})})
                    pts[a] = k  # last wins (deterministic given sorted input)
            addrs = sorted(pts)
            if not addrs or addrs[0] != lo:
                first = addrs[0] if addrs else hi
                self.regions.append(Region(lo, first, "unmapped", sec["name"]))
            for i, a in enumerate(addrs):
                end = addrs[i + 1] if i + 1 < len(addrs) else hi
                kind = {"a": "arm", "t": "thumb", "d": "data"}[pts[a]]
                # merge consecutive same-kind points
                if self.regions and self.regions[-1].kind == kind and self.regions[-1].end == a \
                        and self.regions[-1].section == sec["name"]:
                    prev = self.regions.pop()
                    self.regions.append(Region(prev.start, end, kind, sec["name"]))
                else:
                    self.regions.append(Region(a, end, kind, sec["name"]))
        self._region_starts = [r.start for r in self.regions]

    def region_at(self, va: int) -> Region | None:
        i = bisect.bisect_right(self._region_starts, va) - 1
        if i >= 0 and self.regions[i].start <= va < self.regions[i].end:
            return self.regions[i]
        return None

    # ------------------------------------------------------------------ PLT
    def _load_plt(self):
        """Decode the classic ARM PLT (20-byte header + 12-byte stubs)."""
        self.plt: list[PltStub] = []
        self.plt_by_addr = {}
        sec = self.sec_by_name.get(".plt")
        if not sec:
            return
        jslots = {r.offset: (i, r) for i, r in enumerate(x for x in self.relocs if x.section == ".rel.plt")}
        a = sec["addr"] + 20
        end = sec["addr"] + sec["size"]
        while a + 12 <= end:
            i0, i1, i2 = (self.u32(a + k) for k in (0, 4, 8))
            # add ip, pc, #imm ; add ip, ip, #imm ; ldr pc, [ip, #imm]!
            if (i0 & 0xFFFFF000) == 0xE28FC000 and (i1 & 0xFFFFF000) == 0xE28CC000 \
                    and (i2 & 0xFFFFF000) == 0xE5BCF000:
                got = (a + 8 + arm_expand_imm(i0 & 0xFFF) + arm_expand_imm(i1 & 0xFFF) + (i2 & 0xFFF)) & 0xFFFFFFFF
                idx, r = jslots.get(got, (None, None))
                stub = PltStub(a, 12, got, r.sym_name if r else None, idx)
                self.plt.append(stub)
                self.plt_by_addr[a] = stub
            a += 12

    # ------------------------------------------------------------------ misc
    def got_base(self) -> int | None:
        s = self.sec_by_name.get(".got")
        return s["addr"] if s else None

    def text_range(self) -> tuple[int, int]:
        s = self.sec_by_name[".text"]
        return s["addr"], s["addr"] + s["size"]

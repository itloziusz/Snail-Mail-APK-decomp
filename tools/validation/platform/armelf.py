"""Shared static-analysis helpers for the ARM32 libsnailmail.so builds.

Analysis-only (never linked into the port). Used by import_census.py and
gl_census.py. Everything here is derived from the ELF bytes:

* symbol table (.symtab, unstripped) for function attribution;
* ARM ELF mapping symbols ($a / $d / $t) so literal pools are never decoded
  as instructions;
* .rel.plt (R_ARM_JUMP_SLOT) + the PLT stub encoding to map PLT stub
  addresses to imported symbol names (each stub is decoded and the GOT slot it
  loads is checked against the relocation offset -- no layout assumption is
  trusted without verification);
* .rel.dyn (R_ARM_GLOB_DAT / R_ARM_ABS32 / R_ARM_RELATIVE) to resolve GOT slot
  contents;
* a small, conservative forward constant-propagation over each function's
  basic blocks (core registers, VFP single registers and outgoing stack
  argument slots), used to recover constant call arguments.

The value lattice is deliberately small: a register is either unknown (None),
a small set of possible 32-bit constants, a "load of global memory" marker, or
an "imported data symbol address" marker. Anything not modelled becomes
unknown, so every recovered value is a lower-bound, never a guess.
"""

from __future__ import annotations

import bisect
import hashlib
import struct
from dataclasses import dataclass, field

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
from capstone import arm as A
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

R_ARM_ABS32 = 2
R_ARM_GLOB_DAT = 21
R_ARM_JUMP_SLOT = 22
R_ARM_RELATIVE = 23

MAXSET = 4  # max number of alternative constants tracked per register


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Value lattice
# --------------------------------------------------------------------------
# None                       -> unknown
# ("k", frozenset[int])      -> one of these 32-bit constants
# ("mem", addr)              -> 32-bit value loaded from writable global memory
#                               at a statically known address (runtime value)
# ("imp", name)              -> address of an imported data symbol (GOT GLOB_DAT)
# ("arg", i)                 -> unchanged incoming argument register r<i> of the
#                               containing function (softfp: also float args)

def K(v: int):
    return ("k", frozenset([v & 0xFFFFFFFF]))


def is_const(v) -> bool:
    return v is not None and v[0] == "k"


def single(v):
    if is_const(v) and len(v[1]) == 1:
        return next(iter(v[1]))
    return None


def meet(a, b):
    if a == b:
        return a
    if a is None or b is None:
        return None
    if a[0] == "k" and b[0] == "k":
        s = a[1] | b[1]
        return ("k", s) if len(s) <= MAXSET else None
    return None


def binop(a, b, fn):
    if not (is_const(a) and is_const(b)):
        return None
    out = set()
    for x in a[1]:
        for y in b[1]:
            out.add(fn(x, y) & 0xFFFFFFFF)
    return ("k", frozenset(out)) if len(out) <= MAXSET else None


def unop(a, fn):
    if not is_const(a):
        return None
    return ("k", frozenset(fn(x) & 0xFFFFFFFF for x in a[1]))


def _arm_expand_imm(imm12: int) -> int:
    """ARM modified immediate: 8-bit value rotated right by 2*rot."""
    rot = (imm12 >> 8) * 2
    v = imm12 & 0xFF
    return ((v >> rot) | (v << (32 - rot))) & 0xFFFFFFFF if rot else v


def f32(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


# --------------------------------------------------------------------------
# ELF model
# --------------------------------------------------------------------------
@dataclass
class Func:
    addr: int
    size: int
    name: str
    aliases: list = field(default_factory=list)


class ArmElf:
    def __init__(self, path: str, label: str):
        self.path = path
        self.label = label
        self.sha256 = sha256_file(path)
        with open(path, "rb") as f:
            self.data = f.read()
        self.elf = ELFFile(open(path, "rb"))
        assert self.elf.elfclass == 32 and self.elf["e_machine"] == "EM_ARM"
        self.sections = {}
        for s in self.elf.iter_sections():
            self.sections[s.name] = (s["sh_addr"], s["sh_size"], s["sh_offset"], s["sh_type"], s["sh_flags"])
        self._load_symbols()
        self._load_relocs()
        self._load_plt()
        self.md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        self.md.detail = True
        self._insn_cache = {}

    # ---------------- memory access ----------------
    def section_of(self, addr: int):
        for name, (a, sz, off, typ, flg) in self.sections.items():
            if a and a <= addr < a + sz:
                return name
        return None

    def vaddr_to_off(self, addr: int):
        for seg in self.elf.iter_segments():
            if seg["p_type"] != "PT_LOAD":
                continue
            if seg["p_vaddr"] <= addr < seg["p_vaddr"] + seg["p_filesz"]:
                return addr - seg["p_vaddr"] + seg["p_offset"]
        return None

    def read32(self, addr: int):
        off = self.vaddr_to_off(addr)
        if off is None or off + 4 > len(self.data):
            return None
        return struct.unpack_from("<I", self.data, off)[0]

    def read_bytes(self, addr: int, n: int):
        off = self.vaddr_to_off(addr)
        if off is None:
            return None
        return self.data[off:off + n]

    def cstring(self, addr: int, maxlen: int = 200):
        off = self.vaddr_to_off(addr)
        if off is None:
            return None
        end = self.data.find(b"\0", off, off + maxlen)
        if end < 0:
            return None
        raw = self.data[off:end]
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    def is_readonly_addr(self, addr: int) -> bool:
        sec = self.section_of(addr)
        return sec in (".rodata", ".text")

    # ---------------- symbols ----------------
    def _load_symbols(self):
        symtab = self.elf.get_section_by_name(".symtab")
        self.maps = {}  # section index -> sorted list of (addr, kind)
        funcs = {}
        self.objects = []  # (addr, size, name)
        self.sym_by_name = {}
        for sym in symtab.iter_symbols():
            name = sym.name
            val = sym["st_value"]
            typ = sym["st_info"]["type"]
            shndx = sym["st_shndx"]
            if name in ("$a", "$d", "$t") or name.startswith(("$a.", "$d.", "$t.")):
                if isinstance(shndx, int):
                    self.maps.setdefault(shndx, []).append((val, name[:2]))
                continue
            if typ == "STT_FUNC" and isinstance(shndx, int):
                if val in funcs:
                    if name != funcs[val].name and name not in funcs[val].aliases:
                        funcs[val].aliases.append(name)
                else:
                    funcs[val] = Func(val, sym["st_size"], name)
                self.sym_by_name.setdefault(name, val)
            elif typ == "STT_OBJECT" and isinstance(shndx, int):
                self.objects.append((val, sym["st_size"], name))
                self.sym_by_name.setdefault(name, val)
        for k in self.maps:
            self.maps[k].sort()
        self.funcs = [funcs[a] for a in sorted(funcs)]
        self.func_addrs = [f.addr for f in self.funcs]
        self.objects.sort()
        self.obj_addrs = [o[0] for o in self.objects]
        self.thumb_mapping_symbols = sum(1 for v in self.maps.values() for _, k in v if k == "$t")
        self.odd_func_symbols = [f.name for f in self.funcs if f.addr & 1]

    def func_at(self, addr: int):
        i = bisect.bisect_right(self.func_addrs, addr) - 1
        if i < 0:
            return None
        f = self.funcs[i]
        if f.addr <= addr < f.addr + max(f.size, 4):
            return f
        return None

    def object_at(self, addr: int):
        i = bisect.bisect_right(self.obj_addrs, addr) - 1
        if i < 0:
            return None
        a, sz, name = self.objects[i]
        if a <= addr < a + max(sz, 1):
            return (name, addr - a)
        return None

    def describe_addr(self, addr: int) -> str:
        o = self.object_at(addr)
        if o:
            return f"{o[0]}+0x{o[1]:x}" if o[1] else o[0]
        f = self.func_at(addr)
        if f:
            return f"{f.name}+0x{addr - f.addr:x}" if addr != f.addr else f.name
        sec = self.section_of(addr)
        return f"{sec or '?'}:0x{addr:x}"

    def code_ranges(self, secname: str = ".text"):
        """Yield (start, end) ranges in `secname` that mapping symbols mark as ARM ($a)."""
        idx = None
        for i, s in enumerate(self.elf.iter_sections()):
            if s.name == secname:
                idx = i
        a0, sz, _, _, _ = self.sections[secname]
        marks = [m for m in self.maps.get(idx, []) if a0 <= m[0] < a0 + sz]
        out = []
        for j, (addr, kind) in enumerate(marks):
            end = marks[j + 1][0] if j + 1 < len(marks) else a0 + sz
            if kind == "$a" and end > addr:
                out.append((addr, end))
            if kind == "$t":
                raise RuntimeError(f"Thumb mapping symbol at 0x{addr:x}: Thumb decoding not implemented")
        return out

    def data_ranges(self, secname: str = ".text"):
        idx = None
        for i, s in enumerate(self.elf.iter_sections()):
            if s.name == secname:
                idx = i
        a0, sz, _, _, _ = self.sections[secname]
        marks = [m for m in self.maps.get(idx, []) if a0 <= m[0] < a0 + sz]
        out = []
        for j, (addr, kind) in enumerate(marks):
            end = marks[j + 1][0] if j + 1 < len(marks) else a0 + sz
            if kind == "$d" and end > addr:
                out.append((addr, end))
        return out

    # ---------------- relocations / PLT ----------------
    def _load_relocs(self):
        dynsym = self.elf.get_section_by_name(".dynsym")
        self.got_reloc = {}  # slot addr -> (type, symname)
        self.jump_slots = []  # (slot addr, symname) in .rel.plt order
        self.textrel = []  # relocations applied inside non-writable sections
        for s in self.elf.iter_sections():
            if not isinstance(s, RelocationSection):
                continue
            for r in s.iter_relocations():
                typ = r["r_info_type"]
                symidx = r["r_info_sym"]
                name = dynsym.get_symbol(symidx).name if symidx else ""
                off = r["r_offset"]
                if s.name == ".rel.plt":
                    assert typ == R_ARM_JUMP_SLOT
                    self.jump_slots.append((off, name))
                self.got_reloc[off] = (typ, name)
                sec = self.section_of(off)
                if sec in (".text", ".rodata", ".ARM.extab", ".ARM.exidx"):
                    self.textrel.append((off, typ, name, sec))
        self.dyn_undef = []
        for sym in dynsym.iter_symbols():
            if sym["st_shndx"] == "SHN_UNDEF" and sym.name:
                self.dyn_undef.append((sym.name, sym["st_info"]["bind"], sym["st_info"]["type"]))

    def _load_plt(self):
        a0, sz, _, _, _ = self.sections[".plt"]
        self.plt_start, self.plt_end = a0, a0 + sz
        self.plt_map = {}  # stub addr -> import name
        # PLT0 is 5 words (str lr / ldr lr / add lr / ldr pc / .word); each
        # following stub is 3 ARM instructions: add ip, pc, #X; add ip, ip, #Y;
        # ldr pc, [ip, #Z]!  -> GOT slot = stub + 8 + X + Y + Z.
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        md.detail = True
        stub = a0 + 20
        for slot, name in self.jump_slots:
            code = self.read_bytes(stub, 12)
            insns = list(md.disasm(code, stub))
            assert len(insns) == 3, f"PLT stub decode failed at 0x{stub:x}"
            i0, i1, i2 = insns
            assert i0.mnemonic == "add" and i1.mnemonic == "add" and i2.mnemonic == "ldr", (i0, i1, i2)
            # Decode the modified-immediate fields from the raw encoding
            # (capstone prints non-canonical rotations as two operands).
            w0, w1, w2 = struct.unpack("<3I", code)
            x = _arm_expand_imm(w0 & 0xFFF)
            y = _arm_expand_imm(w1 & 0xFFF)
            z = (w2 & 0xFFF) if (w2 >> 23) & 1 else -(w2 & 0xFFF)
            got = (stub + 8 + x + y + z) & 0xFFFFFFFF
            if got != slot:
                raise RuntimeError(f"PLT stub 0x{stub:x} loads GOT 0x{got:x}, expected 0x{slot:x} ({name})")
            self.plt_map[stub] = name
            stub += 12
        assert stub == self.plt_end, (hex(stub), hex(self.plt_end))

    # ---------------- disassembly ----------------
    def insns_in(self, start: int, end: int):
        key = (start, end)
        if key in self._insn_cache:
            return self._insn_cache[key]
        code = self.read_bytes(start, end - start)
        out = []
        addr = start
        # Decode word by word so a single undecodable word does not stop the sweep.
        while addr < end:
            got = list(self.md.disasm(code[addr - start:addr - start + 4], addr))
            out.append(got[0] if got else None)
            addr += 4
        self._insn_cache[key] = out
        return out

    def _code_ranges_cached(self):
        if not hasattr(self, "_cr"):
            self._cr = self.code_ranges(".text")
        return self._cr

    def all_code_insns(self):
        for s, e in self._code_ranges_cached():
            for insn in self.insns_in(s, e):
                if insn is not None:
                    yield insn

    def func_code(self, f: Func):
        """Instructions of function f, restricted to $a ranges (literal pools skipped)."""
        end = f.addr + max(f.size, 4)
        out = []
        # decode whole $a ranges (cached) and slice, to keep cache hits high
        for s, e in self._code_ranges_cached():
            if e <= f.addr or s >= end:
                continue
            for insn in self.insns_in(s, e):
                if insn is not None and f.addr <= insn.address < end:
                    out.append(insn)
        return out

    def branch_target(self, insn):
        if insn.id in (A.ARM_INS_B, A.ARM_INS_BL, A.ARM_INS_BLX) and insn.operands and insn.operands[0].type == A.ARM_OP_IMM:
            return insn.operands[0].imm & 0xFFFFFFFF
        return None


# --------------------------------------------------------------------------
# Constant propagation
# --------------------------------------------------------------------------
CORE = [f"r{i}" for i in range(13)]
CALLER_SAVED_CORE = ["r0", "r1", "r2", "r3", "r12", "lr"]
CALLER_SAVED_S = [f"s{i}" for i in range(16)]


def _is_terminator(insn):
    """True if control never falls through (unconditional branch/return)."""
    if insn.cc != A.ARM_CC_AL:
        return False
    if insn.id == A.ARM_INS_B:
        return True
    if insn.id == A.ARM_INS_BX:
        return True
    _, w = insn.regs_access()
    if A.ARM_REG_PC in w and insn.id not in (A.ARM_INS_BL, A.ARM_INS_BLX):
        return True
    return False


def _writes_pc_indirect(insn):
    _, w = insn.regs_access()
    return A.ARM_REG_PC in w and insn.id not in (A.ARM_INS_B, A.ARM_INS_BL, A.ARM_INS_BLX)


class State:
    __slots__ = ("r", "s", "stk")

    def __init__(self, r=None, s=None, stk=None):
        self.r = dict(r or {})
        self.s = dict(s or {})
        self.stk = dict(stk or {})

    def copy(self):
        return State(self.r, self.s, self.stk)

    def key(self):
        return (tuple(sorted(self.r.items())), tuple(sorted(self.s.items())), tuple(sorted(self.stk.items())))

    @staticmethod
    def meet(a, b):
        if a is None:
            return b.copy()
        out = State()
        for k in set(a.r) & set(b.r):
            v = meet(a.r[k], b.r[k])
            if v is not None:
                out.r[k] = v
        for k in set(a.s) & set(b.s):
            v = meet(a.s[k], b.s[k])
            if v is not None:
                out.s[k] = v
        for k in set(a.stk) & set(b.stk):
            v = meet(a.stk[k], b.stk[k])
            if v is not None:
                out.stk[k] = v
        return out


class ConstProp:
    """Per-function forward constant propagation with block-level merges."""

    def __init__(self, elf: ArmElf, func: Func):
        self.elf = elf
        self.func = func
        self.insns = elf.func_code(func)
        self.by_addr = {i.address: i for i in self.insns}
        self._build_blocks()
        self.pre_state = {}  # insn addr -> State before the instruction
        self._run()

    def _build_blocks(self):
        leaders = {self.func.addr}
        self.succ = {}
        addrs = [i.address for i in self.insns]
        aset = set(addrs)
        self.unknown_entry = set()  # blocks reachable through untracked edges
        # ARMv4-style indirect call `mov lr, pc ; ldr pc, [...]` returns to the
        # next instruction: treat it as a call, not as a block terminator.
        self.indirect_calls = set()
        for idx in range(1, len(self.insns)):
            prev, insn = self.insns[idx - 1], self.insns[idx]
            if (insn.id == A.ARM_INS_LDR and _writes_pc_indirect(insn) and prev.address + 4 == insn.address
                    and prev.id == A.ARM_INS_MOV and len(prev.operands) == 2
                    and prev.operands[0].type == A.ARM_OP_REG and prev.operands[0].reg == A.ARM_REG_LR
                    and prev.operands[1].type == A.ARM_OP_REG and prev.operands[1].reg == A.ARM_REG_PC):
                self.indirect_calls.add(insn.address)
        for idx, insn in enumerate(self.insns):
            nxt = addrs[idx + 1] if idx + 1 < len(addrs) else None
            if insn.address in self.indirect_calls:
                continue
            if insn.id == A.ARM_INS_B:
                t = self.elf.branch_target(insn)
                if t in aset:
                    leaders.add(t)
                if nxt is not None:
                    leaders.add(nxt)
            elif _writes_pc_indirect(insn):
                # jump table / return: successors unknown
                if nxt is not None:
                    leaders.add(nxt)
                if insn.cc != A.ARM_CC_AL or insn.id == A.ARM_INS_LDR or insn.id == A.ARM_INS_ADD:
                    # conditional return or jump-table dispatch: following
                    # code may be reached from untracked edges
                    pass
            if nxt is not None and nxt != insn.address + 4:
                leaders.add(nxt)  # gap (literal pool) inside the function
            # GCC ARM-mode jump table: `ldr[cc] pc, [pc, rX, lsl #2]` followed
            # by a `b default` and a table of absolute code addresses.
            if (insn.id == A.ARM_INS_LDR and _writes_pc_indirect(insn)
                    and insn.operands[1].type == A.ARM_OP_MEM
                    and insn.operands[1].mem.base == A.ARM_REG_PC and insn.operands[1].mem.index):
                t = insn.address + 8
                end = self.func.addr + max(self.func.size, 4)
                while t < end and t not in aset:
                    w = self.elf.read32(t)
                    if w in aset:
                        leaders.add(w)
                        self.unknown_entry.add(w)
                    t += 4
        self.leaders = sorted(l for l in leaders if l in aset)
        # predecessor map
        self.preds = {l: set() for l in self.leaders}
        lset = set(self.leaders)
        block_of = {}
        cur = None
        for idx, insn in enumerate(self.insns):
            if insn.address in lset:
                cur = insn.address
            block_of[insn.address] = cur
        self.block_of = block_of
        self.blocks = {l: [] for l in self.leaders}
        for insn in self.insns:
            self.blocks[block_of[insn.address]].append(insn)
        jt_targets_unknown = False
        for l, ins in self.blocks.items():
            last = ins[-1]
            idx = addrs.index(last.address)
            nxt = addrs[idx + 1] if idx + 1 < len(addrs) else None
            falls = ((last.address in self.indirect_calls or not _is_terminator(last))
                     and nxt == last.address + 4)
            if last.id == A.ARM_INS_B:
                t = self.elf.branch_target(last)
                if t in lset:
                    self.preds[t].add(l)
            if last.address in self.indirect_calls:
                pass
            elif _writes_pc_indirect(last) and last.cc == A.ARM_CC_AL and last.id in (A.ARM_INS_LDR, A.ARM_INS_ADD):
                # `ldr pc, [pc, rX, lsl #2]` / `add pc, pc, rX, lsl #2` dispatch
                if any(op.type == A.ARM_OP_MEM and op.mem.base == A.ARM_REG_PC for op in last.operands) or last.id == A.ARM_INS_ADD:
                    jt_targets_unknown = True
            if _writes_pc_indirect(last) and last.cc != A.ARM_CC_AL and last.id in (A.ARM_INS_LDR, A.ARM_INS_ADD):
                if any(op.type == A.ARM_OP_MEM and op.mem.base == A.ARM_REG_PC and op.mem.index for op in last.operands) or last.id == A.ARM_INS_ADD:
                    jt_targets_unknown = True
            if falls and nxt in lset:
                self.preds[nxt].add(l)
        # blocks with no known predecessor (other than the entry) are reached
        # through untracked edges (jump tables, etc.): start them unknown.
        for l in self.leaders:
            if l != self.func.addr and not self.preds[l]:
                self.unknown_entry.add(l)
        if self.preds.get(self.func.addr):
            pass  # loops back to the entry: entry state is already "unknown"
        self.has_jump_table = jt_targets_unknown

    def _run(self):
        out_state = {}
        in_state = {}
        work = list(self.leaders)
        iters = 0
        while work and iters < 20000:
            iters += 1
            l = work.pop(0)
            if l == self.func.addr and not self.preds[l]:
                # function entry: r0-r3 hold the (symbolic) incoming arguments
                st = State(r={f"r{i}": ("arg", i) for i in range(4)})
            elif l == self.func.addr or l in self.unknown_entry:
                # entry that is also a loop head, or a block reached only
                # through untracked edges (jump table): start from nothing
                st = State()
            else:
                st = None
                for p in self.preds[l]:
                    if p in out_state:
                        st = State.meet(st, out_state[p])
                if st is None:
                    continue  # no predecessor computed yet
            in_state[l] = st
            cur = st.copy()
            for insn in self.blocks[l]:
                self.pre_state[insn.address] = cur.copy()
                self.step(cur, insn)
            old = out_state.get(l)
            if old is None or old.key() != cur.key():
                out_state[l] = cur
                for l2 in self.leaders:
                    if l in self.preds[l2] and l2 not in work:
                        work.append(l2)

    # ---- value helpers ----
    def rv(self, st: State, reg, insn):
        if reg == A.ARM_REG_PC:
            return K(insn.address + 8)
        name = insn.reg_name(reg)
        if name in ("sb", "sl", "fp", "ip"):
            name = {"sb": "r9", "sl": "r10", "fp": "r11", "ip": "r12"}[name]
        return st.r.get(name)

    @staticmethod
    def rname(insn, reg):
        name = insn.reg_name(reg)
        return {"sb": "r9", "sl": "r10", "fp": "r11", "ip": "r12"}.get(name, name)

    def op_value(self, st, insn, op):
        if op.type == A.ARM_OP_IMM:
            return K(op.imm)
        if op.type == A.ARM_OP_REG:
            v = self.rv(st, op.reg, insn)
            if op.shift.type == 0:
                return v
            if op.shift.type == A.ARM_SFT_LSL:
                return unop(v, lambda x: x << op.shift.value)
            if op.shift.type == A.ARM_SFT_LSR:
                return unop(v, lambda x: (x & 0xFFFFFFFF) >> op.shift.value)
            if op.shift.type == A.ARM_SFT_ASR:
                return unop(v, lambda x: ((x ^ 0x80000000) - 0x80000000) >> op.shift.value)
            return None
        return None

    def load(self, addr: int, size: int = 4):
        """Value of a `size`-byte load from static address `addr`."""
        elf = self.elf
        sec = elf.section_of(addr)
        if sec == ".got":
            rel = elf.got_reloc.get(addr)
            if rel is None:
                return None
            typ, name = rel
            if typ == R_ARM_RELATIVE:
                return K(elf.read32(addr))
            if typ in (R_ARM_GLOB_DAT, R_ARM_ABS32):
                return ("imp", name)
            return None
        if sec in (".rodata", ".text") and size == 4:
            return K(elf.read32(addr))
        if sec in (".rodata", ".text") and size in (1, 2):
            b = elf.read_bytes(addr, size)
            return K(int.from_bytes(b, "little"))
        if sec in (".data", ".bss", ".data.rel.ro"):
            return ("mem", addr)
        return None

    def mem_addr(self, st, insn, op):
        m = op.mem
        if m.base == 0:
            return None
        base = self.rv(st, m.base, insn)
        b = single(base)
        if b is None:
            return None
        if m.index:
            iv = single(self.rv(st, m.index, insn))
            if iv is None:
                return None
            if op.shift.type == A.ARM_SFT_LSL:
                iv = iv << op.shift.value
            elif op.shift.type:
                return None
            if op.subtracted:
                iv = -iv
            return (b + iv) & 0xFFFFFFFF
        return (b + m.disp) & 0xFFFFFFFF

    def setr(self, st, name, val, insn):
        if name == "pc":
            return
        if insn.cc != A.ARM_CC_AL:
            val = meet(st.r.get(name), val)
        if val is None:
            st.r.pop(name, None)
        else:
            st.r[name] = val

    def sets(self, st, name, val, insn):
        if insn.cc != A.ARM_CC_AL:
            val = meet(st.s.get(name), val)
        if val is None:
            st.s.pop(name, None)
        else:
            st.s[name] = val

    def clobber_written(self, st, insn):
        _, w = insn.regs_access()
        for reg in w:
            n = self.rname(insn, reg)
            if n == "sp":
                st.stk.clear()
            if n in st.r:
                st.r.pop(n)
            if n in st.s:
                st.s.pop(n)
            if n.startswith("d") and n[1:].isdigit():
                d = int(n[1:])
                st.s.pop(f"s{2 * d}", None)
                st.s.pop(f"s{2 * d + 1}", None)
            if n.startswith("q") and n[1:].isdigit():
                q = int(n[1:])
                for j in range(4):
                    st.s.pop(f"s{4 * q + j}", None)

    def step(self, st: State, insn):
        iid = insn.id
        ops = insn.operands
        mn = insn.mnemonic
        # calls
        if iid in (A.ARM_INS_BL, A.ARM_INS_BLX) or (iid == A.ARM_INS_MOV and len(ops) == 2 and ops[0].type == A.ARM_OP_REG and ops[0].reg == A.ARM_REG_LR and ops[1].type == A.ARM_OP_REG and ops[1].reg == A.ARM_REG_PC):
            if iid in (A.ARM_INS_BL, A.ARM_INS_BLX):
                for r in CALLER_SAVED_CORE:
                    st.r.pop(r, None)
                for s in CALLER_SAVED_S:
                    st.s.pop(s, None)
                st.stk.clear()
                return
            # `mov lr, pc` precedes `ldr pc, [...]` (indirect call); treat the
            # following ldr pc as the call. Nothing to do here.
            st.r.pop("lr", None)
            return
        if _writes_pc_indirect(insn) and iid == A.ARM_INS_LDR:
            # indirect call via `mov lr,pc; ldr pc,[..]` or a jump
            for r in CALLER_SAVED_CORE:
                st.r.pop(r, None)
            for s in CALLER_SAVED_S:
                st.s.pop(s, None)
            st.stk.clear()
            return
        # data processing / moves
        if iid in (A.ARM_INS_MOV, A.ARM_INS_MOVW) and len(ops) == 2 and ops[0].type == A.ARM_OP_REG and not insn.update_flags:
            if ops[1].type == A.ARM_OP_FP:
                self.clobber_written(st, insn)
                return
            self.setr(st, self.rname(insn, ops[0].reg), self.op_value(st, insn, ops[1]), insn)
            return
        if iid == A.ARM_INS_MOVT and len(ops) == 2:
            dst = self.rname(insn, ops[0].reg)
            old = st.r.get(dst)
            self.setr(st, dst, unop(old, lambda x: (x & 0xFFFF) | ((ops[1].imm & 0xFFFF) << 16)), insn)
            return
        if iid == A.ARM_INS_MVN and len(ops) == 2 and not insn.update_flags:
            self.setr(st, self.rname(insn, ops[0].reg), unop(self.op_value(st, insn, ops[1]), lambda x: ~x), insn)
            return
        arith = {A.ARM_INS_ADD: lambda x, y: x + y, A.ARM_INS_SUB: lambda x, y: x - y,
                 A.ARM_INS_RSB: lambda x, y: y - x, A.ARM_INS_ORR: lambda x, y: x | y,
                 A.ARM_INS_AND: lambda x, y: x & y, A.ARM_INS_EOR: lambda x, y: x ^ y,
                 A.ARM_INS_BIC: lambda x, y: x & ~y}
        if iid in arith and len(ops) == 3 and ops[0].type == A.ARM_OP_REG:
            dst = self.rname(insn, ops[0].reg)
            a = self.op_value(st, insn, ops[1])
            b = self.op_value(st, insn, ops[2])
            if dst == "sp":
                st.stk.clear()
                return
            self.setr(st, dst, binop(a, b, arith[iid]), insn)
            return
        if iid in (A.ARM_INS_LSL, A.ARM_INS_LSR, A.ARM_INS_ASR) and len(ops) == 3:
            dst = self.rname(insn, ops[0].reg)
            a = self.op_value(st, insn, ops[1])
            b = self.op_value(st, insn, ops[2])
            fn = {A.ARM_INS_LSL: lambda x, y: x << y, A.ARM_INS_LSR: lambda x, y: x >> y,
                  A.ARM_INS_ASR: lambda x, y: ((x ^ 0x80000000) - 0x80000000) >> y}[iid]
            self.setr(st, dst, binop(a, b, fn), insn)
            return
        # loads
        if iid in (A.ARM_INS_LDR, A.ARM_INS_LDRB, A.ARM_INS_LDRH) and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM:
            dst = self.rname(insn, ops[0].reg)
            base = self.rname(insn, ops[1].mem.base)
            addr = self.mem_addr(st, insn, ops[1]) if not (insn.writeback and False) else None
            size = {A.ARM_INS_LDR: 4, A.ARM_INS_LDRB: 1, A.ARM_INS_LDRH: 2}[iid]
            if base == "sp" and not ops[1].mem.index:
                val = st.stk.get(ops[1].mem.disp) if size == 4 else None
            else:
                val = self.load(addr, size) if addr is not None else None
            # post-indexed / writeback: base register changes
            self.clobber_written(st, insn)
            self.setr(st, dst, val, insn)
            return
        if iid == A.ARM_INS_VLDR and len(ops) == 2:
            dst = self.rname(insn, ops[0].reg)
            addr = self.mem_addr(st, insn, ops[1])
            if dst.startswith("s"):
                if self.rname(insn, ops[1].mem.base) == "sp":
                    val = st.stk.get(ops[1].mem.disp)
                else:
                    val = self.load(addr, 4) if addr is not None else None
                self.sets(st, dst, val, insn)
            else:
                self.clobber_written(st, insn)
                if dst.startswith("d") and addr is not None and self.elf.section_of(addr) in (".rodata", ".text"):
                    d = int(dst[1:])
                    self.sets(st, f"s{2 * d}", K(self.elf.read32(addr)), insn)
                    self.sets(st, f"s{2 * d + 1}", K(self.elf.read32(addr + 4)), insn)
            return
        if iid == A.ARM_INS_VMOV:
            names = [self.rname(insn, o.reg) if o.type == A.ARM_OP_REG else None for o in ops]
            if len(ops) == 2 and ops[1].type == A.ARM_OP_FP and names[0] and names[0].startswith("s"):
                bits = struct.unpack("<I", struct.pack("<f", ops[1].fp))[0]
                self.sets(st, names[0], K(bits), insn)
                return
            if len(ops) == 2 and names[0] and names[1]:
                d, s = names
                if d.startswith("r") and s.startswith("s"):
                    self.setr(st, d, st.s.get(s), insn)
                    return
                if d.startswith("s") and s.startswith("r"):
                    self.sets(st, d, st.r.get(s), insn)
                    return
                if d.startswith("s") and s.startswith("s"):
                    self.sets(st, d, st.s.get(s), insn)
                    return
            if len(ops) == 3 and all(names) and names[2].startswith("d") and names[0].startswith("r"):
                dd = int(names[2][1:])
                self.setr(st, names[0], st.s.get(f"s{2 * dd}"), insn)
                self.setr(st, names[1], st.s.get(f"s{2 * dd + 1}"), insn)
                return
            if len(ops) == 3 and all(names) and names[0].startswith("d"):
                dd = int(names[0][1:])
                self.sets(st, f"s{2 * dd}", st.r.get(names[1]), insn)
                self.sets(st, f"s{2 * dd + 1}", st.r.get(names[2]), insn)
                return
            self.clobber_written(st, insn)
            return
        # stores to outgoing stack slots
        if iid == A.ARM_INS_STR and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM and self.rname(insn, ops[1].mem.base) == "sp" and not ops[1].mem.index and not insn.writeback:
            src = self.rname(insn, ops[0].reg)
            v = st.r.get(src)
            if v is None:
                st.stk.pop(ops[1].mem.disp, None)
            else:
                st.stk[ops[1].mem.disp] = v
            return
        if iid == A.ARM_INS_VSTR and len(ops) == 2 and self.rname(insn, ops[1].mem.base) == "sp":
            src = self.rname(insn, ops[0].reg)
            if src.startswith("s"):
                v = st.s.get(src)
                if v is None:
                    st.stk.pop(ops[1].mem.disp, None)
                else:
                    st.stk[ops[1].mem.disp] = v
            else:
                st.stk.pop(ops[1].mem.disp, None)
                st.stk.pop(ops[1].mem.disp + 4, None)
            return
        if iid == A.ARM_INS_STM and ops and ops[0].type == A.ARM_OP_REG and self.rname(insn, ops[0].reg) == "sp" and not insn.writeback:
            for j, o in enumerate(ops[1:]):
                v = st.r.get(self.rname(insn, o.reg))
                if v is None:
                    st.stk.pop(4 * j, None)
                else:
                    st.stk[4 * j] = v
            return
        if iid == A.ARM_INS_STRD and ops and ops[-1].type == A.ARM_OP_MEM and self.rname(insn, ops[-1].mem.base) == "sp" and not insn.writeback:
            base = ops[-1].mem.disp
            for j, o in enumerate(ops[:2]):
                v = st.r.get(self.rname(insn, o.reg))
                if v is None:
                    st.stk.pop(base + 4 * j, None)
                else:
                    st.stk[base + 4 * j] = v
            return
        # default: anything written becomes unknown
        self.clobber_written(st, insn)


def fmt_value(elf: ArmElf, v, as_float=False):
    """Human/JSON description of a lattice value."""
    if v is None:
        return {"kind": "unresolved"}
    if v[0] == "k":
        vals = sorted(v[1])
        d = {"kind": "const", "values": [f"0x{x:x}" for x in vals]}
        if as_float:
            d["float"] = [f32(x) for x in vals]
        return d
    if v[0] == "mem":
        return {"kind": "global_load", "addr": f"0x{v[1]:x}", "symbol": elf.describe_addr(v[1])}
    if v[0] == "imp":
        return {"kind": "import_addr", "symbol": v[1]}
    if v[0] == "arg":
        return {"kind": "caller_arg", "arg_index": v[1]}
    return {"kind": "unresolved"}

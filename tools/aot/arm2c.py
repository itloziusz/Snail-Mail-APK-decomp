#!/usr/bin/env python3
"""Static ahead-of-time translator: original ARM32 libsnailmail.so -> portable C.

Every instruction of every function in the original binary is translated, at
build time, into C statements that operate on an explicit guest CPU state and
a 32-bit guest address space (aot/runtime/aot_rt.h). The C is then compiled by
an ordinary compiler for the target (AArch64 on devices). Nothing is decoded or
translated at run time: indirect branches select among already-compiled C
functions through a static table (aot_dispatch), and an address that is not in
that table is a fatal diagnostic, never interpreted.

Semantics follow the ARM Architecture Reference Manual (ARMv7-A, ARM state):
integer instructions are decoded from their raw encodings here (so shifter
carry-out, flag rules and addressing modes are exact); VFPv2 instructions are
decoded with capstone and lowered to single/double IEEE operations with two
roundings for VMLA/VMLS/VNMLS (build with -ffp-contract=off).

Usage:
  arm2c.py --elf work/apk_unzip/lib/armeabi-v7a/libsnailmail.so --out aot/generated
Outputs (gitignored; they embed the original's code semantics and data):
  aot_funcs_NN.c    translated functions
  aot_table.c       dispatch table, import table, image bytes, relocations
  aot_manifest.json per-function translation report (unsupported sites etc.)
"""
import argparse
import bisect
import hashlib
import json
import os
import re
import struct
import sys

import capstone
from capstone import arm as csarm
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

GUEST_BASE = 0x00100000     # guest address of the image's vaddr 0 (aot_rt.h AOT_B)
IMPORT_THUNK_BASE = 0x00010000
FUNCS_PER_FILE = 40

COND = ["EQ", "NE", "CS", "CC", "MI", "PL", "VS", "VC", "HI", "LS", "GE", "LT", "GT", "LE", "AL", "NV"]
COND_C = {
    0: "Z", 1: "!Z", 2: "C", 3: "!C", 4: "N", 5: "!N", 6: "V", 7: "!V",
    8: "(C && !Z)", 9: "(!C || Z)", 10: "(N == V)", 11: "(N != V)",
    12: "(!Z && N == V)", 13: "(Z || N != V)",
}
REG = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc"]


class TranslationError(Exception):
    pass


def sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def ror32(v, n):
    n &= 31
    return ((v >> n) | (v << (32 - n))) & 0xFFFFFFFF if n else v


def hx(v):
    return "0x%08xu" % (v & 0xFFFFFFFF)


class Image:
    def __init__(self, path):
        self.path = path
        self.raw = open(path, "rb").read()
        self.sha256 = hashlib.sha256(self.raw).hexdigest()
        self.elf = ELFFile(open(path, "rb"))
        e = self.elf
        if e["e_machine"] != "EM_ARM" or e.elfclass != 32:
            raise SystemExit("expected ELF32 ARM")
        self.segments = [s for s in e.iter_segments() if s["p_type"] == "PT_LOAD"]
        text = e.get_section_by_name(".text")
        self.text_lo, self.text_hi = text["sh_addr"], text["sh_addr"] + text["sh_size"]
        plt = e.get_section_by_name(".plt")
        self.plt_lo, self.plt_hi = plt["sh_addr"], plt["sh_addr"] + plt["sh_size"]
        self.mem_hi = max(s["p_vaddr"] + s["p_memsz"] for s in self.segments)
        self._syms()
        self._relocs()
        self._plt()

    def read32(self, addr):
        for s in self.segments:
            if s["p_vaddr"] <= addr < s["p_vaddr"] + s["p_filesz"]:
                off = s["p_offset"] + addr - s["p_vaddr"]
                return struct.unpack_from("<I", self.raw, off)[0]
        raise TranslationError("read32 outside file-backed image: %#x" % addr)

    def _syms(self):
        st = self.elf.get_section_by_name(".symtab")
        self.maps = []
        funcs = {}
        self.objects = {}
        for s in st.iter_symbols():
            n, v = s.name, s["st_value"]
            if n in ("$a", "$d", "$t") or n.startswith(("$a.", "$d.", "$t.")):
                self.maps.append((v, n[:2]))
                continue
            t = s["st_info"]["type"]
            if t == "STT_FUNC" and s["st_shndx"] != "SHN_UNDEF":
                if v & 1:
                    raise SystemExit("Thumb function symbol %s" % n)
                funcs.setdefault(v, []).append((n, s["st_size"]))
            elif t == "STT_OBJECT" and s["st_shndx"] != "SHN_UNDEF":
                self.objects[n] = (v, s["st_size"])
        self.maps.sort()
        self.map_addrs = [a for a, _ in self.maps]
        self.funcs = []
        for v in sorted(funcs):
            names = sorted(funcs[v], key=lambda x: (-x[1], x[0]))
            size = max(x[1] for x in names)
            self.funcs.append({"addr": v, "size": size, "name": names[0][0],
                               "aliases": [x[0] for x in names[1:]]})
        # fill zero sizes up to the next function start
        for i, f in enumerate(self.funcs):
            nxt = self.funcs[i + 1]["addr"] if i + 1 < len(self.funcs) else self.text_hi
            if f["size"] == 0:
                f["size"] = nxt - f["addr"]
            f["end"] = min(f["addr"] + f["size"], nxt) if f["size"] else nxt
        self.func_starts = [f["addr"] for f in self.funcs]
        self.func_by_addr = {f["addr"]: f for f in self.funcs}

    def is_code(self, addr):
        i = bisect.bisect_right(self.map_addrs, addr) - 1
        return i >= 0 and self.maps[i][1] == "$a"

    def func_containing(self, addr):
        i = bisect.bisect_right(self.func_starts, addr) - 1
        if i >= 0:
            f = self.funcs[i]
            if f["addr"] <= addr < f["end"]:
                return f
        return None

    def _relocs(self):
        self.relocs = []  # (offset, type, symname, symvalue, is_undef)
        dynsym = self.elf.get_section_by_name(".dynsym")
        for sec in self.elf.iter_sections():
            if not isinstance(sec, RelocationSection) or not sec.name.startswith(".rel"):
                continue
            if sec.name not in (".rel.dyn", ".rel.plt"):
                continue
            for r in sec.iter_relocations():
                t = r["r_info_type"]
                si = r["r_info_sym"]
                sym = dynsym.get_symbol(si) if si else None
                self.relocs.append((r["r_offset"], t, sym.name if sym else "",
                                    sym["st_value"] if sym else 0,
                                    bool(sym) and sym["st_shndx"] == "SHN_UNDEF"))
        self.imports = sorted({n for (_, _, n, _, u) in self.relocs if u and n})

    def _plt(self):
        # ARM PLT entry: add ip, pc, #X ; add ip, ip, #Y ; ldr pc, [ip, #Z]! -> GOT slot
        got2name = {}
        for off, t, n, _, u in self.relocs:
            if t == 22:  # R_ARM_JUMP_SLOT
                got2name[off] = n
        self.plt = {}
        a = self.plt_lo + 20
        md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
        while a + 12 <= self.plt_hi:
            w = [self.read32(a + 4 * i) for i in range(3)]
            ip = a + 8
            for k in range(2):
                rot = (w[k] >> 8) & 0xF
                ip = (ip + ror32(w[k] & 0xFF, 2 * rot)) & 0xFFFFFFFF
            ldr_off = w[2] & 0xFFF
            slot = ip + ldr_off
            if slot not in got2name:
                raise SystemExit("PLT entry %#x -> unknown GOT slot %#x" % (a, slot))
            self.plt[a] = got2name[slot]
            a += 12


class Emitter:
    """Translates one function into C."""

    def __init__(self, img, func, md, report):
        self.img, self.f, self.md, self.report = img, func, md, report
        self.lines = []
        self.labels = set()
        self.uses_vfp = False
        self.insns = []  # (addr, word, capstone insn or None)
        self.unsupported = []

    # ---------------------------------------------------------------- helpers
    def R(self, n, a):
        return hx(GUEST_BASE + a + 8) if n == 15 else REG[n]

    def emit(self, s):
        self.lines.append(s)

    def fail(self, a, msg):
        self.unsupported.append({"addr": hex(a), "why": msg})
        self.emit('    aot_unsupported(c, %s, "%s");' % (hx(GUEST_BASE + a), msg.replace('"', "'")))

    def target_kind(self, t):
        if self.f["addr"] <= t < self.f["end"]:
            return "intra"
        if t in self.img.func_by_addr:
            return "func"
        if t in self.img.plt:
            return "import"
        return "bad"

    def fname(self, t):
        return "F_%08x" % t

    def call_direct(self, t, a):
        k = self.target_kind(t) if t != self.f["addr"] else "func"
        if k == "func":
            return "AOT_CALL(%s);" % self.fname(t)
        if k == "import":
            return "AOT_CALL(aot_imp_%s);" % self.img.plt[t]
        raise TranslationError("direct call to non-function %#x from %#x" % (t, a))

    # ------------------------------------------------------------ first pass
    def collect(self):
        f = self.f
        a = f["addr"]
        while a < f["end"]:
            if not self.img.is_code(a):
                a += 4
                continue
            w = self.img.read32(a)
            self.insns.append((a, w))
            a += 4
        # branch-target labels
        for a, w in self.insns:
            cond = w >> 28
            if cond != 0xF and (w >> 25) & 7 == 5:
                t = (a + 8 + (sx(w & 0xFFFFFF, 24) << 2)) & 0xFFFFFFFF
                if not (w >> 24) & 1 and self.target_kind(t) == "intra":
                    self.labels.add(t)
            # switch: addls pc, pc, rX, lsl #2
            if self.is_add_pc_table(w):
                for t in self.table_targets(a):
                    self.labels.add(t)

    @staticmethod
    def is_add_pc_table(w):
        # cond 000 0100 0 1111 1111 00010 00 0 Rm  (add pc, pc, Rm, lsl #2)
        return (w & 0x0FFFFFF0) == 0x008FF100

    def table_targets(self, a):
        out = []
        t = a + 8
        while t < self.f["end"] and self.img.is_code(t):
            w = self.img.read32(t)
            if (w & 0x0F000000) != 0x0A000000 or (w >> 28) != 0xE:  # unconditional B
                break
            out.append(t)
            t += 4
        if not out:
            raise TranslationError("empty jump table at %#x" % a)
        return out

    # ------------------------------------------------------------ translation
    def translate(self):
        self.collect()
        f = self.f
        body = []
        self.lines = body
        prev_w = None
        for idx, (a, w) in enumerate(self.insns):
            if a in self.labels:
                self.emit("L_%08x:" % a)
            self.emit("    /* %08x: %08x */" % (a, w))
            try:
                self.insn(a, w, prev_w)
            except TranslationError as e:
                self.fail(a, str(e))
            prev_w = w
        self.emit("    aot_fell_off_end(c, %s);" % hx(GUEST_BASE + f["end"]))
        addrs = {a for a, _ in self.insns}
        missing = sorted(t for t in self.labels if t not in addrs)
        if missing:
            raise TranslationError("branch targets not on instructions: %s" % [hex(m) for m in missing])
        head = []
        head.append("#undef AOT_SAVE\n#undef AOT_LOAD")
        if self.uses_vfp:
            head.append("#define AOT_SAVE AOT_SAVE_INT AOT_SAVE_VFP\n#define AOT_LOAD AOT_LOAD_INT AOT_LOAD_VFP")
        else:
            head.append("#define AOT_SAVE AOT_SAVE_INT\n#define AOT_LOAD AOT_LOAD_INT")
        head.append("/* %s%s @ v7a:%#x size %d */" % (
            f["name"], (" aka " + ", ".join(f["aliases"])) if f["aliases"] else "", f["addr"], f["size"]))
        head.append("void %s(aot_cpu *c)\n{" % self.fname(f["addr"]))
        head.append("    AOT_LOCALS%s" % ("_VFP" if self.uses_vfp else ""))
        head.append("    const uint32_t entry_lr = lr;")
        head.append("    (void)entry_lr;")
        head.append("    AOT_TRACE(%s);" % hx(GUEST_BASE + f["addr"]))
        return "\n".join(head + body + ["}"])

    def wrap_cond(self, cond, stmts):
        if cond == 14:
            for s in stmts:
                self.emit("    " + s)
        else:
            self.emit("    if (%s) {" % COND_C[cond])
            for s in stmts:
                self.emit("        " + s)
            self.emit("    }")

    def insn(self, a, w, prev_w):
        cond = w >> 28
        if cond == 0xF:
            return self.uncond(a, w)
        op1 = (w >> 25) & 7
        if op1 == 5:
            return self.branch(a, w, cond)
        if op1 == 4:
            return self.ldm_stm(a, w, cond)
        if op1 in (2, 3):
            if op1 == 3 and (w >> 4) & 1:
                return self.media(a, w, cond)
            return self.ldr_str(a, w, cond, prev_w)
        if op1 in (0, 1):
            return self.dp_misc(a, w, cond, prev_w)
        if op1 in (6, 7):
            if op1 == 7 and (w >> 24) & 1:
                raise TranslationError("SVC")
            return self.vfp(a, w, cond)
        raise TranslationError("unhandled encoding")

    def uncond(self, a, w):
        # PLD (hint) -> no-op; everything else unsupported (only libgcc unwinder has LDC2/STC2)
        if (w & 0xFD70F000) == 0xF550F000:
            self.emit("    /* pld */")
            return
        raise TranslationError("unconditional-space instruction %08x" % w)

    # ------------------------------------------------------------ PC writes
    def pc_write(self, a, texpr, cond_pre=None, is_call=False):
        """Statements for a runtime PC write with target expression texpr."""
        if is_call:
            return ["{ uint32_t t_ = %s; AOT_CALL_INDIRECT(t_, %s); }" % (texpr, hx(GUEST_BASE + a))]
        return ["{ uint32_t t_ = %s; AOT_JUMP(t_, %s); }" % (texpr, hx(GUEST_BASE + a))]

    def is_call_after_mov_lr_pc(self, prev_w, cond):
        # mov lr, pc (e1a0e00f) immediately before, same or AL condition
        return prev_w is not None and (prev_w & 0x0FFFFFFF) == 0x01A0E00F and (
            (prev_w >> 28) == 0xE or (prev_w >> 28) == cond)

    # ------------------------------------------------------------ branches
    def branch(self, a, w, cond):
        link = (w >> 24) & 1
        t = (a + 8 + (sx(w & 0xFFFFFF, 24) << 2)) & 0xFFFFFFFF
        if link:
            stmts = ["lr = %s;" % hx(GUEST_BASE + a + 4), self.call_direct(t, a)]
            return self.wrap_cond(cond, stmts)
        k = self.target_kind(t)
        if k == "intra":
            return self.wrap_cond(cond, ["goto L_%08x;" % t])
        if k == "func":
            return self.wrap_cond(cond, ["AOT_TAILCALL(%s);" % self.fname(t)])
        if k == "import":
            return self.wrap_cond(cond, ["AOT_TAILCALL(aot_imp_%s);" % self.img.plt[t]])
        raise TranslationError("branch to unknown target %#x" % t)

    # ------------------------------------------------------------ data processing
    def shifter(self, a, w, need_carry):
        """Return (value_expr, carry_expr, pre_stmts)."""
        if (w >> 25) & 1:  # immediate
            rot = (w >> 8) & 0xF
            v = ror32(w & 0xFF, 2 * rot)
            carry = "C" if rot == 0 else str((v >> 31) & 1)
            return hx(v), carry, []
        rm = w & 0xF
        typ = (w >> 5) & 3
        if (w >> 4) & 1:  # register-shifted register
            rs = (w >> 8) & 0xF
            if rm == 15 or rs == 15:
                raise TranslationError("register-shifted operand uses PC")
            pre = ["uint32_t sc_ = C;",
                   "uint32_t sv_ = aot_shift_reg(%d, %s, %s & 0xffu, &sc_);" % (typ, REG[rm], REG[rs])]
            return "sv_", "sc_", pre
        imm5 = (w >> 7) & 0x1F
        rv = self.R(rm, a)
        if typ == 0:
            if imm5 == 0:
                return rv, "C", []
            return "(%s << %d)" % (rv, imm5), "((%s >> %d) & 1u)" % (rv, 32 - imm5), []
        if typ == 1:
            if imm5 == 0:
                return "0u", "(%s >> 31)" % rv, []
            return "(%s >> %d)" % (rv, imm5), "((%s >> %d) & 1u)" % (rv, imm5 - 1), []
        if typ == 2:
            n = 32 if imm5 == 0 else imm5
            if n == 32:
                return "(uint32_t)((int32_t)%s >> 31)" % rv, "(%s >> 31)" % rv, []
            return "(uint32_t)((int32_t)%s >> %d)" % (rv, n), "((%s >> %d) & 1u)" % (rv, n - 1), []
        if imm5 == 0:  # RRX
            return "((C << 31) | (%s >> 1))" % rv, "(%s & 1u)" % rv, []
        return "aot_ror(%s, %d)" % (rv, imm5), "((%s >> %d) & 1u)" % (rv, imm5 - 1), []

    def dp_misc(self, a, w, cond, prev_w):
        op = (w >> 21) & 0xF
        s = (w >> 20) & 1
        I = (w >> 25) & 1
        # --- multiply / extra load-store / misc encodings in the 000 space
        if not I:
            if (w & 0x0F0000F0) == 0x00000090:
                return self.multiply(a, w, cond)
            if (w & 0x0F8000F0) == 0x00800090:
                return self.multiply(a, w, cond)
            if (w & 0x0E000090) == 0x00000090 and (w & 0x60) != 0:
                return self.extra_ldst(a, w, cond)
            if (w & 0x0F900000) == 0x01000000:  # misc (op 10xx with S=0)
                return self.misc(a, w, cond, prev_w)
        else:
            if (w & 0x0FF00000) == 0x03000000:  # MOVW
                rd = (w >> 12) & 0xF
                v = ((w >> 4) & 0xF000) | (w & 0xFFF)
                return self.wrap_cond(cond, ["%s = %s;" % (REG[rd], hx(v))])
            if (w & 0x0FF00000) == 0x03400000:  # MOVT
                rd = (w >> 12) & 0xF
                v = ((w >> 4) & 0xF000) | (w & 0xFFF)
                return self.wrap_cond(cond, ["%s = (%s & 0xffffu) | %s;" % (REG[rd], REG[rd], hx(v << 16))])
            if (w & 0x0FB00000) == 0x03200000:  # MSR imm / hints
                if (w & 0x0FFFFF00) == 0x0320F000:
                    self.emit("    /* hint %d */" % (w & 0xFF))
                    return
                raise TranslationError("MSR immediate")
        rn = (w >> 16) & 0xF
        rd = (w >> 12) & 0xF
        logical = op in (0, 1, 8, 9, 12, 13, 14, 15)
        need_carry = bool(s) and logical
        val, carry, pre = self.shifter(a, w, need_carry)
        rnv = self.R(rn, a)
        st = list(pre)
        res = "res_"
        if op in (0, 8):
            st.append("uint32_t res_ = %s & %s;" % (rnv, val))
        elif op in (1, 9):
            st.append("uint32_t res_ = %s ^ %s;" % (rnv, val))
        elif op == 12:
            st.append("uint32_t res_ = %s | %s;" % (rnv, val))
        elif op == 13:
            st.append("uint32_t res_ = %s;" % val)
        elif op == 14:
            st.append("uint32_t res_ = %s & ~%s;" % (rnv, val))
        elif op == 15:
            st.append("uint32_t res_ = ~%s;" % val)
        else:
            # arithmetic: a + b + cin  (sub = a + ~b + 1)
            A, B, CIN = {
                2: (rnv, "~(uint32_t)(%s)" % val, "1u"),
                3: (val, "~(uint32_t)(%s)" % rnv, "1u"),
                4: (rnv, val, "0u"),
                5: (rnv, val, "C"),
                6: (rnv, "~(uint32_t)(%s)" % val, "C"),
                7: (val, "~(uint32_t)(%s)" % rnv, "C"),
                10: (rnv, "~(uint32_t)(%s)" % val, "1u"),
                11: (rnv, val, "0u"),
            }[op]
            if s:
                st.append("uint32_t res_ = aot_addc(%s, %s, %s, &N, &Z, &C, &V);" % (A, B, CIN))
            else:
                if CIN == "0u":
                    st.append("uint32_t res_ = %s + %s;" % (A, B))
                else:
                    st.append("uint32_t res_ = %s + %s + %s;" % (A, B, CIN))
        if s and logical:
            st.append("N = res_ >> 31; Z = (res_ == 0);")
            if carry != "C":
                st.append("C = %s;" % carry)
        if op in (8, 9, 10, 11):  # TST TEQ CMP CMN
            if not s:
                raise TranslationError("compare without S")
            st.append("(void)res_;")
            return self.wrap_cond(cond, st)
        if rd == 15:
            if s:
                raise TranslationError("flag-setting PC write (exception return)")
            if self.is_add_pc_table(w):
                targets = self.table_targets(a)
                st2 = st + ["switch (res_ - AOT_B) {"]
                for t in targets:
                    st2.append("    case %s: goto L_%08x;" % (hx(t), t))
                st2.append("    default: aot_bad_jump(c, %s, res_);" % hx(GUEST_BASE + a))
                st2.append("}")
                return self.wrap_cond(cond, st2)
            is_call = self.is_call_after_mov_lr_pc(prev_w, cond)
            return self.wrap_cond(cond, st + self.pc_write(a, "res_", is_call=is_call))
        st.append("%s = res_;" % REG[rd])
        return self.wrap_cond(cond, st)

    def misc(self, a, w, cond, prev_w):
        op2 = (w >> 4) & 0xF
        op = (w >> 21) & 3
        rm = w & 0xF
        if op2 == 1 and op == 1:  # BX
            if rm == 14:
                return self.wrap_cond(cond, ["AOT_RETURN_TO(lr, %s);" % hx(GUEST_BASE + a)])
            is_call = self.is_call_after_mov_lr_pc(prev_w, cond)
            return self.wrap_cond(cond, self.pc_write(a, REG[rm], is_call=is_call))
        if op2 == 3 and op == 1:  # BLX reg
            return self.wrap_cond(cond, ["{ uint32_t t_ = %s; lr = %s; AOT_CALL_INDIRECT(t_, %s); }" % (
                REG[rm], hx(GUEST_BASE + a + 4), hx(GUEST_BASE + a))])
        if op2 == 1 and op == 3:  # CLZ
            rd = (w >> 12) & 0xF
            return self.wrap_cond(cond, ["%s = aot_clz(%s);" % (REG[rd], REG[rm])])
        raise TranslationError("misc instruction %08x" % w)

    def multiply(self, a, w, cond):
        op = (w >> 20) & 0xF
        s = op & 1
        rd = (w >> 16) & 0xF   # RdHi for long
        rn = (w >> 12) & 0xF   # Ra / RdLo
        rs = (w >> 8) & 0xF
        rm = w & 0xF
        top = (w >> 21) & 7
        st = []
        if top == 0:    # MUL
            st.append("uint32_t res_ = %s * %s;" % (REG[rm], REG[rs]))
            if s:
                st.append("N = res_ >> 31; Z = (res_ == 0);")
            st.append("%s = res_;" % REG[rd])
        elif top == 1:  # MLA
            st.append("uint32_t res_ = %s * %s + %s;" % (REG[rm], REG[rs], REG[rn]))
            if s:
                st.append("N = res_ >> 31; Z = (res_ == 0);")
            st.append("%s = res_;" % REG[rd])
        elif top == 3 and not s:  # MLS
            st.append("%s = %s - %s * %s;" % (REG[rd], REG[rn], REG[rm], REG[rs]))
        elif top in (4, 5, 6, 7):
            signed = top in (6, 7)
            acc = top in (5, 7)
            if signed:
                st.append("uint64_t p_ = (uint64_t)((int64_t)(int32_t)%s * (int64_t)(int32_t)%s);" % (REG[rm], REG[rs]))
            else:
                st.append("uint64_t p_ = (uint64_t)%s * (uint64_t)%s;" % (REG[rm], REG[rs]))
            if acc:
                st.append("p_ += ((uint64_t)%s << 32) | %s;" % (REG[rd], REG[rn]))
            if s:
                st.append("N = (uint32_t)(p_ >> 63); Z = (p_ == 0);")
            st.append("%s = (uint32_t)p_; %s = (uint32_t)(p_ >> 32);" % (REG[rn], REG[rd]))
        else:
            raise TranslationError("multiply variant %08x" % w)
        return self.wrap_cond(cond, st)

    # ------------------------------------------------------------ load/store
    def addr_mode(self, a, w, off_expr):
        P = (w >> 24) & 1
        U = (w >> 23) & 1
        W = (w >> 21) & 1
        rn = (w >> 16) & 0xF
        base = self.R(rn, a)
        sign = "+" if U else "-"
        ea = "(%s %s %s)" % (base, sign, off_expr)
        addr = ea if P else base
        wb = (not P) or W
        if wb and rn == 15:
            raise TranslationError("writeback to PC base")
        return addr, (("%s = %s;" % (REG[rn], ea)) if wb else None), rn

    def ldr_str(self, a, w, cond, prev_w):
        I = (w >> 25) & 1
        B = (w >> 22) & 1
        L = (w >> 20) & 1
        rt = (w >> 12) & 0xF
        P = (w >> 24) & 1
        W = (w >> 21) & 1
        if not P and W:
            raise TranslationError("LDRT/STRT")
        if I:
            rm = w & 0xF
            typ = (w >> 5) & 3
            imm5 = (w >> 7) & 0x1F
            rv = self.R(rm, a)
            if typ == 0:
                off = "(%s << %d)" % (rv, imm5) if imm5 else rv
            elif typ == 1:
                off = "(%s >> %d)" % (rv, imm5) if imm5 else "0u"
            elif typ == 2:
                off = "(uint32_t)((int32_t)%s >> %d)" % (rv, imm5 if imm5 else 31)
            else:
                off = "aot_ror(%s, %d)" % (rv, imm5) if imm5 else "((C << 31) | (%s >> 1))" % rv
        else:
            off = hx(w & 0xFFF)
        addr, wb, rn = self.addr_mode(a, w, off)
        st = ["uint32_t ea_ = %s;" % addr]
        if L:
            if rt == 15:
                if B:
                    raise TranslationError("LDRB to PC")
                st.append("uint32_t v_ = AOT_LD32(ea_);")
                if wb:
                    st.append(wb)
                # switch table: ldrls pc, [pc, rX, lsl #2]
                is_call = self.is_call_after_mov_lr_pc(prev_w, cond)
                st += self.pc_write(a, "v_", is_call=is_call)
                return self.wrap_cond(cond, st)
            st.append("uint32_t v_ = %s;" % ("AOT_LD8(ea_)" if B else "AOT_LD32(ea_)"))
            if wb:
                st.append(wb)
            st.append("%s = v_;" % REG[rt])
        else:
            if rt == 15:
                raise TranslationError("STR PC")
            st.append("%s(ea_, %s);" % ("AOT_ST8" if B else "AOT_ST32", REG[rt]))
            if wb:
                st.append(wb)
        return self.wrap_cond(cond, st)

    def extra_ldst(self, a, w, cond):
        P = (w >> 24) & 1
        W = (w >> 21) & 1
        L = (w >> 20) & 1
        op2 = (w >> 5) & 3
        rt = (w >> 12) & 0xF
        if (w >> 22) & 1:
            off = hx(((w >> 4) & 0xF0) | (w & 0xF))
        else:
            off = self.R(w & 0xF, a)
        if not P and W:
            raise TranslationError("unprivileged extra load/store")
        addr, wb, rn = self.addr_mode(a, w, off)
        st = ["uint32_t ea_ = %s;" % addr]
        if rt == 15:
            raise TranslationError("extra load/store with PC")
        if op2 == 1:  # H
            if L:
                st.append("uint32_t v_ = AOT_LD16(ea_);")
            else:
                st.append("AOT_ST16(ea_, %s);" % REG[rt])
        elif op2 == 2:
            if L:  # LDRSB
                st.append("uint32_t v_ = (uint32_t)(int32_t)(int8_t)AOT_LD8(ea_);")
            else:  # LDRD
                if rt & 1:
                    raise TranslationError("LDRD odd Rt")
                st.append("uint32_t v_ = AOT_LD32(ea_); uint32_t v2_ = AOT_LD32(ea_ + 4u);")
        else:
            if L:  # LDRSH
                st.append("uint32_t v_ = (uint32_t)(int32_t)(int16_t)AOT_LD16(ea_);")
            else:  # STRD
                if rt & 1:
                    raise TranslationError("STRD odd Rt")
                st.append("AOT_ST32(ea_, %s); AOT_ST32(ea_ + 4u, %s);" % (REG[rt], REG[rt + 1]))
        if wb:
            st.append(wb)
        if L or op2 == 2 and not L:
            if op2 == 2 and not L:
                st.append("%s = v_; %s = v2_;" % (REG[rt], REG[rt + 1]))
            else:
                st.append("%s = v_;" % REG[rt])
        return self.wrap_cond(cond, st)

    def ldm_stm(self, a, w, cond):
        P = (w >> 24) & 1
        U = (w >> 23) & 1
        S = (w >> 22) & 1
        W = (w >> 21) & 1
        L = (w >> 20) & 1
        rn = (w >> 16) & 0xF
        regs = [i for i in range(16) if (w >> i) & 1]
        n = len(regs)
        if S:
            raise TranslationError("LDM/STM with S bit")
        if rn == 15:
            raise TranslationError("LDM/STM with PC base")
        if not n:
            raise TranslationError("empty register list")
        st = ["uint32_t b_ = %s;" % REG[rn]]
        if U:
            start = "b_ + 4u" if P else "b_"
        else:
            start = "b_ - %du" % (4 * n) if P else "b_ - %du" % (4 * n - 4)
        st.append("uint32_t ea_ = %s;" % start)
        new_base = "b_ + %du" % (4 * n) if U else "b_ - %du" % (4 * n)
        if L:
            for i, r in enumerate(regs):
                st.append("uint32_t m%d_ = AOT_LD32(ea_ + %du);" % (r, 4 * i))
            if W and rn not in regs:
                st.append("%s = %s;" % (REG[rn], new_base))
            for r in regs:
                if r != 15:
                    st.append("%s = m%d_;" % (REG[r], r))
            if W and rn in regs:
                raise TranslationError("LDM writeback with base in list")
            if 15 in regs:
                st += self.pc_write(a, "m15_")
        else:
            for i, r in enumerate(regs):
                v = hx(GUEST_BASE + a + 8) if r == 15 else REG[r]
                st.append("AOT_ST32(ea_ + %du, %s);" % (4 * i, v))
            if W:
                st.append("%s = %s;" % (REG[rn], new_base))
        return self.wrap_cond(cond, st)

    # ------------------------------------------------------------ media
    def media(self, a, w, cond):
        rd = (w >> 12) & 0xF
        rn = (w >> 16) & 0xF
        rm = w & 0xF
        # extend: cond 0110 1 op 1 ... 0111 Rm
        if (w & 0x0F8003F0) == 0x06800070:
            op = (w >> 20) & 7
            rot = ((w >> 10) & 3) * 8
            src = "aot_ror(%s, %d)" % (REG[rm], rot) if rot else REG[rm]
            ext = {2: "(uint32_t)(int32_t)(int8_t)(%s)", 3: "(uint32_t)(int32_t)(int16_t)(%s)",
                   6: "((%s) & 0xffu)", 7: "((%s) & 0xffffu)"}.get(op)
            if ext is None:
                raise TranslationError("extend variant %d" % op)
            v = ext % src
            if rn != 15:
                v = "(%s + %s)" % (REG[rn], v)
            return self.wrap_cond(cond, ["%s = %s;" % (REG[rd], v)])
        if (w & 0x0FE00070) == 0x07C00010:  # BFC / BFI
            msb = (w >> 16) & 0x1F
            lsb = (w >> 7) & 0x1F
            if msb < lsb:
                raise TranslationError("BFC/BFI msb<lsb")
            width = msb - lsb + 1
            mask = (((1 << width) - 1) << lsb) & 0xFFFFFFFF
            if rm == 15:
                return self.wrap_cond(cond, ["%s &= %s;" % (REG[rd], hx(~mask))])
            return self.wrap_cond(cond, ["%s = (%s & %s) | ((%s << %d) & %s);" % (
                REG[rd], REG[rd], hx(~mask), REG[rm], lsb, hx(mask))])
        if (w & 0x0FA00070) == 0x07A00050:  # UBFX / SBFX
            widthm1 = (w >> 16) & 0x1F
            lsb = (w >> 7) & 0x1F
            width = widthm1 + 1
            if lsb + width > 32:
                raise TranslationError("bitfield out of range")
            unsigned = (w >> 22) & 1
            if unsigned:
                mask = (1 << width) - 1
                v = "((%s >> %d) & %s)" % (REG[rm], lsb, hx(mask))
            else:
                v = "(uint32_t)((int32_t)(%s << %d) >> %d)" % (REG[rm], 32 - lsb - width, 32 - width)
            return self.wrap_cond(cond, ["%s = %s;" % (REG[rd], v)])
        if (w & 0x0FFF0FF0) == 0x06BF0F30:  # REV
            return self.wrap_cond(cond, ["%s = aot_bswap32(%s);" % (REG[rd], REG[rm])])
        raise TranslationError("media instruction %08x" % w)

    # ------------------------------------------------------------ VFP
    def vfp_ldst_raw(self, a, w, cond):
        """VLDR/VSTR/VLDM/VSTM(VPUSH/VPOP) decoded from the encoding (ARM ARM A7.6)."""
        cp = (w >> 8) & 0xF
        if cp not in (10, 11):
            raise TranslationError("coprocessor %d load/store" % cp)
        P = (w >> 24) & 1
        U = (w >> 23) & 1
        D = (w >> 22) & 1
        W = (w >> 21) & 1
        L = (w >> 20) & 1
        rn = (w >> 16) & 0xF
        vd = (w >> 12) & 0xF
        imm8 = w & 0xFF
        dbl = cp == 11
        self.uses_vfp = True
        if dbl:
            first = (D << 4) | vd
            if first > 15:
                raise TranslationError("D16-D31")
        else:
            first = (vd << 1) | D
        base = hx(GUEST_BASE + a + 8) if rn == 15 else REG[rn]
        st = []
        if P == 1 and W == 0:  # VLDR / VSTR
            off = imm8 * 4
            st.append("uint32_t ea_ = %s %s %du;" % (base, "+" if U else "-", off))
            words = ["s%d" % (2 * first), "s%d" % (2 * first + 1)] if dbl else ["s%d" % first]
        else:
            if P == U:
                raise TranslationError("VLDM/VSTM addressing P==U")
            if dbl:
                if imm8 & 1:
                    raise TranslationError("FLDMX/FSTMX (unwinder)")
                n = imm8 // 2
                words = []
                for k in range(first, first + n):
                    words += ["s%d" % (2 * k), "s%d" % (2 * k + 1)]
            else:
                words = ["s%d" % k for k in range(first, first + imm8)]
            if not words or len(words) > 32:
                raise TranslationError("bad VFP register list")
            if rn == 15:
                raise TranslationError("VLDM/VSTM with PC base")
            nb = 4 * len(words)
            st.append("uint32_t b_ = %s;" % REG[rn])
            st.append("uint32_t ea_ = %s;" % ("b_" if U else "b_ - %du" % nb))
        for i, sreg in enumerate(words):
            if L:
                st.append("%s = AOT_LD32(ea_ + %du);" % (sreg, 4 * i))
            else:
                st.append("AOT_ST32(ea_ + %du, %s);" % (4 * i, sreg))
        if not (P == 1 and W == 0) and W:
            st.append("%s = %s;" % (REG[rn], "b_ + %du" % nb if U else "b_ - %du" % nb))
        return self.wrap_cond(cond, st)

    def vfp(self, a, w, cond):
        if (w >> 25) & 7 == 6:
            return self.vfp_ldst_raw(a, w, cond)
        code = struct.pack("<I", w)
        ins = list(self.md.disasm(code, a))
        if not ins:
            raise TranslationError("coprocessor instruction not decodable")
        ins = ins[0]
        name = ins.insn_name()
        mn = ins.mnemonic
        ops = ins.operands
        self.uses_vfp = True
        rn = self.md.reg_name

        def reg(o):
            return rn(o.reg)

        def is_s(r):
            return re.fullmatch(r"s\d+", r) is not None

        def is_d(r):
            return re.fullmatch(r"d\d+", r) is not None

        def core(r):
            m = {"sb": "r9", "sl": "r10", "fp": "r11", "ip": "r12"}.get(r, r)
            if m == "pc":
                raise TranslationError("VFP transfer with PC")
            return m

        def S(r):
            return "s%d" % int(r[1:])

        def Fv(r):  # float value expr
            return "aot_u2f(%s)" % S(r)

        def Dv(r):
            k = int(r[1:])
            if k > 15:
                raise TranslationError("D16-D31 (VFPv3-D32)")
            return "aot_u2d(s%d, s%d)" % (2 * k, 2 * k + 1)

        def setS(r, fexpr):
            return "%s = aot_f2u(%s);" % (S(r), fexpr)

        def setD(r, dexpr):
            k = int(r[1:])
            return "aot_d2u(%s, &s%d, &s%d);" % (dexpr, 2 * k, 2 * k + 1)

        dtype = ".f64" in mn
        st = []
        if name in ("vadd", "vsub", "vmul", "vdiv", "vnmul"):
            d, n, m = (reg(o) for o in ops)
            opc = {"vadd": "+", "vsub": "-", "vmul": "*", "vdiv": "/", "vnmul": "*"}[name]
            if dtype:
                e = "(%s %s %s)" % (Dv(n), opc, Dv(m))
                if name == "vnmul":
                    e = "(-%s)" % e
                st.append("{ double t_ = %s; %s }" % (e, setD(d, "t_")))
            else:
                e = "(%s %s %s)" % (Fv(n), opc, Fv(m))
                if name == "vnmul":
                    e = "(-%s)" % e
                st.append("{ float t_ = %s; %s }" % (e, setS(d, "t_")))
        elif name in ("vmla", "vmls", "vnmls", "vnmla"):
            d, n, m = (reg(o) for o in ops)
            V = Dv if dtype else Fv
            T = "double" if dtype else "float"
            prod = "%s p_ = %s * %s;" % (T, V(n), V(m))
            acc = {"vmla": "%s + p_", "vmls": "%s - p_", "vnmls": "p_ - %s", "vnmla": "-%s - p_"}[name] % V(d)
            st.append("{ %s %s t_ = %s; %s }" % (prod, T, acc, setD(d, "t_") if dtype else setS(d, "t_")))
        elif name in ("vneg", "vsqrt", "vabs"):
            d, m = (reg(o) for o in ops)
            if dtype:
                e = {"vneg": "(-%s)", "vsqrt": "sqrt(%s)", "vabs": "fabs(%s)"}[name] % Dv(m)
                st.append("{ double t_ = %s; %s }" % (e, setD(d, "t_")))
            else:
                if name == "vneg":
                    st.append("%s = %s ^ 0x80000000u;" % (S(d), S(m)))
                elif name == "vabs":
                    st.append("%s = %s & 0x7fffffffu;" % (S(d), S(m)))
                else:
                    st.append("{ float t_ = sqrtf(%s); %s }" % (Fv(m), setS(d, "t_")))
        elif name in ("vcmp", "vcmpe"):
            d = reg(ops[0])
            if ops[1].type == csarm.ARM_OP_FP or ops[1].type == csarm.ARM_OP_IMM:
                rhs = "0.0" if dtype else "0.0f"
            else:
                rhs = Dv(reg(ops[1])) if dtype else Fv(reg(ops[1]))
            lhs = Dv(d) if dtype else Fv(d)
            st.append("aot_fcmp((double)%s, (double)%s, &FN, &FZ, &FC, &FV);" % (lhs, rhs))
        elif name == "fmstat":
            st.append("N = FN; Z = FZ; C = FC; V = FV;")
        elif name == "vcvt":
            m = re.match(r"vcvt[a-z]*\.(\w+)\.(\w+)", mn)
            if not m or "vcvtr" in mn:
                raise TranslationError("vcvt form %s" % mn)
            to, frm = m.group(1), m.group(2)
            d, s_ = reg(ops[0]), reg(ops[1])
            if len(ops) != 2:
                raise TranslationError("fixed-point vcvt")
            src = {"f32": Fv, "f64": Dv}.get(frm)
            if frm in ("s32", "u32"):
                src_e = "(int32_t)%s" % S(s_) if frm == "s32" else "%s" % S(s_)
            else:
                src_e = src(s_)
            if to == "f32":
                st.append("{ float t_ = (float)(%s); %s }" % (src_e, setS(d, "t_")))
            elif to == "f64":
                st.append("{ double t_ = (double)(%s); %s }" % (src_e, setD(d, "t_")))
            elif to == "s32":
                st.append("%s = (uint32_t)aot_f2s32((double)%s);" % (S(d), src_e))
            elif to == "u32":
                st.append("%s = aot_f2u32((double)%s);" % (S(d), src_e))
            else:
                raise TranslationError("vcvt %s" % mn)
        elif name == "vmov":
            rs_ = [reg(o) if o.type == csarm.ARM_OP_REG else None for o in ops]
            if None in rs_:
                raise TranslationError("vmov immediate (VFPv3)")
            if len(rs_) == 2:
                d, m = rs_
                if is_s(d) and is_s(m):
                    st.append("%s = %s;" % (S(d), S(m)))
                elif is_d(d) and is_d(m):
                    kd, km = int(d[1:]), int(m[1:])
                    st.append("s%d = s%d; s%d = s%d;" % (2 * kd, 2 * km, 2 * kd + 1, 2 * km + 1))
                elif is_s(d):
                    st.append("%s = %s;" % (S(d), core(m)))
                elif is_s(m):
                    st.append("%s = %s;" % (core(d), S(m)))
                else:
                    raise TranslationError("vmov form %s" % ins.op_str)
            elif len(rs_) == 3:
                x, y, z = rs_
                if is_d(x):
                    k = int(x[1:])
                    st.append("s%d = %s; s%d = %s;" % (2 * k, core(y), 2 * k + 1, core(z)))
                elif is_d(z):
                    k = int(z[1:])
                    st.append("{ uint32_t lo_ = s%d, hi_ = s%d; %s = lo_; %s = hi_; }" % (2 * k, 2 * k + 1, core(x), core(y)))
                else:
                    raise TranslationError("vmov form %s" % ins.op_str)
            elif len(rs_) == 4:
                x, y, z, q = rs_
                if is_s(x):
                    st.append("%s = %s; %s = %s;" % (S(x), core(z), S(y), core(q)))
                else:
                    st.append("{ uint32_t a_ = %s, b_ = %s; %s = a_; %s = b_; }" % (S(z), S(q), core(x), core(y)))
            else:
                raise TranslationError("vmov operands")
        elif name in ("vldr", "vstr"):
            r = reg(ops[0])
            mem = ops[1].mem
            base = rn(mem.base)
            basee = hx(GUEST_BASE + a + 8) if base == "pc" else core(base)
            ea = "(%s + (uint32_t)%d)" % (basee, mem.disp) if mem.disp else basee
            st.append("uint32_t ea_ = %s;" % ea)
            if name == "vldr":
                if is_s(r):
                    st.append("%s = AOT_LD32(ea_);" % S(r))
                else:
                    k = int(r[1:])
                    st.append("s%d = AOT_LD32(ea_); s%d = AOT_LD32(ea_ + 4u);" % (2 * k, 2 * k + 1))
            else:
                if is_s(r):
                    st.append("AOT_ST32(ea_, %s);" % S(r))
                else:
                    k = int(r[1:])
                    st.append("AOT_ST32(ea_, s%d); AOT_ST32(ea_ + 4u, s%d);" % (2 * k, 2 * k + 1))
        elif name in ("vpush", "vpop", "vldmia", "vstmia", "vldmdb", "vstmdb"):
            if name in ("vpush", "vpop"):
                base = "sp"
                lst = [reg(o) for o in ops]
                wb = True
                load = name == "vpop"
                inc = name == "vpop"
                before = name == "vpush"
            else:
                base = core(reg(ops[0]))
                lst = [reg(o) for o in ops[1:]]
                wb = ins.writeback
                load = name.startswith("vld")
                inc = name.endswith("ia")
                before = not inc
            words = []
            for r in lst:
                if is_s(r):
                    words.append(S(r))
                else:
                    k = int(r[1:])
                    words += ["s%d" % (2 * k), "s%d" % (2 * k + 1)]
            n = len(words)
            st.append("uint32_t b_ = %s;" % base)
            st.append("uint32_t ea_ = %s;" % ("b_" if inc else "b_ - %du" % (4 * n)))
            for i, sreg in enumerate(words):
                if load:
                    st.append("%s = AOT_LD32(ea_ + %du);" % (sreg, 4 * i))
                else:
                    st.append("AOT_ST32(ea_ + %du, %s);" % (4 * i, sreg))
            if wb:
                st.append("%s = %s;" % (base, "b_ + %du" % (4 * n) if inc else "b_ - %du" % (4 * n)))
        elif name == "vmrs" or name == "vmsr":
            raise TranslationError("FPSCR access %s" % mn)
        else:
            raise TranslationError("VFP instruction %s" % mn)
        return self.wrap_cond(cond, st)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--elf", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    img = Image(args.elf)
    os.makedirs(args.out, exist_ok=True)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    md.detail = True
    manifest = {"binary": args.elf, "binary_sha256": img.sha256, "guest_base": hex(GUEST_BASE),
                "functions": [], "imports": img.imports}
    code_funcs = [f for f in img.funcs if img.text_lo <= f["addr"] < img.text_hi]
    chunks = [code_funcs[i:i + FUNCS_PER_FILE] for i in range(0, len(code_funcs), FUNCS_PER_FILE)]
    total_unsupported = 0
    for ci, chunk in enumerate(chunks):
        out = ['/* Generated by tools/aot/arm2c.py from %s (sha256 %s). Do not edit. */' % (
            os.path.basename(args.elf), img.sha256), '#include "aot_rt.h"', '#include "aot_decls.h"', ""]
        for f in chunk:
            em = Emitter(img, f, md, manifest)
            try:
                out.append(em.translate())
            except TranslationError as e:
                raise SystemExit("function %s: %s" % (f["name"], e))
            out.append("")
            total_unsupported += len(em.unsupported)
            manifest["functions"].append({"addr": hex(f["addr"]), "name": f["name"], "end": hex(f["end"]),
                                          "insns": len(em.insns), "uses_vfp": em.uses_vfp,
                                          "unsupported": em.unsupported})
        with open(os.path.join(args.out, "aot_funcs_%02d.c" % ci), "w") as fh:
            fh.write("\n".join(out))
    # declarations
    decl = ["/* Generated. */", "#ifndef AOT_DECLS_H", "#define AOT_DECLS_H", '#include "aot_rt.h"']
    for f in code_funcs:
        decl.append("void F_%08x(aot_cpu *c);" % f["addr"])
    for name in img.imports:
        decl.append("void aot_imp_%s(aot_cpu *c);" % name)
    decl.append("#endif")
    with open(os.path.join(args.out, "aot_decls.h"), "w") as fh:
        fh.write("\n".join(decl) + "\n")
    # table + image
    t = ['/* Generated by tools/aot/arm2c.py. Do not edit. */', '#include "aot_rt.h"', '#include "aot_decls.h"', ""]
    t.append('const char aot_binary_sha256[] = "%s";' % img.sha256)
    t.append("const aot_func_entry aot_func_table[] = {")
    for f in code_funcs:
        t.append('    {%s, F_%08x, "%s"},' % (hx(GUEST_BASE + f["addr"]), f["addr"], f["name"]))
    t.append("};")
    t.append("const uint32_t aot_func_count = %d;" % len(code_funcs))
    t.append("const aot_import_entry aot_import_table[] = {")
    for i, name in enumerate(img.imports):
        t.append('    {%s, aot_imp_%s, "%s"},' % (hx(IMPORT_THUNK_BASE + 16 * i), name, name))
    t.append("};")
    t.append("const uint32_t aot_import_count = %d;" % len(img.imports))
    # segments
    segs = []
    for i, s in enumerate(img.segments):
        data = img.raw[s["p_offset"]:s["p_offset"] + s["p_filesz"]]
        t.append("static const uint8_t seg%d[%d] = {" % (i, max(len(data), 1)))
        for k in range(0, len(data), 32):
            t.append("    " + ",".join(str(b) for b in data[k:k + 32]) + ",")
        t.append("};")
        segs.append((s["p_vaddr"], s["p_memsz"], len(data), i))
    t.append("const aot_segment aot_segments[] = {")
    for v, ms, fs, i in segs:
        t.append("    {%s, %du, %du, seg%d}," % (hx(GUEST_BASE + v), ms, fs, i))
    t.append("};")
    t.append("const uint32_t aot_segment_count = %d;" % len(segs))
    t.append("const uint32_t aot_image_end = %s;" % hx(GUEST_BASE + img.mem_hi))
    # relocations
    t.append("const aot_reloc aot_relocs[] = {")
    for off, typ, name, val, undef in img.relocs:
        imp = img.imports.index(name) if undef and name else -1
        t.append('    {%s, %d, %d, %s, "%s"},' % (hx(GUEST_BASE + off), typ, imp,
                                                hx(GUEST_BASE + val if (name and not undef) else 0), name))
    t.append("};")
    t.append("const uint32_t aot_reloc_count = %d;" % len(img.relocs))
    # init array
    ia = img.elf.get_section_by_name(".init_array")
    t.append("const uint32_t aot_init_array = %s;" % hx(GUEST_BASE + ia["sh_addr"]))
    t.append("const uint32_t aot_init_array_count = %d;" % (ia["sh_size"] // 4))
    # objects of interest for the runtime (by name)
    t.append("const aot_symbol aot_symbols[] = {")
    for n in sorted(img.objects):
        v, sz = img.objects[n]
        t.append('    {"%s", %s, %d},' % (n, hx(GUEST_BASE + v), sz))
    for f in code_funcs:
        t.append('    {"%s", %s, %d},' % (f["name"], hx(GUEST_BASE + f["addr"]), f["size"]))
    t.append("};")
    t.append("const uint32_t aot_symbol_count = %d;" % (len(img.objects) + len(code_funcs)))
    with open(os.path.join(args.out, "aot_table.c"), "w") as fh:
        fh.write("\n".join(t) + "\n")
    manifest["function_count"] = len(code_funcs)
    manifest["chunks"] = len(chunks)
    manifest["unsupported_sites"] = total_unsupported
    with open(os.path.join(args.out, "aot_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    print("translated %d functions into %d files; %d unsupported sites (fatal if reached)" % (
        len(code_funcs), len(chunks), total_unsupported))


if __name__ == "__main__":
    main()

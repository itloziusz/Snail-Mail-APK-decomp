#!/usr/bin/env python3
"""Capstone ARM disassembly over mapping-symbol code regions, plus a small,
conservative intra-procedural constant propagation used to resolve
PC-relative / GOT-relative address computations (Android -fpic code).

Analysis-only. Results are *static inference*; every resolved reference keeps
the instruction address that produced it so it can be re-checked by hand.

Resolution model (per function, forward dataflow over basic blocks, meet =
keep only registers whose known value agrees on all incoming edges):
  * ldr rX, [pc, #d]            -> rX = relocated literal word
  * add rX, pc, rY / add rX, rY, #i / sub / mov / movw / movt / mvn -> arithmetic
  * ldr rX, [rB, rI|#d] with a known address inside .got/.data.rel.ro/.rodata
                                -> rX = relocated word (GOT entry = symbol address)
  * loads/stores through a known address are recorded as read/write refs
  * calls clobber r0-r3, r12, lr; any other register write kills the value
"""
from __future__ import annotations

import bisect
from collections import Counter, defaultdict

from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs
from capstone import arm as A

MASK = 0xFFFFFFFF
AL = A.ARM_CC_AL
CALL_CLOBBER = ("r0", "r1", "r2", "r3", "r12", "lr")
LOAD_IDS = {A.ARM_INS_LDR, A.ARM_INS_LDRB, A.ARM_INS_LDRH, A.ARM_INS_LDRSB, A.ARM_INS_LDRSH,
            A.ARM_INS_LDRD, A.ARM_INS_VLDR, A.ARM_INS_LDREX, A.ARM_INS_LDRT, A.ARM_INS_LDRBT}
STORE_IDS = {A.ARM_INS_STR, A.ARM_INS_STRB, A.ARM_INS_STRH, A.ARM_INS_STRD, A.ARM_INS_VSTR,
             A.ARM_INS_STREX, A.ARM_INS_STRT, A.ARM_INS_STRBT}
LDM_IDS = {A.ARM_INS_LDM, A.ARM_INS_LDMDA, A.ARM_INS_LDMDB, A.ARM_INS_LDMIB, A.ARM_INS_POP,
           A.ARM_INS_VLDMIA, A.ARM_INS_VLDMDB, A.ARM_INS_VPOP}
STM_IDS = {A.ARM_INS_STM, A.ARM_INS_STMDA, A.ARM_INS_STMDB, A.ARM_INS_STMIB, A.ARM_INS_PUSH,
           A.ARM_INS_VSTMIA, A.ARM_INS_VSTMDB, A.ARM_INS_VPUSH}


class Insn:
    __slots__ = ("addr", "size", "id", "mnem", "op_str", "cc", "groups", "ops", "regs_w",
                 "writeback", "post_index", "mode")

    def __repr__(self):
        return f"<0x{self.addr:x} {self.mnem} {self.op_str}>"

    @property
    def text(self):
        return f"{self.mnem} {self.op_str}".strip()


def _convert(ci, md, mode):
    i = Insn()
    i.addr, i.size, i.id, i.mnem, i.op_str = ci.address, ci.size, ci.id, ci.mnemonic, ci.op_str
    i.cc = ci.cc
    i.groups = tuple(md.group_name(g) for g in ci.groups)
    ops = []
    for o in ci.operands:
        if o.type == A.ARM_OP_REG:
            ops.append(("reg", ci.reg_name(o.reg), o.shift.type, o.shift.value, bool(o.subtracted)))
        elif o.type == A.ARM_OP_IMM:
            ops.append(("imm", o.imm & MASK))
        elif o.type == A.ARM_OP_MEM:
            ops.append(("mem", ci.reg_name(o.mem.base) if o.mem.base else None,
                        ci.reg_name(o.mem.index) if o.mem.index else None, o.mem.disp,
                        o.shift.type, o.shift.value, bool(o.subtracted)))
        else:
            ops.append(("other", o.type))
    i.ops = tuple(ops)
    try:
        _r, w = ci.regs_access()
        i.regs_w = tuple(ci.reg_name(x) for x in w)
    except Exception:  # pragma: no cover - capstone quirk
        i.regs_w = ()
    i.writeback = bool(ci.writeback)
    i.post_index = bool(getattr(ci, "post_index", False))
    i.mode = mode
    return i


def disassemble(b):
    """Disassemble every arm/thumb mapping region. Returns (dict addr->Insn, invalid list)."""
    md_arm = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md_arm.detail = True
    md_thumb = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    md_thumb.detail = True
    insns = {}
    invalid = []
    for r in b.regions:
        if r.kind not in ("arm", "thumb"):
            continue
        md = md_arm if r.kind == "arm" else md_thumb
        step = 4 if r.kind == "arm" else 2
        code = b.read(r.start, r.end - r.start)
        n = len(code) - len(code) % step
        off = 0
        while off < n:
            for ci in md.disasm(code[off:n], r.start + off):
                insns[ci.address] = _convert(ci, md, r.kind)
                off += ci.size
            if off < n:
                invalid.append({"addr": r.start + off, "bytes": code[off:off + step].hex(), "region": r.kind})
                off += step
    return dict(sorted(insns.items())), invalid


# --------------------------------------------------------------------------- classification
def _reg_ops(i):
    return [o for o in i.ops if o[0] == "reg"]


def writes_pc(i):
    return "pc" in i.regs_w


def is_mov_lr_pc(i):
    return (i is not None and i.id == A.ARM_INS_MOV and len(i.ops) == 2
            and i.ops[0][:2] == ("reg", "lr") and i.ops[1][:2] == ("reg", "pc"))


def classify(i, prev):
    """-> (kind, target) with kind in call_direct, call_indirect, jump_direct,
    return, jump_indirect, normal."""
    if i.id == A.ARM_INS_BL:
        return "call_direct", i.ops[0][1]
    if i.id == A.ARM_INS_BLX:
        if i.ops and i.ops[0][0] == "imm":
            return "call_direct", i.ops[0][1]
        return "call_indirect", None
    if i.id == A.ARM_INS_B:
        return "jump_direct", i.ops[0][1]
    if i.id == A.ARM_INS_BX:
        if i.ops and i.ops[0][:2] == ("reg", "lr"):
            return "return", None
        if is_mov_lr_pc(prev):
            return "call_indirect", None
        return "jump_indirect", None
    if writes_pc(i):
        if is_mov_lr_pc(prev):
            return "call_indirect", None
        if i.id in LDM_IDS or i.id == A.ARM_INS_POP:
            if i.id == A.ARM_INS_POP or (i.ops and i.ops[0][:2] == ("reg", "sp")):
                return "return", None
        if i.id == A.ARM_INS_LDR and i.ops[1][0] == "mem" and i.ops[1][1] == "sp":
            return "return", None
        if i.id == A.ARM_INS_MOV and len(i.ops) == 2 and i.ops[1][:2] == ("reg", "lr"):
            return "return", None
        return "jump_indirect", None
    return "normal", None


# --------------------------------------------------------------------------- dataflow
class FunctionAnalysis:
    """Result container (plain lists/dicts; addresses are ints)."""

    def __init__(self):
        self.insn_count = 0
        self.calls = []            # (site, target)
        self.tailcalls = []        # (site, target)
        self.internal_branches = 0
        self.external_jumps = []   # (site, target) B to non-function-start outside the body
        self.indirect_calls = []   # dict(site, text, expr, resolved)
        self.indirect_jumps = []   # dict(site, text, kind, targets)
        self.returns = 0
        self.refs = []             # dict(site, kind, addr, via)
        self.literals = []         # (site, literal_addr)
        self.mov_lr_pc_calls = 0
        self.blx_reg_calls = 0


def _shift(val, stype, sval):
    if stype in (0, None) or sval == 0:
        return val
    if stype == A.ARM_SFT_LSL:
        return (val << sval) & MASK
    if stype == A.ARM_SFT_LSR:
        return (val >> sval) & MASK
    if stype == A.ARM_SFT_ASR:
        v = val - (1 << 32) if val & 0x80000000 else val
        return (v >> sval) & MASK
    return None


class Resolver:
    def __init__(self, b, insns):
        self.b = b
        self.insns = insns
        self.got = b.got_base()
        ro = []
        for s in b.sections:
            if s["name"] in (".got", ".data.rel.ro", ".rodata", ".text", ".ARM.extab"):
                ro.append((s["addr"], s["addr"] + s["size"], s["name"]))
        self.const_secs = ro

    def _const_word_sec(self, addr):
        for lo, hi, n in self.const_secs:
            if lo <= addr < hi:
                return n
        return None

    def analyze(self, start, end, func_starts):
        b = self.b
        fa = FunctionAnalysis()
        addrs = [a for a in self._addrs_in(start, end)]
        fa.insn_count = len(addrs)
        if not addrs:
            return fa
        aset = set(addrs)
        # --- leaders / blocks
        leaders = {addrs[0]}
        succ_of = {}
        prev = None
        kinds = {}
        for a in addrs:
            i = self.insns[a]
            k, t = classify(i, prev)
            kinds[a] = (k, t)
            nxt = a + i.size
            cond = i.cc not in (AL, 0)
            if k == "jump_direct":
                s = []
                if start <= t < end and t in aset:
                    s.append(t)
                    leaders.add(t)
                if cond and nxt in aset:
                    s.append(nxt)
                succ_of[a] = s
                leaders.add(nxt)
            elif k in ("return",):
                succ_of[a] = [nxt] if cond and nxt in aset else []
                leaders.add(nxt)
            elif k == "jump_indirect":
                targets = self._switch_targets(i, start, end, aset)
                s = list(targets) + ([nxt] if cond and nxt in aset else [])
                succ_of[a] = sorted(set(s))
                for t2 in s:
                    leaders.add(t2)
                leaders.add(nxt)
            else:
                succ_of[a] = [nxt] if nxt in aset else []
            prev = i
        # contiguous runs split at leaders; a gap (literal pool) also ends a block
        blocks = []
        cur = []
        for a in addrs:
            if cur and (a in leaders or a != cur[-1] + self.insns[cur[-1]].size):
                blocks.append(cur)
                cur = []
            cur.append(a)
        if cur:
            blocks.append(cur)
        block_of = {blk[0]: blk for blk in blocks}
        bsucc = {}
        for blk in blocks:
            last = blk[-1]
            bsucc[blk[0]] = [s for s in succ_of[last] if s in block_of]
        # --- fixpoint
        TOP = None
        state_in = {blk[0]: TOP for blk in blocks}
        state_in[blocks[0][0]] = {"sp": ("sp", 0)}
        work = [blocks[0][0]]
        seen_iter = 0
        while work and seen_iter < 20000:
            seen_iter += 1
            h = work.pop()
            st = dict(state_in[h])
            for a in block_of[h]:
                st = self._transfer(self.insns[a], st, kinds[a], None)
            for s in bsucc[h]:
                old = state_in[s]
                if old is TOP:
                    new = dict(st)
                else:
                    new = {k: v for k, v in old.items() if st.get(k) == v}
                if old is TOP or new != old:
                    state_in[s] = new
                    if s not in work:
                        work.append(s)
        # blocks never reached from the entry (e.g. only reachable via unresolved
        # switch tables) are analysed from an empty state
        for blk in blocks:
            st = dict(state_in[blk[0]] or {})
            prev = None
            for a in blk:
                i = self.insns[a]
                st = self._transfer(i, st, kinds[a], fa, prev=prev, func=(start, end, func_starts, aset))
                prev = i
        return fa

    def _addrs_in(self, start, end):
        keys = self._keys if hasattr(self, "_keys") else None
        if keys is None:
            self._keys = keys = list(self.insns)
        lo = bisect.bisect_left(keys, start)
        hi = bisect.bisect_left(keys, end)
        return keys[lo:hi]

    def _switch_targets(self, i, start, end, aset):
        """GCC ARM switch idioms: `add pc, pc, rX, lsl #2` + table of B,
        or `ldr pc, [pc, rX, lsl #2]` + table of words in a $d region."""
        tg = []
        if i.id == A.ARM_INS_ADD and len(i.ops) == 3 and i.ops[0][:2] == ("reg", "pc") \
                and i.ops[1][:2] == ("reg", "pc") and i.ops[2][0] == "reg":
            a = i.addr + 8
            while a in self.insns and self.insns[a].id == A.ARM_INS_B and self.insns[a].cc == AL and start <= a < end:
                tg.append(a)
                a += 4
        elif i.id == A.ARM_INS_LDR and i.ops[1][0] == "mem" and i.ops[1][1] == "pc" and i.ops[1][2]:
            a = i.addr + 8
            reg = self.b.region_at(a)
            while reg is not None and reg.kind == "data" and a < reg.end and start <= a < end:
                t = self.b.resolve_word(a)["value"]
                if t is None or not (start <= t < end):
                    break
                tg.append(t)
                a += 4
        return tg

    # -------------------------------------------------------------- transfer
    def _val(self, st, reg, i):
        if reg == "pc":
            return (i.addr + (8 if i.mode == "arm" else 4)) & MASK
        return st.get(reg)

    def _set(self, st, reg, val, i):
        if reg == "pc":
            return
        if val is None:
            st.pop(reg, None)
            return
        if i.cc not in (AL, 0):  # conditional write: keep only if unchanged
            if st.get(reg) != val:
                st.pop(reg, None)
            return
        st[reg] = val

    def _frame_off(self, st, i, memop):
        """Frame offset (relative to the entry sp) addressed by a memory operand
        whose base holds a frame pointer value ('sp', off); None otherwise."""
        _, base, index, disp, _stype, _sval, _sub = memop
        if not base or index:
            return None
        bv = st.get(base)
        if not (isinstance(bv, tuple) and bv[0] == "sp"):
            return None
        return bv[1] if i.post_index else bv[1] + disp

    def _stack_store(self, st, off, val, i, size=4):
        for k in range(off - (off % 4), off + size, 4):
            st.pop(("stk", k), None)
        if size == 4 and off % 4 == 0 and val is not None and i.cc in (AL, 0):
            st[("stk", off)] = val

    def _eff_addr(self, st, i, memop):
        _, base, index, disp, stype, sval, sub = memop
        bv = self._val(st, base, i) if base else 0
        if not isinstance(bv, int):
            return None
        if i.post_index:
            return bv
        off = disp
        if index:
            iv = self._val(st, index, i)
            if not isinstance(iv, int):
                return None
            iv = _shift(iv, stype, sval)
            if iv is None:
                return None
            off = -iv if sub else iv
        return (bv + off) & MASK

    def _transfer(self, i, st, kind, fa, prev=None, func=None):
        k, target = kind
        rec = fa is not None
        # ---- control flow effects (recording only)
        if rec:
            start, end, func_starts, aset = func
            if k == "call_direct":
                fa.calls.append((i.addr, target))
            elif k == "jump_direct":
                if start <= target < end:
                    fa.internal_branches += 1
                elif target in func_starts or self.b.plt_by_addr.get(target):
                    fa.tailcalls.append((i.addr, target))
                else:
                    fa.external_jumps.append((i.addr, target))
            elif k == "call_indirect":
                expr, resolved = self._indirect_expr(i, st)
                fa.indirect_calls.append({"site": i.addr, "text": i.text, "expr": expr, "resolved": resolved,
                                          "idiom": "blx_reg" if i.id == A.ARM_INS_BLX else "mov_lr_pc"})
                if i.id == A.ARM_INS_BLX:
                    fa.blx_reg_calls += 1
                else:
                    fa.mov_lr_pc_calls += 1
            elif k == "jump_indirect":
                tg = self._switch_targets(i, start, end, aset)
                expr, resolved = self._indirect_expr(i, st)
                fa.indirect_jumps.append({"site": i.addr, "text": i.text,
                                          "kind": "switch_table" if tg else "unresolved",
                                          "targets": len(tg), "expr": expr, "resolved": resolved})
            elif k == "return":
                fa.returns += 1
        # ---- register/data effects
        if k == "return":
            # a conditional return only continues on the path where it did not
            # execute, so the fall-through state is unchanged
            return st
        iid = i.id
        ops = i.ops
        handled = False
        if iid in LOAD_IDS or iid in STORE_IDS:
            mem = next((o for o in ops if o[0] == "mem"), None)
            fo = self._frame_off(st, i, mem) if mem is not None and mem[1] != "pc" else None
            if fo is not None:
                regs = [o[1] for o in ops if o[0] == "reg"]
                if iid in LOAD_IDS:
                    for r in regs:
                        self._set(st, r, None, i)
                    if iid == A.ARM_INS_LDR and regs and regs[0] != "pc":
                        self._set(st, regs[0], st.get(("stk", fo)), i)
                    elif iid == A.ARM_INS_LDRD and len(regs) >= 2:
                        self._set(st, regs[0], st.get(("stk", fo)), i)
                        self._set(st, regs[1], st.get(("stk", fo + 4)), i)
                else:
                    if iid == A.ARM_INS_STR and regs:
                        self._stack_store(st, fo, st.get(regs[0]) if regs[0] != "pc" else None, i)
                    elif iid == A.ARM_INS_STRD and len(regs) >= 2:
                        self._stack_store(st, fo, st.get(regs[0]), i)
                        self._stack_store(st, fo + 4, st.get(regs[1]), i)
                    else:
                        size = 8 if (iid == A.ARM_INS_VSTR and regs and regs[0].startswith("d")) else 1
                        self._stack_store(st, fo, None, i, size=size)
                if i.writeback and mem[1]:
                    bv = st.get(mem[1])
                    if i.post_index:
                        imm = next((o[1] for o in ops if o[0] == "imm"), None)
                        if imm is not None and imm & 0x80000000:
                            imm -= 1 << 32
                        nb = ("sp", bv[1] + imm) if imm is not None else None
                    else:
                        nb = ("sp", bv[1] + mem[3])
                    self._set(st, mem[1], nb, i)
                mem = None
                handled = True
            if mem is not None:
                ea = self._eff_addr(st, i, mem)
                dst = [o[1] for o in ops if o[0] == "reg"]
                if iid in LOAD_IDS:
                    newval = None
                    if ea is not None:
                        if mem[1] == "pc":
                            if rec:
                                fa.literals.append((i.addr, ea))
                            if iid == A.ARM_INS_LDR:
                                newval = self._const_word(ea)
                        else:
                            sec = self._const_word_sec(ea)
                            if iid == A.ARM_INS_LDR and sec in (".got", ".data.rel.ro"):
                                newval = self._const_word(ea)
                                if rec:
                                    via = "got" if sec == ".got" else "rel_ro"
                                    fa.refs.append({"site": i.addr, "kind": "read", "addr": ea, "via": via})
                                    if newval is not None:
                                        fa.refs.append({"site": i.addr, "kind": "addr", "addr": newval, "via": via})
                            elif rec:
                                fa.refs.append({"site": i.addr, "kind": "read", "addr": ea, "via": "reg"})
                    for r in dst:
                        self._set(st, r, None, i)
                    if dst and iid == A.ARM_INS_LDR and dst[0] != "pc":
                        self._set(st, dst[0], newval, i)
                else:
                    if ea is not None and rec:
                        fa.refs.append({"site": i.addr, "kind": "write", "addr": ea, "via": "reg"})
                if i.writeback and mem[1]:
                    # base update: [rB, #d]! or post-index; resolve simple immediates
                    bv = self._val(st, mem[1], i)
                    nb = None
                    if isinstance(bv, int):
                        if i.post_index:
                            imm = next((o[1] for o in ops if o[0] == "imm"), None)
                            nb = (bv + imm) & MASK if imm is not None else None
                        elif not mem[2]:
                            nb = (bv + mem[3]) & MASK
                    self._set(st, mem[1], nb, i)
                handled = True
        elif iid in LDM_IDS or iid in STM_IDS:
            implicit_sp = iid in (A.ARM_INS_POP, A.ARM_INS_PUSH, A.ARM_INS_VPUSH, A.ARM_INS_VPOP)
            base = ops[0][1] if ops and ops[0][0] == "reg" and not implicit_sp else "sp"
            bv = self._val(st, base, i)
            if isinstance(bv, tuple) and bv[0] == "sp":
                regs = [o[1] for o in (ops if implicit_sp else ops[1:]) if o[0] == "reg"]
                sizes = [8 if r.startswith("d") else 4 for r in regs]
                total = sum(sizes)
                dec = iid in (A.ARM_INS_PUSH, A.ARM_INS_VPUSH, A.ARM_INS_STMDB, A.ARM_INS_LDMDB,
                              A.ARM_INS_VSTMDB, A.ARM_INS_VLDMDB, A.ARM_INS_STMDA, A.ARM_INS_LDMDA)
                start_off = bv[1] - total if dec else bv[1]
                if iid in (A.ARM_INS_STMIB, A.ARM_INS_LDMIB):
                    start_off += 4
                elif iid in (A.ARM_INS_STMDA, A.ARM_INS_LDMDA):
                    start_off += 4
                off = start_off
                for r, sz in zip(regs, sizes):
                    if iid in STM_IDS:
                        self._stack_store(st, off, st.get(r) if sz == 4 else None, i, size=sz)
                    elif r != "pc":
                        self._set(st, r, st.get(("stk", off)) if sz == 4 else None, i)
                    off += sz
                if implicit_sp or i.writeback:
                    self._set(st, base, ("sp", bv[1] - total if dec else bv[1] + total), i)
                handled = True
        if handled:
            pass
        elif iid in LDM_IDS or iid in STM_IDS:
            if rec and isinstance(bv, int) and base != "sp":
                fa.refs.append({"site": i.addr, "kind": "read" if iid in LDM_IDS else "write", "addr": bv, "via": "reg"})
            for r in i.regs_w:
                self._set(st, r, None, i)
            handled = True
        elif not handled and iid in (A.ARM_INS_MOV, A.ARM_INS_MOVW, A.ARM_INS_MVN, A.ARM_INS_MOVT) and ops and ops[0][0] == "reg":
            rd = ops[0][1]
            v = None
            if iid == A.ARM_INS_MOVT and len(ops) == 2 and ops[1][0] == "imm":
                old = st.get(rd)
                v = ((old & 0xFFFF) | (ops[1][1] << 16)) & MASK if isinstance(old, int) else None
            elif len(ops) == 2 and ops[1][0] == "imm":
                v = ops[1][1] if iid != A.ARM_INS_MVN else (~ops[1][1]) & MASK
            elif iid == A.ARM_INS_MOV and len(ops) == 2 and ops[1][0] == "reg" and ops[1][2] in (0, None) \
                    and not i.mnem.endswith("s"):
                v = self._val(st, ops[1][1], i)
            if rd == "lr" and len(ops) == 2 and ops[1][:2] == ("reg", "pc"):
                v = None
            for r in i.regs_w:
                if r != rd:
                    self._set(st, r, None, i)
            self._set(st, rd, v, i)
            handled = True
        elif not handled and iid in (A.ARM_INS_ADD, A.ARM_INS_SUB, A.ARM_INS_ADR) and ops and ops[0][0] == "reg" and ops[0][1] != "pc":
            rd = ops[0][1]
            v = None
            if iid == A.ARM_INS_ADR and len(ops) == 2 and ops[1][0] == "imm":
                v = ops[1][1]
            elif len(ops) == 3:
                a = self._val(st, ops[1][1], i) if ops[1][0] == "reg" else None
                if ops[2][0] == "imm":
                    c = ops[2][1]
                elif ops[2][0] == "reg":
                    c = self._val(st, ops[2][1], i)
                    c = _shift(c, ops[2][2], ops[2][3]) if isinstance(c, int) else None
                else:
                    c = None
                if isinstance(a, tuple) and a[0] == "sp" and isinstance(c, int):
                    cs = c - (1 << 32) if c & 0x80000000 else c
                    v = ("sp", a[1] + cs if iid == A.ARM_INS_ADD else a[1] - cs)
                elif isinstance(a, int) and isinstance(c, int):
                    v = (a + c) & MASK if iid == A.ARM_INS_ADD else (a - c) & MASK
                    if rec and (ops[1][1] == "pc" or (ops[2][0] == "reg" and ops[2][1] == "pc")
                                or a == self.got or c == self.got):
                        if v != self.got:
                            fa.refs.append({"site": i.addr, "kind": "addr", "addr": v,
                                            "via": "gotoff" if (a == self.got or c == self.got) else "pcrel"})
            for r in i.regs_w:
                if r != rd:
                    self._set(st, r, None, i)
            self._set(st, rd, v, i)
            handled = True
        if not handled:
            for r in i.regs_w:
                self._set(st, r, None, i)
        if k in ("call_direct", "call_indirect"):
            lows = [st[r][1] for r in ("r0", "r1", "r2", "r3")
                    if isinstance(st.get(r), tuple) and st[r][0] == "sp"]
            if lows:
                lo = min(lows)
                for key in [x for x in st if isinstance(x, tuple) and x[0] == "stk" and x[1] >= lo]:
                    del st[key]
            for r in CALL_CLOBBER:
                st.pop(r, None)
        return st

    def _const_word(self, ea):
        try:
            rw = self.b.resolve_word(ea)
        except ValueError:
            return None
        if rw["kind"] == "import":
            return ("imp", rw["symbol"])
        return rw["value"]

    def _indirect_expr(self, i, st):
        """Describe the target of an indirect call/jump; resolve it when the
        slot address is known and constant (GOT / .data.rel.ro)."""
        if i.id in (A.ARM_INS_BLX, A.ARM_INS_BX):
            r = i.ops[0][1]
            v = st.get(r)
            if isinstance(v, tuple) and v[0] == "imp":
                return r, {"import": v[1]}
            if isinstance(v, tuple):
                return r, None
            return r, ({"addr": v} if isinstance(v, int) else None)
        mem = next((o for o in i.ops if o[0] == "mem"), None)
        if mem is not None:
            base, index, disp = mem[1], mem[2], mem[3]
            expr = f"[{base}" + (f"+{index}" if index else "") + (f"+0x{disp:x}" if disp > 0 else (f"-0x{-disp:x}" if disp < 0 else "")) + "]"
            fo = self._frame_off(st, i, mem)
            if fo is not None:
                v = st.get(("stk", fo))
                if isinstance(v, int):
                    return expr, {"frame_slot": fo, "addr": v}
                if isinstance(v, tuple) and v[0] == "imp":
                    return expr, {"frame_slot": fo, "import": v[1]}
                return expr, None
            ea = self._eff_addr(st, i, mem)
            if ea is not None:
                sec = self._const_word_sec(ea)
                val = self._const_word(ea) if sec in (".got", ".data.rel.ro") else None
                if isinstance(val, tuple):
                    return expr, {"slot": ea, "import": val[1]}
                return expr, {"slot": ea, "addr": val}
            bv = st.get(base) if base else None
            if isinstance(bv, int):
                return expr, {"base": bv}
            return expr, None
        if i.ops and i.ops[-1][0] == "reg":
            r = i.ops[-1][1]
            v = st.get(r)
            return r, ({"addr": v} if isinstance(v, int) else None)
        return i.text, None


def isa_census(b, insns, invalid):
    """Instruction-set evidence over mapping-symbol code regions."""
    groups = Counter()
    mnem = Counter()
    sel = Counter()
    blx_imm = []
    for a, i in insns.items():
        for g in i.groups:
            groups[g] += 1
        mnem[i.mnem] += 1
        m = i.mnem
        base = m.rstrip(".")
        if i.id == A.ARM_INS_BLX and i.ops and i.ops[0][0] == "imm":
            blx_imm.append(a)
        if i.id == A.ARM_INS_BLX and i.ops and i.ops[0][0] == "reg":
            sel["blx_reg"] += 1
        if i.id == A.ARM_INS_BX:
            sel["bx_lr" if i.ops[0][:2] == ("reg", "lr") else "bx_other"] += 1
        if is_mov_lr_pc(i):
            sel["mov_lr_pc"] += 1
        if base.startswith("v") or m in ("fmrx", "fmxr", "fmstat"):
            sel["vfp_or_neon_mnemonic"] += 1
        if m.startswith(("ldrex", "strex", "clrex")):
            sel["ldrex_strex"] += 1
        if m.startswith(("svc", "swi")):
            sel["svc"] += 1
        if m.startswith(("mrc", "mcr")):
            sel["mrc_mcr"] += 1
        if m.startswith(("dmb", "dsb", "isb")):
            sel["barriers"] += 1
        if m.startswith(("movw", "movt")):
            sel["movw_movt"] += 1
        if m.startswith(("bkpt", "udf")):
            sel["bkpt_udf"] += 1
    return {
        "decoded_instructions": len(insns),
        "invalid_decodes": len(invalid),
        "invalid_list": [{"addr": f"0x{x['addr']:x}", "bytes": x["bytes"], "region": x["region"]} for x in invalid[:200]],
        "by_mode": dict(sorted(Counter(i.mode for i in insns.values()).items())),
        "capstone_groups": dict(sorted(groups.items())),
        "selected": dict(sorted(sel.items())),
        "blx_immediate_sites": [f"0x{a:x}" for a in blx_imm],
        "top_mnemonics": dict(sorted(mnem.most_common(40), key=lambda kv: (-kv[1], kv[0]))),
    }

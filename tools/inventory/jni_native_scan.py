#!/usr/bin/env python3
"""Deterministic scan of the JNI boundary inside libsnailmail.so (ELF32 ARM).

For each input binary this reports:

* SHA-256, the `Java_*` exports (.dynsym address/size) and whether each maps
  to a `native` method declared in the DEX (JNI short-name mangling).
* The JNI-related globals (gJavaEnv, gJavaObj, gJavaClass, gJAVAFunction,
  gJavaAsset*) and the GOT slots that point at them.
* The decoded `gJAVAFunction` method table ({jmethodID, name, signature}
  records filled by JAVA_RegisterFunctions via GetMethodID).
* Every JNIEnv function-table call site found by a small linear register
  tracker (``ldr rT,[env]; ldr pc,[rT,#off]`` / ``blx``), mapped to the
  JNINativeInterface slot name (index = off / 4 on 32-bit ARM).
* Every call through the `_JNIEnv::Call{Void,Int}Method` varargs helpers,
  resolved to the Java method name/signature in gJAVAFunction.
* Loads/stores of the JNI globals per function.
* A direct call graph (bl/blx/b to function starts) and, for every Java_*
  export, whether it can reach (a) a function that uses the cached
  gJavaEnv, (b) a GL import, and which JAVA* wrappers it can reach.

The tracker is linear and heuristic (no CFG merge); every reported site
carries an address so it can be re-verified with
``llvm-objdump --triple=armv7-linux-androideabi``.

Optionally ``--check-map docs/JNI_MAP.json`` verifies that every v7a/v5
address, size and exported symbol recorded in the map matches .dynsym.

Usage (repo root):
    tools/inventory/jni_native_scan.py \
        --bin v7a=work/apk_unzip/lib/armeabi-v7a/libsnailmail.so \
        --bin v5=work/apk_unzip/lib/armeabi/libsnailmail.so \
        --managed analysis/dex/managed_shell_scan.json \
        > analysis/dex/jni_native_scan.json
"""
import argparse
import hashlib
import json
import struct
import sys
from collections import defaultdict

import capstone
from capstone import arm as carm
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

# JNINativeInterface function table, in jni.h / JNI specification order.
# Index 0-3 are reserved (NULL).  Byte offset on 32-bit targets = index*4,
# on 64-bit targets = index*8.
JNI_FUNCS = [
    "reserved0", "reserved1", "reserved2", "reserved3",
    "GetVersion", "DefineClass", "FindClass", "FromReflectedMethod",
    "FromReflectedField", "ToReflectedMethod", "GetSuperclass",
    "IsAssignableFrom", "ToReflectedField", "Throw", "ThrowNew",
    "ExceptionOccurred", "ExceptionDescribe", "ExceptionClear", "FatalError",
    "PushLocalFrame", "PopLocalFrame", "NewGlobalRef", "DeleteGlobalRef",
    "DeleteLocalRef", "IsSameObject", "NewLocalRef", "EnsureLocalCapacity",
    "AllocObject", "NewObject", "NewObjectV", "NewObjectA", "GetObjectClass",
    "IsInstanceOf", "GetMethodID",
]
for _t in ("Object", "Boolean", "Byte", "Char", "Short", "Int", "Long", "Float",
           "Double", "Void"):
    JNI_FUNCS += ["Call%sMethod" % _t, "Call%sMethodV" % _t, "Call%sMethodA" % _t]
for _t in ("Object", "Boolean", "Byte", "Char", "Short", "Int", "Long", "Float",
           "Double", "Void"):
    JNI_FUNCS += ["CallNonvirtual%sMethod" % _t, "CallNonvirtual%sMethodV" % _t,
                  "CallNonvirtual%sMethodA" % _t]
JNI_FUNCS += ["GetFieldID"]
JNI_FUNCS += ["Get%sField" % t for t in ("Object", "Boolean", "Byte", "Char", "Short",
                                         "Int", "Long", "Float", "Double")]
JNI_FUNCS += ["Set%sField" % t for t in ("Object", "Boolean", "Byte", "Char", "Short",
                                         "Int", "Long", "Float", "Double")]
JNI_FUNCS += ["GetStaticMethodID"]
for _t in ("Object", "Boolean", "Byte", "Char", "Short", "Int", "Long", "Float",
           "Double", "Void"):
    JNI_FUNCS += ["CallStatic%sMethod" % _t, "CallStatic%sMethodV" % _t,
                  "CallStatic%sMethodA" % _t]
JNI_FUNCS += ["GetStaticFieldID"]
JNI_FUNCS += ["GetStatic%sField" % t for t in ("Object", "Boolean", "Byte", "Char",
                                               "Short", "Int", "Long", "Float", "Double")]
JNI_FUNCS += ["SetStatic%sField" % t for t in ("Object", "Boolean", "Byte", "Char",
                                               "Short", "Int", "Long", "Float", "Double")]
JNI_FUNCS += ["NewString", "GetStringLength", "GetStringChars", "ReleaseStringChars",
              "NewStringUTF", "GetStringUTFLength", "GetStringUTFChars",
              "ReleaseStringUTFChars", "GetArrayLength", "NewObjectArray",
              "GetObjectArrayElement", "SetObjectArrayElement"]
_PT = ("Boolean", "Byte", "Char", "Short", "Int", "Long", "Float", "Double")
JNI_FUNCS += ["New%sArray" % t for t in _PT]
JNI_FUNCS += ["Get%sArrayElements" % t for t in _PT]
JNI_FUNCS += ["Release%sArrayElements" % t for t in _PT]
JNI_FUNCS += ["Get%sArrayRegion" % t for t in _PT]
JNI_FUNCS += ["Set%sArrayRegion" % t for t in _PT]
JNI_FUNCS += ["RegisterNatives", "UnregisterNatives", "MonitorEnter", "MonitorExit",
              "GetJavaVM", "GetStringRegion", "GetStringUTFRegion",
              "GetPrimitiveArrayCritical", "ReleasePrimitiveArrayCritical",
              "GetStringCritical", "ReleaseStringCritical", "NewWeakGlobalRef",
              "DeleteWeakGlobalRef", "ExceptionCheck", "NewDirectByteBuffer",
              "GetDirectBufferAddress", "GetDirectBufferCapacity", "GetObjectRefType"]

# Spot checks of well-known ARM32 offsets (independent of the loop above).
_SPOT = {0x18: "FindClass", 0x54: "NewGlobalRef", 0x5c: "DeleteLocalRef",
         0x7c: "GetObjectClass", 0x84: "GetMethodID", 0xc8: "CallIntMethodV",
         0xf8: "CallVoidMethodV", 0x178: "GetFieldID", 0x190: "GetIntField",
         0x1c4: "GetStaticMethodID", 0x29c: "NewStringUTF", 0x2a4: "GetStringUTFChars",
         0x2a8: "ReleaseStringUTFChars", 0x2c0: "NewByteArray",
         0x320: "GetByteArrayRegion", 0x340: "SetByteArrayRegion",
         0x35c: "RegisterNatives", 0x36c: "GetJavaVM"}
assert len(JNI_FUNCS) == 233, len(JNI_FUNCS)
for _off, _name in _SPOT.items():
    assert JNI_FUNCS[_off // 4] == _name, (_off, _name, JNI_FUNCS[_off // 4])

JNI_GLOBALS = ["gJavaEnv", "gJavaObj", "gJavaClass", "gJAVAFunction",
               "gJavaAssetFid", "gJavaAssetStart", "gJavaAssetLength"]


_REGALIAS = {"ip": "r12", "sb": "r9", "sl": "r10", "fp": "r11"}


def rname(ins, reg):
    n = ins.reg_name(reg)
    return _REGALIAS.get(n, n)


def jni_mangle(s: str) -> str:
    out = []
    for ch in s:
        if ch == "/":
            out.append("_")
        elif ch == "_":
            out.append("_1")
        elif ch == ";":
            out.append("_2")
        elif ch == "[":
            out.append("_3")
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append("_0%04x" % ord(ch))
    return "".join(out)


class Binary:
    def __init__(self, tag, path):
        self.tag = tag
        self.path = path
        self.raw = open(path, "rb").read()
        self.sha256 = hashlib.sha256(self.raw).hexdigest()
        self.elf = ELFFile(open(path, "rb"))
        self.sections = {s.name: s for s in self.elf.iter_sections()}
        self.got = self.sections[".got"]["sh_addr"]
        self._load_symbols()
        self._load_relocs()
        self._load_plt()

    # --- ELF helpers -----------------------------------------------------
    def off(self, addr):
        for seg in self.elf.iter_segments():
            if seg["p_type"] == "PT_LOAD" and seg["p_vaddr"] <= addr < seg["p_vaddr"] + seg["p_filesz"]:
                return addr - seg["p_vaddr"] + seg["p_offset"]
        return None

    def word(self, addr):
        o = self.off(addr)
        return None if o is None else struct.unpack_from("<I", self.raw, o)[0]

    def cstr(self, addr):
        o = self.off(addr)
        if o is None:
            return None
        end = self.raw.index(b"\0", o)
        return self.raw[o:end].decode("latin-1")

    def _load_symbols(self):
        self.funcs = {}       # addr -> (name, size)
        self.objects = {}     # name -> (addr, size)
        self.dynsym = {}      # name -> (addr, size, type)
        self.mapping = []     # (addr, 'a'|'d')
        for secname in (".symtab", ".dynsym"):
            sec = self.sections.get(secname)
            if sec is None:
                continue
            for sym in sec.iter_symbols():
                n = sym.name
                t = sym["st_info"]["type"]
                a = sym["st_value"]
                if secname == ".dynsym" and sym["st_shndx"] != "SHN_UNDEF" and n:
                    self.dynsym[n] = (a, sym["st_size"], t)
                if n in ("$a", "$d", "$t") and secname == ".symtab":
                    self.mapping.append((a, n[1]))
                elif t == "STT_FUNC" and sym["st_shndx"] != "SHN_UNDEF":
                    if a not in self.funcs or (self.funcs[a][0].startswith("$")):
                        self.funcs.setdefault(a, (n, sym["st_size"]))
                elif t == "STT_OBJECT" and n:
                    self.objects[n] = (a, sym["st_size"])
        self.mapping.sort()
        self.func_addrs = sorted(self.funcs)

    def _load_relocs(self):
        self.relocs = {}
        for sec in self.elf.iter_sections():
            if isinstance(sec, RelocationSection):
                for r in sec.iter_relocations():
                    self.relocs[r["r_offset"]] = r["r_info_type"]

    def _load_plt(self):
        """Map PLT entry address -> imported symbol name (.rel.plt order)."""
        self.plt = {}
        plt = self.sections[".plt"]
        relplt = self.sections[".rel.plt"]
        dynsym = self.sections[".dynsym"]
        start = plt["sh_addr"]
        n = relplt.num_relocations()
        hdr = plt["sh_size"] - 12 * n
        for i, r in enumerate(relplt.iter_relocations()):
            self.plt[start + hdr + 12 * i] = dynsym.get_symbol(r["r_info_sym"]).name
        self.plt_header_size = hdr

    def is_code(self, addr):
        # last mapping symbol at or before addr
        lo, hi = 0, len(self.mapping)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.mapping[mid][0] <= addr:
                lo = mid + 1
            else:
                hi = mid
        return lo > 0 and self.mapping[lo - 1][1] == "a"

    def func_of(self, addr):
        lo, hi = 0, len(self.func_addrs)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.func_addrs[mid] <= addr:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            return None
        a = self.func_addrs[lo - 1]
        name, size = self.funcs[a]
        return a if addr < a + max(size, 4) else None

    def name(self, addr):
        if addr in self.funcs:
            return self.funcs[addr][0]
        if addr in self.plt:
            return self.plt[addr] + "@plt"
        return "0x%x" % addr


def demangle_map(names):
    import subprocess
    names = sorted(set(names))
    if not names:
        return {}
    res = subprocess.run(["c++filt"], input="\n".join(names), capture_output=True,
                         text=True, check=True).stdout.splitlines()
    return dict(zip(names, res))


def analyse(b: Binary, managed_natives, managed_methods):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    md.detail = True
    glob_addr = {g: b.objects[g][0] for g in JNI_GLOBALS if g in b.objects}
    addr_glob = {v: k for k, v in glob_addr.items()}
    tbl = glob_addr.get("gJAVAFunction")
    tbl_size = b.objects["gJAVAFunction"][1] if "gJAVAFunction" in b.objects else 0

    # Decode gJAVAFunction.
    table = []
    for i in range(tbl_size // 12):
        ea = tbl + 12 * i
        mid, np, sp = b.word(ea), b.word(ea + 4), b.word(ea + 8)
        table.append({"index": i, "entry_addr": "0x%x" % ea, "table_offset": "0x%x" % (12 * i),
                      "jmethodID_initial": "0x%x" % mid,
                      "name": b.cstr(np) if np else None, "signature": b.cstr(sp) if sp else None,
                      "name_ptr_reloc": b.relocs.get(ea + 4), "sig_ptr_reloc": b.relocs.get(ea + 8)})
    tbl_by_off = {12 * e["index"]: e for e in table}
    for e in table:
        if e["name"]:
            hits = [m for m in managed_methods
                    if m["name"] == e["name"] and m["descriptor"] == e["signature"]]
            e["managed_matches"] = sorted("%s->%s%s [%s]" % (m["class"], m["name"], m["descriptor"],
                                                             m["access"]) for m in hits)

    # GOT slots pointing at the JNI globals.
    got_slots = {}
    sec = b.sections[".got"]
    for a in range(sec["sh_addr"], sec["sh_addr"] + sec["sh_size"], 4):
        v = b.word(a)
        if v in addr_glob:
            got_slots[addr_glob[v]] = {"slot": "0x%x" % a, "got_offset": "0x%x" % (a - b.got),
                                       "reloc_type": b.relocs.get(a)}

    rodata = b.sections[".rodata"]

    def strargs(state):
        out = {}
        for r in ("r1", "r2", "r3"):
            v = state.get(r)
            if v and v[0] == "addr" and rodata["sh_addr"] <= v[1] < rodata["sh_addr"] + rodata["sh_size"]:
                out[r] = b.cstr(v[1])
        return out

    helpers = {a: n for a, (n, _s) in b.funcs.items()
               if n in ("_ZN7_JNIEnv14CallVoidMethodEP8_jobjectP10_jmethodIDz",
                        "_ZN7_JNIEnv13CallIntMethodEP8_jobjectP10_jmethodIDz")}

    callgraph = defaultdict(set)
    jni_calls = []
    helper_calls = []
    glob_access = defaultdict(lambda: defaultdict(set))
    plt_calls = defaultdict(set)
    indirect = defaultdict(list)   # func -> sites of non-JNI indirect calls/jumps

    for fa in b.func_addrs:
        fname, fsize = b.funcs[fa]
        if fsize == 0:
            continue
        # r0 is a JNIEnv* for Java_* exports and for C++ functions whose first
        # parameter is _JNIEnv* (e.g. JAVA_RegisterFunctions(_JNIEnv*, _jobject*)).
        is_java = fname.startswith("Java_") or ("JNIEnv" in fname and not fname.startswith("_ZN7_JNIEnv")
                                                and fname.split("JNIEnv")[0].endswith("P7_"))
        st = {}
        if is_java or fa in helpers:
            st["r0"] = ("env",)
        stack = {}
        saved = {}          # branch target -> register state at the branch
        prev_term = False   # previous instruction ended a block unconditionally
        a = fa
        end = fa + fsize
        while a < end:
            if not b.is_code(a):
                a += 4
                prev_term = True
                continue
            o = b.off(a)
            insns = list(md.disasm(b.raw[o:o + 4], a))
            if not insns:
                a += 4
                continue
            ins = insns[0]
            if prev_term and ins.address in saved:
                st = dict(saved[ins.address])
                stack = {}
            a += 4
            mn = ins.mnemonic
            ops = ins.operands
            always = ins.cc in (carm.ARM_CC_AL, carm.ARM_CC_INVALID)
            prev_term = False
            if ins.id in (carm.ARM_INS_B, carm.ARM_INS_BX) and always:
                prev_term = True
            if ins.id in (carm.ARM_INS_POP, carm.ARM_INS_LDM) and always and any(
                    op.type == carm.ARM_OP_REG and rname(ins, op.reg) == "pc" for op in ops):
                prev_term = True
            if ins.id in (carm.ARM_INS_B,) and ops and ops[0].type == carm.ARM_OP_IMM and \
                    fa <= ops[0].imm < end:
                saved.setdefault(ops[0].imm, dict(st))
            regs = [rname(ins, op.reg) if op.type == carm.ARM_OP_REG else None for op in ops]

            def val(r):
                return st.get(r)

            def setr(r, v):
                if v is None:
                    st.pop(r, None)
                else:
                    st[r] = v

            cond_ok = always
            is_call = ins.id in (carm.ARM_INS_BL, carm.ARM_INS_BLX)
            is_jump = ins.id in (carm.ARM_INS_B, carm.ARM_INS_BX)
            # --- branches / calls
            if is_call or is_jump:
                if ops and ops[0].type == carm.ARM_OP_IMM:
                    tgt = ops[0].imm
                    external = tgt in b.plt or (tgt in b.funcs and b.func_of(tgt) != fa)
                    if is_call or external:
                        if tgt in b.funcs and tgt != fa:
                            callgraph[fa].add(tgt)
                        if tgt in b.plt:
                            plt_calls[fa].add(b.plt[tgt])
                        if tgt in helpers:
                            mv = val("r2")
                            e = tbl_by_off.get(mv[1]) if mv and mv[0] == "mid" else None
                            helper_calls.append({
                                "caller": "0x%x" % fa, "caller_name": fname,
                                "site": "0x%x" % ins.address,
                                "helper": b.funcs[tgt][0],
                                "env_source": "/".join(map(str, val("r0") or ("?",))),
                                "obj_source": "/".join(map(str, val("r1") or ("?",))),
                                "table_offset": ("0x%x" % mv[1]) if mv and mv[0] == "mid" else None,
                                "java_method": e["name"] if e else None,
                                "java_signature": e["signature"] if e else None,
                                "tail_call": not is_call})
                    if is_call:
                        for r in ("r0", "r1", "r2", "r3", "r12", "lr"):
                            st.pop(r, None)
                        st["r0"] = ("ret", tgt)
                elif ins.id in (carm.ARM_INS_BLX, carm.ARM_INS_BX) and regs and regs[0]:
                    v = val(regs[0])
                    if not (v and v[0] == "jnifn") and regs[0] != "lr":
                        indirect[fa].append("0x%x" % ins.address)
                    if ins.id == carm.ARM_INS_BLX and v and v[0] == "jnifn":
                        jni_calls.append({"func": "0x%x" % fa, "func_name": fname,
                                          "site": "0x%x" % ins.address, "offset": "0x%x" % v[1],
                                          "index": v[1] // 4, "jni": JNI_FUNCS[v[1] // 4],
                                          "env_source": v[2]})
                    for r in ("r0", "r1", "r2", "r3", "r12", "lr"):
                        st.pop(r, None)
                continue
            if mn.startswith("ldr") and len(ops) == 2 and ops[1].type == carm.ARM_OP_MEM:
                rd = regs[0]
                mem = ops[1].mem
                base = rname(ins, mem.base) if mem.base else None
                idx = rname(ins, mem.index) if mem.index else None
                disp = mem.disp
                if rd == "pc" and not (val(base) and val(base)[0] == "envfn"):
                    indirect[fa].append("0x%x" % ins.address)
                if mn != "ldr":
                    setr(rd, None)
                    continue
                if base == "pc" and idx is None:
                    setr(rd, ("const", b.word(ins.address + 8 + disp)))
                    continue
                bv = val(base)
                if idx:
                    iv = val(idx)
                    if bv and iv and bv[0] == "addr" and iv[0] == "const" and ops[1].subtracted is False:
                        target = (bv[1] + iv[1]) & 0xffffffff
                        pv = b.word(target)
                        setr(rd, ("ptr", pv) if pv is not None else None)
                    else:
                        setr(rd, None)
                    continue
                if bv is None:
                    if base == "sp" and disp in stack and ins.writeback is False:
                        setr(rd, stack[disp])
                    else:
                        setr(rd, None)
                    continue
                kind = bv[0]
                if kind == "ptr":
                    ga = bv[1] + disp
                    g = addr_glob.get(bv[1])
                    if g:
                        glob_access[fa][g].add("load")
                    if ga == glob_addr.get("gJavaEnv"):
                        nv = ("env", "gJavaEnv")
                    elif bv[1] == tbl:
                        nv = ("mid", disp)
                    elif g:
                        nv = ("glob", g)
                    else:
                        nv = ("mem", ga)
                    setr(rd, nv)
                elif kind == "env" and disp == 0:
                    setr(rd, ("envfn", bv[1] if len(bv) > 1 else "arg0"))
                elif kind == "envfn":
                    if rd == "pc":
                        jni_calls.append({"func": "0x%x" % fa, "func_name": fname,
                                          "site": "0x%x" % ins.address, "offset": "0x%x" % disp,
                                          "index": disp // 4, "jni": JNI_FUNCS[disp // 4],
                                          "env_source": bv[1],
                                          "string_args": strargs(st)})
                        for r in ("r0", "r1", "r2", "r3", "r12", "lr"):
                            st.pop(r, None)
                    else:
                        setr(rd, ("jnifn", disp, bv[1]))
                else:
                    setr(rd, None)
                continue
            if mn in ("str",) and len(ops) == 2 and ops[1].type == carm.ARM_OP_MEM:
                mem = ops[1].mem
                base = rname(ins, mem.base) if mem.base else None
                bv = val(base)
                if bv and bv[0] == "ptr" and bv[1] in addr_glob and mem.index == 0:
                    glob_access[fa][addr_glob[bv[1]]].add("store")
                if base == "sp" and not ins.writeback and mem.index == 0:
                    stack[mem.disp] = val(regs[0])
                continue
            if mn in ("push", "pop", "stmdb", "ldm", "stm") or (regs and regs[0] == "sp"):
                if mn in ("pop", "ldm") and not prev_term:
                    for op in ops:
                        if op.type == carm.ARM_OP_REG:
                            st.pop(rname(ins, op.reg), None)
                stack = {}
                continue
            if mn == "add" and len(ops) == 3 and cond_ok:
                rd = regs[0]
                r1v = ("addr", ins.address + 8) if regs[1] == "pc" else val(regs[1])
                if ops[2].type == carm.ARM_OP_REG and ops[2].shift.type == 0:
                    r2v = ("addr", ins.address + 8) if regs[2] == "pc" else val(regs[2])
                elif ops[2].type == carm.ARM_OP_IMM:
                    r2v = ("const", ops[2].imm)
                else:
                    r2v = None
                if r1v and r2v and {r1v[0], r2v[0]} <= {"addr", "const"} and \
                        (r1v[0] == "addr") != (r2v[0] == "addr"):
                    setr(rd, ("addr", (r1v[1] + r2v[1]) & 0xffffffff))
                elif r1v and r2v and r1v[0] == "ptr" and r2v[0] == "const":
                    setr(rd, ("ptr", (r1v[1] + r2v[1]) & 0xffffffff))
                else:
                    setr(rd, None)
                continue
            if mn == "mov" and len(ops) == 2 and cond_ok:
                if ops[1].type == carm.ARM_OP_REG and ops[1].shift.type == 0:
                    setr(regs[0], val(regs[1]))
                elif ops[1].type == carm.ARM_OP_IMM:
                    setr(regs[0], ("const", ops[1].imm))
                else:
                    setr(regs[0], None)
                continue
            # default: clobber destination register(s)
            if ops and ops[0].type == carm.ARM_OP_REG and mn not in ("cmp", "cmn", "tst", "teq") \
                    and not mn.startswith("vst") and not mn.startswith("str"):
                st.pop(regs[0], None)

    # Reachability from Java_* exports.
    def reach(start):
        seen = set()
        todo = [start]
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            todo.extend(callgraph.get(x, ()))
        return seen

    env_users = {fa for fa, gs in glob_access.items() if "load" in gs.get("gJavaEnv", ())}
    exports = []
    managed_by_sym = {}
    for m in managed_natives:
        cls = m["class"][1:-1]
        sym = "Java_" + jni_mangle(cls) + "_" + jni_mangle(m["name"])
        managed_by_sym[sym] = m
    for n, (a, s, t) in sorted(b.dynsym.items(), key=lambda kv: kv[1][0]):
        if not n.startswith("Java_"):
            continue
        r = reach(a)
        m = managed_by_sym.get(n)
        exports.append({
            "symbol": n, "addr": "0x%x" % a, "size": s,
            "java": ("%s->%s%s" % (m["class"], m["name"], m["descriptor"])) if m else None,
            "direct_jni_calls": sorted({(c["jni"]) for c in jni_calls if int(c["func"], 16) == a}),
            "reaches_cached_env_users": sorted(b.name(x) for x in r if x in env_users),
            "reaches_gl_imports": sorted({p for x in r for p in plt_calls.get(x, ()) if p.startswith("gl")}),
            "reachable_function_count": len(r),
            "reachable_indirect_call_sites": sorted(
                "%s@%s" % (b.name(x), site) for x in r for site in indirect.get(x, ())),
        })
    missing = sorted(sym for sym in managed_by_sym if sym not in b.dynsym)

    # callers of JAVA* wrappers (engine side entry points).
    wrappers = sorted({int(h["caller"], 16) for h in helper_calls} |
                      {int(c["func"], 16) for c in jni_calls if c["env_source"] == "gJavaEnv"})
    rev = defaultdict(set)
    for x, ys in callgraph.items():
        for y in ys:
            rev[y].add(x)
    wrapper_info = []
    for w in wrappers:
        wrapper_info.append({"addr": "0x%x" % w, "name": b.name(w), "size": b.funcs[w][1],
                             "direct_callers": sorted(b.name(c) for c in rev.get(w, ())),
                             "jni_calls": sorted({c["jni"] for c in jni_calls if int(c["func"], 16) == w}),
                             "java_methods": sorted({h["java_method"] or "?" for h in helper_calls
                                                     if int(h["caller"], 16) == w})})

    glob_out = {}
    for g in JNI_GLOBALS:
        if g not in glob_addr:
            continue
        glob_out[g] = {"addr": "0x%x" % glob_addr[g], "size": b.objects[g][1],
                       "section": next((s.name for s in b.elf.iter_sections()
                                        if s["sh_addr"] <= glob_addr[g] < s["sh_addr"] + s["sh_size"]
                                        and s["sh_flags"] & 2), None),
                       "got": got_slots.get(g),
                       "stored_by": sorted(b.name(f) for f, gs in glob_access.items()
                                           if "store" in gs.get(g, ())),
                       "loaded_by_count": sum(1 for f, gs in glob_access.items()
                                              if "load" in gs.get(g, ()))}

    return {
        "tag": b.tag, "path": b.path, "sha256": b.sha256,
        "plt_header_size": b.plt_header_size,
        "java_exports": exports,
        "declared_natives_without_export": missing,
        "jni_globals": glob_out,
        "gJAVAFunction": {"addr": "0x%x" % tbl if tbl else None, "size": tbl_size,
                          "entries": table},
        "jnienv_call_sites": sorted(jni_calls, key=lambda c: int(c["site"], 16)),
        "helper_call_sites": sorted(helper_calls, key=lambda c: int(c["site"], 16)),
        "java_callback_wrappers": wrapper_info,
    }


def check_map(map_path, results):
    """Verify addresses/sizes/symbols recorded in docs/JNI_MAP.json.

    The map is a JSON array of objects; entries with direction
    'java_to_native' carry native_symbol + v7a/v5 {addr,size} (or "MISSING"),
    entries with direction 'native_to_java' carry native_wrapper.{symbol,v7a,v5}
    and gJAVAFunction.{index,name,signature,v7a_entry_addr,v5_entry_addr}.
    """
    problems = []
    jmap = json.load(open(map_path))
    entries = jmap if isinstance(jmap, list) else jmap.get("entries", [])
    by_tag = {r["tag"]: r for r in results}
    checked = 0
    for e in entries:
        d = e.get("direction")
        for tag, res in by_tag.items():
            if d == "java_to_native":
                rec = e.get(tag)
                if rec is None:
                    continue
                exp = {x["symbol"]: x for x in res["java_exports"]}
                sym = e.get("native_symbol")
                checked += 1
                if rec == "MISSING":
                    if sym in exp or sym not in res["declared_natives_without_export"]:
                        problems.append("%s %s: map says MISSING" % (tag, sym))
                    continue
                x = exp.get(sym)
                if x is None:
                    problems.append("%s %s: not exported" % (tag, sym))
                elif x["addr"] != rec.get("addr") or x["size"] != rec.get("size"):
                    problems.append("%s %s: map %s/%s != dynsym %s/%s" % (
                        tag, sym, rec.get("addr"), rec.get("size"), x["addr"], x["size"]))
                if x is not None and x["java"] != "%s->%s%s" % (
                        e.get("java_class"), e.get("java_method"), e.get("descriptor")):
                    problems.append("%s %s: java mismatch %s" % (tag, sym, x["java"]))
            elif d == "native_to_java":
                w = e.get("native_wrapper", {})
                rec = w.get(tag)
                wr = {x["name"]: x for x in res["java_callback_wrappers"]}
                checked += 1
                x = wr.get(w.get("symbol"))
                if x is None:
                    problems.append("%s wrapper %s not found" % (tag, w.get("symbol")))
                elif rec and (x["addr"] != rec.get("addr") or x["size"] != rec.get("size")):
                    problems.append("%s wrapper %s: map %s/%s != %s/%s" % (
                        tag, w.get("symbol"), rec.get("addr"), rec.get("size"), x["addr"], x["size"]))
                elif x is not None and e.get("java_method") not in x["java_methods"]:
                    problems.append("%s wrapper %s does not call %s" % (tag, w.get("symbol"),
                                                                        e.get("java_method")))
                g = e.get("gJAVAFunction")
                if g:
                    ent = res["gJAVAFunction"]["entries"][g["index"]]
                    if ent["name"] != e.get("java_method") or ent["signature"] != e.get("descriptor") \
                            or ent["entry_addr"] != g.get(tag + "_entry_addr"):
                        problems.append("%s gJAVAFunction[%d] mismatch" % (tag, g["index"]))
    return problems, checked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", action="append", required=True, help="tag=path")
    ap.add_argument("--managed", required=True, help="managed_shell_scan.json")
    ap.add_argument("--check-map", help="docs/JNI_MAP.json to verify")
    a = ap.parse_args()
    managed = json.load(open(a.managed))
    natives = managed["native_methods"]
    results = []
    for spec in a.bin:
        tag, path = spec.split("=", 1)
        results.append(analyse(Binary(tag, path), natives, managed.get("game_shell_methods", [])))
    names = []
    for r in results:
        names += [c["func_name"] for c in r["jnienv_call_sites"]]
        names += [c["caller_name"] for c in r["helper_call_sites"]]
    dm = demangle_map(names)
    for r in results:
        for c in r["jnienv_call_sites"]:
            c["func_demangled"] = dm.get(c["func_name"], c["func_name"])
        for c in r["helper_call_sites"]:
            c["caller_demangled"] = dm.get(c["caller_name"], c["caller_name"])
    if a.check_map:
        probs, checked = check_map(a.check_map, results)
        for p in probs:
            print("MAP MISMATCH:", p, file=sys.stderr)
        print("check-map: %d record(s) checked, %d problem(s)" % (checked, len(probs)),
              file=sys.stderr)
        sys.exit(1 if probs else 0)
    json.dump({"schema": "snailmail.jni_native_scan/1",
               "jni_function_table": {"entries": len(JNI_FUNCS),
                                      "note": "index = byte offset / 4 on 32-bit ARM"},
               "binaries": results}, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

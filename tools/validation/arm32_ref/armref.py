#!/usr/bin/env python3
"""armref -- run functions of the ORIGINAL ARM32 libsnailmail.so under Unicorn.

ANALYSIS-ONLY. Never shipped, never linked into or reachable from the port.
It is a test oracle for differential testing of reconstructed code against the
original machine code (see docs/TESTING.md).

What it does
  * Maps every PT_LOAD of the ELF32/ARM/ET_DYN image at a chosen base (default
    0x40000000); .bss and other memsz > filesz tails are zero.
  * Applies dynamic relocations from DT_REL / DT_JMPREL: R_ARM_RELATIVE,
    R_ARM_ABS32, R_ARM_GLOB_DAT, R_ARM_JUMP_SLOT. Any other type is an error.
  * Resolves imports:
      - functions -> an 8-byte trap stub (`bx lr`) whose execution is intercepted
        by a UC_HOOK_CODE and dispatched to a Python handler. A stub WITHOUT a
        handler raises UnknownImportError; it never returns a fake value.
      - data objects -> real storage only for the ones modelled here
        (__stack_chk_guard); all others point into an UNMAPPED poison region,
        so any access faults (GuestFault).
  * Provides a small heap (malloc/free with double-free/unknown-pointer
    detection), a file model for dup/fdopen/fseek/ftell/fread/fclose over
    Python byte strings, and a JNIEnv function table whose entries are
    individually trapping stubs (only explicitly provided JNI handlers work).
  * call(symbol, *int_args) follows AAPCS (r0-r3, then stack), returns r0,
    and enforces an instruction-count limit.
  * .init_array constructors are NOT run (call them explicitly if needed).

Only routines whose dependencies are fully stubbed with known semantics are
suitable (parsing, hashing, table code). GL / timing / real JNI behaviour is
out of scope.
"""
from __future__ import annotations

import hashlib
import io
import struct
import subprocess
from typing import Callable

from elftools.elf.dynamic import DynamicSegment
from elftools.elf.elffile import ELFFile
from unicorn import (UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_ARM, UC_PROT_ALL, Uc, UcError)
from unicorn import arm_const as A

PAGE = 0x1000
R_ARM_ABS32 = 2
R_ARM_GLOB_DAT = 21
R_ARM_JUMP_SLOT = 22
R_ARM_RELATIVE = 23

STUB_BASE = 0x7F000000
STUB_SIZE = 0x00100000
POISON_BASE = 0x7E000000        # never mapped: data imports without a model point here
HEAP_BASE = 0x80000000
STACK_BASE = 0xF0000000
BX_LR = struct.pack("<I", 0xE12FFF1E)
NOP = struct.pack("<I", 0xE320F000)
MASK32 = 0xFFFFFFFF

JNI_PRIMS = ["Boolean", "Byte", "Char", "Short", "Int", "Long", "Float", "Double"]
JNI_CALL_TYPES = ["Object"] + JNI_PRIMS + ["Void"]


def _jni_function_names() -> list[str]:
    """JNINativeInterface slot order (jni.h), index = offset / 4."""
    n = ["reserved0", "reserved1", "reserved2", "reserved3", "GetVersion", "DefineClass", "FindClass",
         "FromReflectedMethod", "FromReflectedField", "ToReflectedMethod", "GetSuperclass",
         "IsAssignableFrom", "ToReflectedField", "Throw", "ThrowNew", "ExceptionOccurred",
         "ExceptionDescribe", "ExceptionClear", "FatalError", "PushLocalFrame", "PopLocalFrame",
         "NewGlobalRef", "DeleteGlobalRef", "DeleteLocalRef", "IsSameObject", "NewLocalRef",
         "EnsureLocalCapacity", "AllocObject", "NewObject", "NewObjectV", "NewObjectA",
         "GetObjectClass", "IsInstanceOf", "GetMethodID"]
    n += [f"Call{t}Method{s}" for t in JNI_CALL_TYPES for s in ("", "V", "A")]
    n += [f"CallNonvirtual{t}Method{s}" for t in JNI_CALL_TYPES for s in ("", "V", "A")]
    n += ["GetFieldID"] + [f"Get{t}Field" for t in ["Object"] + JNI_PRIMS]
    n += [f"Set{t}Field" for t in ["Object"] + JNI_PRIMS]
    n += ["GetStaticMethodID"] + [f"CallStatic{t}Method{s}" for t in JNI_CALL_TYPES for s in ("", "V", "A")]
    n += ["GetStaticFieldID"] + [f"GetStatic{t}Field" for t in ["Object"] + JNI_PRIMS]
    n += [f"SetStatic{t}Field" for t in ["Object"] + JNI_PRIMS]
    n += ["NewString", "GetStringLength", "GetStringChars", "ReleaseStringChars", "NewStringUTF",
          "GetStringUTFLength", "GetStringUTFChars", "ReleaseStringUTFChars", "GetArrayLength",
          "NewObjectArray", "GetObjectArrayElement", "SetObjectArrayElement"]
    n += [f"New{t}Array" for t in JNI_PRIMS]
    n += [f"Get{t}ArrayElements" for t in JNI_PRIMS]
    n += [f"Release{t}ArrayElements" for t in JNI_PRIMS]
    n += [f"Get{t}ArrayRegion" for t in JNI_PRIMS]
    n += [f"Set{t}ArrayRegion" for t in JNI_PRIMS]
    n += ["RegisterNatives", "UnregisterNatives", "MonitorEnter", "MonitorExit", "GetJavaVM",
          "GetStringRegion", "GetStringUTFRegion", "GetPrimitiveArrayCritical",
          "ReleasePrimitiveArrayCritical", "GetStringCritical", "ReleaseStringCritical",
          "NewWeakGlobalRef", "DeleteWeakGlobalRef", "ExceptionCheck", "NewDirectByteBuffer",
          "GetDirectBufferAddress", "GetDirectBufferCapacity", "GetObjectRefType"]
    return n


JNI_FUNCTIONS = _jni_function_names()
assert len(JNI_FUNCTIONS) == 233
assert JNI_FUNCTIONS[0x178 // 4] == "GetFieldID" and JNI_FUNCTIONS[0x190 // 4] == "GetIntField"
assert JNI_FUNCTIONS[0x2C0 // 4] == "NewByteArray" and JNI_FUNCTIONS[0x340 // 4] == "SetByteArrayRegion"


class ArmRefError(Exception):
    pass


class UnknownImportError(ArmRefError):
    pass


class InstructionLimitExceeded(ArmRefError):
    pass


class GuestFault(ArmRefError):
    pass


class _File:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0


def _page_down(x: int) -> int:
    return x & ~(PAGE - 1)


def _page_up(x: int) -> int:
    return (x + PAGE - 1) & ~(PAGE - 1)


class ArmRef:
    def __init__(self, so_path: str, base: int = 0x40000000, expected_sha256: str | None = None,
                 stack_size: int = 0x100000, heap_size: int = 0x08000000):
        raw = open(so_path, "rb").read()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and self.sha256 != expected_sha256:
            raise ArmRefError(f"{so_path}: sha256 {self.sha256} != expected {expected_sha256}")
        self.path = so_path
        self.base = base
        self.elf = ELFFile(io.BytesIO(raw))
        h = self.elf.header
        if (self.elf.elfclass != 32 or not self.elf.little_endian or h["e_machine"] != "EM_ARM"
                or h["e_type"] != "ET_DYN"):
            raise ArmRefError("expected ELF32 little-endian ARM ET_DYN")
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        # Switch to USR mode once, BEFORE any SP/LR writes (SP/LR are banked
        # per mode; changing mode later would expose zeroed USR registers).
        self.uc.reg_write(A.UC_ARM_REG_CPSR, 0x10)
        self._enable_vfp()
        self._raw = raw
        self._map_segments()
        self._stub_next = STUB_BASE + 0x10
        self._stub_names: dict[int, str] = {}
        self._import_addr: dict[str, int] = {}
        self.handlers: dict[str, Callable[["ArmRef"], int | None]] = {}
        self.RET_ADDR = STUB_BASE
        self.uc.mem_map(STUB_BASE, STUB_SIZE, UC_PROT_ALL)
        self.uc.mem_write(STUB_BASE, BX_LR + NOP)
        self.uc.hook_add(UC_HOOK_CODE, self._on_stub, begin=STUB_BASE + 0x10, end=STUB_BASE + STUB_SIZE - 1)
        self._pending: BaseException | None = None
        self.heap_base, self.heap_size = HEAP_BASE, heap_size
        self.uc.mem_map(HEAP_BASE, heap_size, UC_PROT_ALL)
        self._heap_next = HEAP_BASE + 0x10
        self.allocs: dict[int, int] = {}
        self.freed: set[int] = set()
        self.stack_top = STACK_BASE + stack_size
        self.uc.mem_map(STACK_BASE, stack_size, UC_PROT_ALL)
        self.files: dict[int, _File] = {}
        self.fds: dict[int, bytes] = {}
        self._next_fd = 100
        self.data_imports: dict[str, int] = {}
        self._symbols = self._load_symbols()
        self._install_default_handlers()
        self.relocation_counts = self._relocate()

    # ------------------------------------------------------------------ setup
    def _enable_vfp(self) -> None:
        cpacr = self.uc.reg_read(A.UC_ARM_REG_C1_C0_2)
        self.uc.reg_write(A.UC_ARM_REG_C1_C0_2, cpacr | (0xF << 20))
        self.uc.reg_write(A.UC_ARM_REG_FPEXC, 0x40000000)

    def _map_segments(self) -> None:
        loads = [s for s in self.elf.iter_segments() if s["p_type"] == "PT_LOAD"]
        spans = []
        for s in loads:
            lo = _page_down(self.base + s["p_vaddr"])
            hi = _page_up(self.base + s["p_vaddr"] + s["p_memsz"])
            spans.append([lo, hi])
        spans.sort()
        merged: list[list[int]] = []
        for lo, hi in spans:
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        for lo, hi in merged:
            self.uc.mem_map(lo, hi - lo, UC_PROT_ALL)
        for s in loads:
            data = self._raw[s["p_offset"]:s["p_offset"] + s["p_filesz"]]
            self.uc.mem_write(self.base + s["p_vaddr"], data)
        self.segments = [(s["p_vaddr"], s["p_memsz"], s["p_filesz"]) for s in loads]

    def _load_symbols(self) -> dict[str, int]:
        out: dict[str, int] = {}
        raw_names = []
        for secname in (".dynsym", ".symtab"):
            sec = self.elf.get_section_by_name(secname)
            if sec is None:
                continue
            for sym in sec.iter_symbols():
                if sym.name and sym["st_shndx"] != "SHN_UNDEF" and not sym.name.startswith("$") \
                        and sym["st_info"]["type"] in ("STT_FUNC", "STT_OBJECT"):
                    out.setdefault(sym.name, sym["st_value"])
                    raw_names.append(sym.name)
        try:
            dem = subprocess.run(["c++filt"], input="\n".join(raw_names), capture_output=True, text=True,
                                 check=True).stdout.split("\n")
            for r, d in zip(raw_names, dem):
                if d and d != r:
                    out.setdefault(d, out[r])
        except (OSError, subprocess.CalledProcessError):
            pass
        return out

    def _relocate(self) -> dict[str, int]:
        dyn = next((s for s in self.elf.iter_segments() if isinstance(s, DynamicSegment)), None)
        if dyn is None:
            raise ArmRefError("no PT_DYNAMIC")
        counts: dict[str, int] = {}
        for _tag, table in dyn.get_relocation_tables().items():
            if table.is_RELA():
                raise ArmRefError("RELA relocations not expected on ARM32")
            for rel in table.iter_relocations():
                t = rel["r_info_type"]
                where = self.base + rel["r_offset"]
                symidx = rel["r_info_sym"]
                sym = dyn.get_symbol(symidx) if symidx else None
                if t == R_ARM_RELATIVE:
                    self.write_u32(where, (self.read_u32(where) + self.base) & MASK32)
                    counts["R_ARM_RELATIVE"] = counts.get("R_ARM_RELATIVE", 0) + 1
                elif t in (R_ARM_ABS32, R_ARM_GLOB_DAT, R_ARM_JUMP_SLOT):
                    s = self._resolve_symbol(sym, t)
                    if t == R_ARM_ABS32:
                        self.write_u32(where, (self.read_u32(where) + s) & MASK32)
                    else:
                        self.write_u32(where, s)
                    key = {R_ARM_ABS32: "R_ARM_ABS32", R_ARM_GLOB_DAT: "R_ARM_GLOB_DAT",
                           R_ARM_JUMP_SLOT: "R_ARM_JUMP_SLOT"}[t]
                    counts[key] = counts.get(key, 0) + 1
                else:
                    raise ArmRefError(f"unsupported relocation type {t} at {rel['r_offset']:#x}")
        return counts

    def _resolve_symbol(self, sym, rtype: int) -> int:
        if sym is None:
            return 0
        if sym["st_shndx"] != "SHN_UNDEF":
            return self.base + sym["st_value"]
        name = sym.name
        stype = sym["st_info"]["type"]
        if rtype == R_ARM_JUMP_SLOT or stype == "STT_FUNC":
            return self._import_stub(name)
        if name == "__stack_chk_guard":
            if name not in self.data_imports:
                a = self.alloc(4)
                self.write_u32(a, 0x5AC3C35A)
                self.data_imports[name] = a
            return self.data_imports[name]
        if rtype == R_ARM_ABS32 and stype == "STT_NOTYPE":
            # address-taken function without type info (e.g. unwinder hooks)
            return self._import_stub(name)
        # unmodelled data import: poison address, any access faults
        addr = POISON_BASE + 0x100 * (len(self.data_imports) + 1)
        self.data_imports[name] = addr
        return addr

    def _import_stub(self, name: str) -> int:
        if name in self._import_addr:
            return self._import_addr[name]
        a = self._stub_next
        self._stub_next += 8
        if self._stub_next >= STUB_BASE + STUB_SIZE:
            raise ArmRefError("stub region exhausted")
        self.uc.mem_write(a, BX_LR + NOP)
        self._stub_names[a] = name
        self._import_addr[name] = a
        return a

    def make_callback(self, name: str, fn: Callable[["ArmRef"], int | None]) -> int:
        """Guest-callable function pointer that runs `fn` (AAPCS args via arg())."""
        key = f"callback:{name}"
        a = self._import_stub(key)
        self.handlers[key] = fn
        return a

    # ------------------------------------------------------------- dispatch
    def _on_stub(self, uc, address, size, user_data):
        name = self._stub_names.get(address)
        if name is None:
            return
        handler = self.handlers.get(name)
        if handler is None:
            self._pending = UnknownImportError(
                f"import '{name}' called from lr={self.reg('lr'):#x} (module+{self.reg('lr') - self.base:#x})"
                " has no handler")
            uc.emu_stop()
            return
        try:
            ret = handler(self)
        except BaseException as e:  # propagate after emu_start returns
            self._pending = e
            uc.emu_stop()
            return
        if ret is not None:
            uc.reg_write(A.UC_ARM_REG_R0, ret & MASK32)

    # ------------------------------------------------------------ registers
    _REGS = {"r0": A.UC_ARM_REG_R0, "r1": A.UC_ARM_REG_R1, "r2": A.UC_ARM_REG_R2, "r3": A.UC_ARM_REG_R3,
             "sp": A.UC_ARM_REG_SP, "lr": A.UC_ARM_REG_LR, "pc": A.UC_ARM_REG_PC}

    def reg(self, name: str) -> int:
        return self.uc.reg_read(self._REGS[name])

    def arg(self, i: int) -> int:
        """AAPCS integer argument i at stub entry."""
        if i < 4:
            return self.uc.reg_read([A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3][i])
        return self.read_u32(self.reg("sp") + 4 * (i - 4))

    @staticmethod
    def s32(v: int) -> int:
        v &= MASK32
        return v - (1 << 32) if v & 0x80000000 else v

    # --------------------------------------------------------------- memory
    def read(self, addr: int, n: int) -> bytes:
        try:
            return bytes(self.uc.mem_read(addr, n))
        except UcError as e:
            raise GuestFault(f"read {n} bytes at {addr:#x}: {e}") from None

    def write(self, addr: int, data: bytes) -> None:
        try:
            self.uc.mem_write(addr, bytes(data))
        except UcError as e:
            raise GuestFault(f"write {len(data)} bytes at {addr:#x}: {e}") from None

    def read_u32(self, addr: int) -> int:
        return struct.unpack("<I", self.read(addr, 4))[0]

    def write_u32(self, addr: int, v: int) -> None:
        self.write(addr, struct.pack("<I", v & MASK32))

    def read_cstr(self, addr: int, limit: int = 1 << 20) -> bytes:
        out = bytearray()
        while len(out) < limit:
            chunk = self.read(addr + len(out), 64)
            k = chunk.find(b"\0")
            if k >= 0:
                return bytes(out + chunk[:k])
            out += chunk
        raise GuestFault(f"unterminated string at {addr:#x}")

    def alloc(self, n: int, align: int = 8) -> int:
        a = (self._heap_next + align - 1) & ~(align - 1)
        size = max(n, 1)
        if a + size > self.heap_base + self.heap_size:
            raise ArmRefError(f"guest heap exhausted allocating {n} bytes")
        self._heap_next = a + size
        self.allocs[a] = n
        return a

    def alloc_bytes(self, data: bytes, align: int = 8) -> int:
        a = self.alloc(len(data), align)
        self.write(a, data)
        return a

    def alloc_cstr(self, s: bytes | str) -> int:
        if isinstance(s, str):
            s = s.encode("latin-1")
        if b"\0" in s:
            raise ValueError("embedded NUL")
        return self.alloc_bytes(s + b"\0", 1)

    def free(self, a: int) -> None:
        if a == 0:
            return
        if a in self.freed:
            raise GuestFault(f"double free of {a:#x}")
        if a not in self.allocs:
            raise GuestFault(f"free of unknown pointer {a:#x}")
        self.freed.add(a)
        del self.allocs[a]

    # -------------------------------------------------------------- symbols
    def sym(self, name: str) -> int:
        """Absolute runtime address of a defined symbol (raw or demangled name)."""
        if name not in self._symbols:
            raise KeyError(name)
        return self.base + self._symbols[name]

    # ----------------------------------------------------------------- call
    def call(self, target: str | int, *args: int, max_insns: int = 10_000_000) -> int:
        addr = self.sym(target) if isinstance(target, str) else self.base + target
        if addr & 1:
            raise ArmRefError("Thumb entry points are not supported (library is ARM-only)")
        sp = self.stack_top - 0x100
        stack_args = [a & MASK32 for a in args[4:]]
        sp -= 4 * len(stack_args)
        sp &= ~7
        for k, v in enumerate(stack_args):
            self.write_u32(sp + 4 * k, v)
        regs = [A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3]
        for k, r in enumerate(regs):
            self.uc.reg_write(r, (args[k] & MASK32) if k < len(args) else 0)
        self.uc.reg_write(A.UC_ARM_REG_SP, sp)
        self.uc.reg_write(A.UC_ARM_REG_LR, self.RET_ADDR)
        self._pending = None
        try:
            self.uc.emu_start(addr, self.RET_ADDR, count=max_insns)
        except UcError as e:
            pc = self.reg("pc")
            raise GuestFault(f"{e} at pc={pc:#x} (module+{pc - self.base:#x})") from None
        if self._pending is not None:
            p, self._pending = self._pending, None
            raise p
        pc = self.reg("pc")
        if pc != self.RET_ADDR:
            raise InstructionLimitExceeded(f"stopped at pc={pc:#x} (module+{pc - self.base:#x}) "
                                           f"after {max_insns} instructions")
        return self.reg("r0")

    # ------------------------------------------------------- libc handlers
    def _install_default_handlers(self) -> None:
        h = self.handlers

        def malloc(r):
            return r.alloc(r.arg(0))

        def free(r):
            r.free(r.arg(0))

        def memset(r):
            d, c, n = r.arg(0), r.arg(1) & 0xFF, r.arg(2)
            if n:
                r.write(d, bytes([c]) * n)
            return d

        def memcpy(r):
            d, s, n = r.arg(0), r.arg(1), r.arg(2)
            if n:
                r.write(d, r.read(s, n))
            return d

        def strlen(r):
            return len(r.read_cstr(r.arg(0)))

        def strcpy(r):
            d, s = r.arg(0), r.arg(1)
            r.write(d, r.read_cstr(s) + b"\0")
            return d

        def strcat(r):
            d, s = r.arg(0), r.arg(1)
            r.write(d + len(r.read_cstr(d)), r.read_cstr(s) + b"\0")
            return d

        def dup(r):
            fd = r.s32(r.arg(0))
            if fd not in r.fds:
                return -1
            nfd = r._next_fd
            r._next_fd += 1
            r.fds[nfd] = r.fds[fd]
            return nfd

        def fdopen(r):
            fd = r.s32(r.arg(0))
            mode = r.read_cstr(r.arg(1))
            if fd not in r.fds or mode not in (b"r", b"rb"):
                return 0
            fp = r.alloc(16)
            r.files[fp] = _File(r.fds[fd])
            return fp

        def fseek(r):
            f = r._file(r.arg(0))
            off, whence = r.s32(r.arg(1)), r.s32(r.arg(2))
            base = {0: 0, 1: f.pos, 2: len(f.data)}.get(whence)
            if base is None or base + off < 0:
                return -1
            f.pos = base + off
            return 0

        def ftell(r):
            return r._file(r.arg(0)).pos

        def fread(r):
            ptr, size, n, f = r.arg(0), r.arg(1), r.arg(2), r._file(r.arg(3))
            want = size * n
            chunk = f.data[f.pos:f.pos + want] if f.pos < len(f.data) else b""
            if chunk:
                r.write(ptr, chunk)
            f.pos += len(chunk)
            return len(chunk) // size if size else 0

        def fclose(r):
            fp = r.arg(0)
            r._file(fp)
            del r.files[fp]
            return 0

        def stack_chk_fail(r):
            raise GuestFault("__stack_chk_fail called (stack canary corrupted)")

        def abort(r):
            raise GuestFault("abort() called")

        for name, fn in [("malloc", malloc), ("free", free), ("memset", memset), ("memcpy", memcpy),
                         ("memmove", memcpy), ("strlen", strlen), ("strcpy", strcpy), ("strcat", strcat),
                         ("dup", dup), ("fdopen", fdopen), ("fseek", fseek), ("ftell", ftell),
                         ("fread", fread), ("fclose", fclose), ("__stack_chk_fail", stack_chk_fail),
                         ("abort", abort)]:
            h[name] = fn

    def _file(self, fp: int) -> _File:
        if fp not in self.files:
            raise GuestFault(f"FILE* {fp:#x} is not an open stream")
        return self.files[fp]

    def add_fd(self, data: bytes) -> int:
        fd = self._next_fd
        self._next_fd += 1
        self.fds[fd] = bytes(data)
        return fd

    # ----------------------------------------------------------------- JNI
    def make_jnienv(self, jni_handlers: dict[str, Callable[["ArmRef"], int | None]]) -> int:
        """Builds a JNIEnv (pointer to a function table). Each slot is its own
        trap stub named JNI:<Function>; slots without a handler raise."""
        table = self.alloc(4 * len(JNI_FUNCTIONS), 4)
        for i, fname in enumerate(JNI_FUNCTIONS):
            key = f"JNI:{fname}"
            self.write_u32(table + 4 * i, self._import_stub(key))
            if fname in jni_handlers:
                self.handlers[key] = jni_handlers[fname]
        env = self.alloc(4, 4)
        self.write_u32(env, table)
        return env


if __name__ == "__main__":
    import sys
    so = sys.argv[1] if len(sys.argv) > 1 else "work/apk_unzip/lib/armeabi-v7a/libsnailmail.so"
    ref = ArmRef(so)
    print(f"loaded {so} sha256={ref.sha256} base={ref.base:#x} relocations={ref.relocation_counts}")
    s = ref.alloc_cstr(b"RANDTABLE.BIN")
    print("cRHash::Calc('RANDTABLE.BIN') =", ref.call("cRHash::Calc(char*)", 0, s))

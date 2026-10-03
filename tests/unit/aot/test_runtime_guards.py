#!/usr/bin/env python3
"""Compile actual allocator/dispatch bodies with an isolated memory fixture.

The production runtime depends on POSIX mmap and pthreads. This fixture runs
its unchanged heap and dispatch source on Windows as well as POSIX, without
pretending to validate mmap, threading, or the complete game runtime. No APK
or generated translation is needed. Invalid operations must terminate before
they can alias an allocation, index a free list, or dispatch a JNI callback.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
FATAL_EXIT = 86

PREAMBLE = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#define AOT_HEAP_BASE 0x10000000u
#define AOT_HEAP_END 0xE0000000u
#define AOT_JNI_THUNK_BASE 0x00020000u
#define AOT_JNI_THUNK_COUNT 240u
#define AOT_B 0x00100000u
typedef struct { uint32_t r[16]; } aot_cpu;
typedef void (*aot_fn)(aot_cpu *);
static int g_lock;
static uint8_t memory[256];
static uint32_t dispatched = UINT32_MAX;
static void aot_fatal(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    exit(86);
}
static void aot_lock_acquire(int *lock) {
    if (*lock) exit(87);
    *lock = 1;
}
static void aot_lock_release(int *lock) { *lock = 0; }
static size_t offset(uint32_t a) {
    if (a < AOT_HEAP_BASE || (uint64_t)a + 4 > AOT_HEAP_BASE + sizeof memory) exit(88);
    return a - AOT_HEAP_BASE;
}
static uint32_t AOT_LD32(uint32_t a) {
    uint32_t v;
    memcpy(&v, memory + offset(a), sizeof v);
    return v;
}
static void AOT_ST32(uint32_t a, uint32_t v) { memcpy(memory + offset(a), &v, sizeof v); }
static void commit(uint32_t lo, uint64_t hi) { (void)lo; (void)hi; }
static aot_fn aot_lookup_function(uint32_t target) { (void)target; return NULL; }
static void aot_jni_dispatch(aot_cpu *cpu, uint32_t slot) { (void)cpu; dispatched = slot; }
"""

MAIN = r"""
int main(int argc, char **argv) {
    uint32_t p, q, b;
    aot_cpu cpu = {{0}};
    const char *test = argc > 1 ? argv[1] : "";
    if (!strcmp(test, "jni-valid")) {
        aot_tailjump(&cpu, AOT_JNI_THUNK_BASE + 4u * 50u, AOT_B, 0);
        return dispatched == 50u ? 0 : 1;
    }
    if (!strncmp(test, "jni-invalid-", 12)) {
        aot_tailjump(&cpu, AOT_JNI_THUNK_BASE + 4u * 50u + (uint32_t)atoi(test + 12), AOT_B, 0);
        return 1;
    }
    if (!strcmp(test, "jni-end")) {
        aot_tailjump(&cpu, AOT_JNI_THUNK_BASE + 4u * AOT_JNI_THUNK_COUNT, AOT_B, 0);
        return 1;
    }
    if (!strcmp(test, "null")) { aot_free(0); return g_lock ? 1 : 0; }
    p = aot_malloc(9);
    b = p - 8u;
    if (!strcmp(test, "reuse")) {
        aot_free(p);
        q = aot_malloc(9);
        if (q != p || g_lock) return 1;
        return aot_malloc(9) == p ? 1 : 0;
    }
    if (!strcmp(test, "double")) { aot_free(p); aot_free(p); return 1; }
    if (!strcmp(test, "class-low")) AOT_ST32(b, HEAP_MAGIC | 3u);
    if (!strcmp(test, "class-high")) AOT_ST32(b, HEAP_MAGIC | 255u);
    if (!strcmp(test, "reserved")) AOT_ST32(b, HEAP_MAGIC | 0x100u | 5u);
    if (!strcmp(test, "size-large")) AOT_ST32(b + 4u, UINT32_MAX);
    if (!strcmp(test, "size-class")) AOT_ST32(b + 4u, 0u);
    if (!strcmp(test, "extent")) AOT_ST32(b, HEAP_MAGIC | 6u);
    if (!strcmp(test, "unaligned")) ++p;
    if (!strcmp(test, "outside")) p = AOT_HEAP_BASE - 8u;
    aot_free(p);
    return 1;
}
"""


class RuntimeGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        requested = os.environ.get("CC")
        compiler = shutil.which(requested) if requested else (shutil.which("clang") or shutil.which("gcc"))
        if not compiler:
            raise unittest.SkipTest("clang or gcc required to compile isolated runtime guard fixture")
        source = (ROOT / "aot/runtime/aot_core.c").read_text(encoding="utf-8")
        heap = source[source.index("#define HEAP_MAGIC"):source.index("/* ---------------------------------------------------------------- dispatch */")]
        start = source.index("void aot_tailjump(")
        tailjump = source[start:source.index("uint32_t aot_symbol_addr(", start)]
        cls.temp = tempfile.TemporaryDirectory(prefix="snailmail-runtime-guards-")
        cls.addClassCleanup(cls.temp.cleanup)
        fixture = Path(cls.temp.name) / "guards.c"
        cls.exe = Path(cls.temp.name) / ("guards.exe" if os.name == "nt" else "guards")
        fixture.write_text(PREAMBLE + heap + tailjump + MAIN, encoding="utf-8")
        result = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(fixture), "-o", str(cls.exe)],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError("runtime guard fixture failed to compile:\n" + result.stdout + result.stderr)

    def check_case(self, case, expected):
        result = subprocess.run([str(self.exe), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, case + ": " + result.stderr)
        if expected == FATAL_EXIT:
            self.assertTrue(result.stderr, "fatal operation must retain diagnostic evidence")

    def test_null_free_and_normal_reuse(self):
        for case in ("null", "reuse", "jni-valid"):
            with self.subTest(case=case):
                self.check_case(case, 0)

    def test_invalid_free_is_fatal(self):
        for case in ("double", "class-low", "class-high", "reserved", "size-large", "size-class", "extent",
                     "unaligned", "outside"):
            with self.subTest(case=case):
                self.check_case(case, FATAL_EXIT)

    def test_invalid_jni_target_is_fatal(self):
        for case in ("jni-invalid-1", "jni-invalid-2", "jni-invalid-3", "jni-end"):
            with self.subTest(case=case):
                self.check_case(case, FATAL_EXIT)


if __name__ == "__main__":
    unittest.main()

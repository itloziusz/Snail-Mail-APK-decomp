/*
 * FP contraction check (validation only). Mirrors tVector::Dot
 * (v7a:0x16aa4-0x16acc): s = ay*by; s = s + ax*bx (VMLA); s = s + az*bz (VMLA).
 * ARMv7 VFPv2 VMLA rounds the product before the add (two roundings); the
 * Unicorn probe (unicorn_runtime_probe.py [1]) confirms the original returns
 * 0.0 for the inputs below. An AArch64 compiler allowed to contract emits
 * FMADD (one rounding) and returns +-2^-46 (+-1.42e-14) instead, whichever
 * product it chooses to fuse.
 * Build once with the project flags (-ffp-contract=off) and once with the
 * compiler default to see the difference.
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>

__attribute__((noinline)) float dot(const float *a, const float *b) {
  float s = a[1] * b[1];
  s = s + a[0] * b[0];
  s = s + a[2] * b[2];
  return s;
}

int main(void) {
  volatile float one = 1.0f;
  float a[3] = {one + 0x1p-23f, one + 0x1p-23f, 0.0f};
  float b[3] = {one - 0x1p-23f, -(one - 0x1p-23f), 0.0f};
  float r = dot(a, b);
  uint32_t bits;
  memcpy(&bits, &r, 4);
  printf("dot = %.17g (0x%08x) -> %s\n", (double)r, bits,
         bits == 0 ? "matches original ARMv7 VMLA (two roundings)" : "DIFFERS from original (fused multiply-add)");
  return bits == 0 ? 0 : 1;
}

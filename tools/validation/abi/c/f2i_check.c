/*
 * float -> integer conversion semantics (validation only).
 * Original v7a code uses VCVT.S32.F32 / VCVT.U32.F32 (round toward zero,
 * saturating, NaN -> 0), e.g. tColourSmall::Set v7a:0x1811c-0x18170 stores
 * (uint8_t)(uint32_t)(x*255.0f); v5 uses libgcc __aeabi_f2iz/__aeabi_f2uiz
 * with the same results (unicorn_runtime_probe.py [2],[3]).
 * In C, converting an out-of-range float to an integer type is undefined
 * behaviour: AArch64 FCVTZS/FCVTZU happen to saturate like ARMv7, x86-64
 * CVTTSS2SI does not. Reconstructed code must use explicit helpers such as
 * sm_f2i32_arm/sm_f2u32_arm below (proposed for reconstructed/core).
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>

static int32_t sm_f2i32_arm(float x) {
  if (isnan(x)) return 0;
  if (x >= 2147483648.0f) return INT32_MAX;
  if (x <= -2147483648.0f) return INT32_MIN;
  return (int32_t)x; /* in range: C truncation == round toward zero */
}
static uint32_t sm_f2u32_arm(float x) {
  if (isnan(x) || x <= 0.0f) return 0;
  if (x >= 4294967296.0f) return UINT32_MAX;
  return (uint32_t)x;
}

int main(void) {
  const float in[] = {0.5f, -0.1f, 1.2f, 2.0f, -1.0f, 1e10f, -1e10f, NAN, INFINITY, -INFINITY, 2147483648.0f, -2.5f};
  int mismatch = 0;
  for (unsigned i = 0; i < sizeof in / sizeof in[0]; i++) {
    volatile float x = in[i];
    volatile float y = x * 255.0f;
    /* raw casts: UB when out of range -- shown only to expose the host */
    uint8_t raw = (uint8_t)(uint32_t)y;
    uint8_t arm = (uint8_t)sm_f2u32_arm(y);
    int32_t rawi = (int32_t)x;
    int32_t armi = sm_f2i32_arm(x);
    printf("x=%-12g colour-byte raw=%3u arm=%3u | (int)x raw=%11d arm=%11d%s\n", (double)in[i], raw, arm, rawi, armi,
           (raw != arm || rawi != armi) ? "   <-- raw C cast differs from ARM semantics on this host" : "");
    mismatch += (raw != arm || rawi != armi);
  }
  /* expected ARM bytes for the unicorn probe inputs */
  printf("helper bytes for (-0.1,1.2,2.0,-1.0): r=%u g=%u b=%u a=%u (original: r=0 g=50 b=254 a=0)\n",
         (uint8_t)sm_f2u32_arm(-0.1f * 255.0f), (uint8_t)sm_f2u32_arm(1.2f * 255.0f),
         (uint8_t)sm_f2u32_arm(2.0f * 255.0f), (uint8_t)sm_f2u32_arm(-1.0f * 255.0f));
  printf("raw-cast mismatches on this host: %d\n", mismatch);
  return 0;
}

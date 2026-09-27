# Rendering: GLES 1.1 fixed-function emulation on GLES 2.0

Status: `RECONSTRUCTED` + `UNIT_TESTED` (host, Mesa llvmpipe). It has not been
compared against the original game's frames: no device capture exists yet.

The original `libsnailmail.so` (v7a, SHA-256
`e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466`) renders
through exactly 45 GLES 1.x entry points (`aot/runtime/sm_gl_api.h`; census in
`analysis/native/gl_usage.json`, summary in `docs/PLATFORM_BOUNDARIES.md` §3).
`reconstructed/rendering` implements those 45 functions on top of GLES 2.0, for
hosts with no GLES 1 driver. On this machine Mesa cannot create an ES1 context
(`EGL_BAD_ALLOC`), while ES2/ES3 on llvmpipe works through EGL surfaceless plus
a pbuffer. On Android the planned reference backend remains the system
`libGLESv1_CM` (`docs/ARCHITECTURE.md` §4). This layer is the fallback, and a
test oracle for any later backend.

## 1. Layout and API

| Target | Sources | Needs | Built when |
|---|---|---|---|
| `sm_rendering_math` | `reconstructed/rendering/src/smgl_math.c` | libm | always (also AArch64 cross) |
| `sm_rendering` | `reconstructed/rendering/src/smgl.c` | `GLES2/gl2.h`, `libGLESv2` | both found by `find_path`/`find_library`, otherwise skipped with a STATUS message |
| `sm_host_egl` | `host/egl_offscreen.c` (+ `host/CMakeLists.txt`) | `EGL/egl.h`, `libEGL`; libpng optional | host builds with EGL found; never on Android. Added from `reconstructed/rendering/CMakeLists.txt`, because the top level only globs `reconstructed/*` and `tests/*` |

`sm_rendering` exposes `aot/runtime` (for `sm_gl_api.h`) and its own
`include/` publicly.

Public header `sm_rendering/smgl.h`:

* `smgl_<Name>` for every `SM_GL_FUNCS` entry. The prototypes are generated
  from the X-macro, so the signatures match `sm_gl_api.h` by construction, and
  `smgl.c` asserts there are exactly 45.
* `const sm_gl_backend *smgl_backend(void)` returns a static table of all 45
  functions.
* `int smgl_init(void)` compiles the shader and resets every emulated state to
  its GLES 1.1 default. It needs a current ES2+ context and returns 0 or -1.
  `void smgl_shutdown(void)` deletes the GL objects and frees the bookkeeping.
* Introspection functions. These are not GL entry points; the game imports no
  `glGet*`:
  * `smgl_take_error()`: glGetError semantics for errors that the emulator
    detects itself.
  * `smgl_get_matrix(mode, out)` and `smgl_get_stack_depth(mode)`.
  * `smgl_get_tracked(pname, out)`: state that the emulator holds and GLES2
    does not.
  * `smgl_diag_count(d)` and `smgl_diag_name(d)`.
* `SMGL_GL_*` constants for GLES 1.1 tokens that `<GLES2/gl2.h>` lacks.

`sm_rendering_math` (`smgl_math.h`) contains the GL-free matrix operations and
the matrix stack.

`host/egl_offscreen.h` provides the following:

* `sm_host_egl_create(w, h, depth_bits)` sets up the display with
  `eglGetPlatformDisplayEXT(EGL_PLATFORM_SURFACELESS_MESA)` and falls back to
  the default display. It picks an exact RGBA8888, single-sampled pbuffer
  config, preferring the exact depth size (16 in the tests), creates a
  `EGL_CONTEXT_CLIENT_VERSION 2` context and makes it current.
* `sm_host_egl_destroy()`.
* `sm_host_read_rgba_topdown()`.
* `sm_host_write_png(path)`: reads RGBA with `glReadPixels`, flips the rows so
  the top row comes first, and writes the PNG with libpng's `png_image` API.

The emulator assumes it owns these parts of the context: the program,
vertex attributes 0 and 1, and texture unit 0. It is single-threaded and
supports one context at a time, like GLES itself.

## 2. Architecture

* **Emulator state:** the three matrix stacks, the current colour, the texenv
  mode, fog parameters, the client-array enables, alpha test, shade model,
  hints, `GL_MULTISAMPLE`, and a per-texture record. The record holds the base
  format, the defined mip levels, the level-0 size, the min filter and
  `GL_GENERATE_MIPMAP`.
* **One GLSL ES 1.00 program** does all fixed-function work:
  * position = P·(MV·v) and texcoord = T·(s,t,r,q), sampled with
    `texture2DProj`, which divides by q;
  * texenv on unit 0;
  * fog, after texturing.
  Uniforms (`u_tex_mode`, `u_fog_mode`, …) switch features on and off. They
  are uploaded lazily from dirty bits at `glDrawElements`. The vertex shader is
  `highp`; the fragment shader is `highp` when `GL_FRAGMENT_PRECISION_HIGH` is
  available, otherwise `mediump`.
* **Pass-through:** buffers, textures, blending, depth, cull, scissor,
  viewport, clears, line width, pixel store and read-back go straight to GLES2.
  Where GLES2 accepts *more* than GLES 1.1, the GLES 1.1 rules are checked
  first (see §4).

## 3. Semantics (subset used by the game, per GLES 1.1)

### Matrices

* Storage is column-major (`m[c*4+r]`). Every operation post-multiplies
  (M = M·X), all in `float` with a fixed summation order (k = 0..3); the build
  uses `-ffp-contract=off`.
* Rotate, frustum and ortho use the spec formulas.
* `glRotatef` takes degrees, converted to radians in float. The axis is
  normalised. A zero axis leaves the matrix unchanged, the same as Mesa; the
  spec leaves this undefined. For axis-aligned axes (the game only uses
  (0,0,1)) the exact {c, s} entries are used, so the diagonal element on the
  axis is exactly 1.
* Scale and translate drop the zero terms of the product, as Mesa does. For
  finite inputs this is identical except possibly for the sign of a zero.
* `glFrustumf`/`glOrthof` with invalid arguments (n≤0, f≤0, l=r, b=t, n=f)
  raise `GL_INVALID_VALUE` and leave the matrix unchanged.
* Stack depths are MODELVIEW 32, PROJECTION 4 and TEXTURE 4. The GLES 1.1
  minima are 16, 2 and 2.
  * Overflow sets `GL_STACK_OVERFLOW` and underflow sets `GL_STACK_UNDERFLOW`.
    In both cases the stack and matrix are unchanged and the event is logged.
  * Growing a stack beyond the GLES 1.1 minimum is logged
    (`stack-beyond-es11-minimum`), because a minimal 2011 device would have
    overflowed there. The larger depth is tolerant; the diagnostic keeps the
    portability risk visible.

### Vertex arrays and draws

* `glVertexPointer`/`glTexCoordPointer` become
  `glVertexAttribPointer(attr, size, type, GL_FALSE, stride, ptr)` at call time.
  * GLES2 latches the bound `GL_ARRAY_BUFFER` at that moment, which is the
    GLES 1.1 rule. With a VBO bound, `ptr` is an offset.
  * `normalized = GL_FALSE` converts BYTE/SHORT like GLES 1.1 does (no
    normalisation) and FIXED as 16.16.
  * Sizes 2–4 are accepted. Types BYTE, SHORT, FIXED and FLOAT are accepted,
    anything else raises INVALID_ENUM. Negative strides raise INVALID_VALUE.
* A disabled `GL_TEXTURE_COORD_ARRAY` means attribute 1 keeps its current
  value (0,0,0,1). That is GLES 1.1's current texcoord, since
  `glMultiTexCoord4f` is not imported.
* `glDrawElements` takes indices as `GL_UNSIGNED_BYTE`/`GL_UNSIGNED_SHORT`
  only; `GL_UNSIGNED_INT` is ES3-only and is rejected. `indices` is an offset
  when an element buffer is bound and a client pointer otherwise, as in GLES2.
* With `GL_VERTEX_ARRAY` disabled nothing is drawn (GL 1.x issues no vertices),
  and this is logged. `gl_PointSize = 1` (`glPointSize` is not imported).
* `GL_COLOR_ARRAY`, `GL_NORMAL_ARRAY` and `GL_POINT_SIZE_ARRAY_OES` are
  tracked. A draw with any of them enabled is logged and the array is ignored
  (no `glColorPointer`/`glNormalPointer` import exists). The game only ever
  *disables* `GL_COLOR_ARRAY`.

### Colour and texturing

* The current colour from `glColor4f` is clamped to [0,1] when used (GLES 1.1
  colour clamping without lighting). A test shows that the clamp happens
  before MODULATE.
* `glShadeModel` is tracked only. Without colour arrays or lighting every
  vertex carries the current colour, so FLAT and SMOOTH give identical
  fragments.
* `GL_TEXTURE_2D` enables sampling of the texture bound to unit 0.
* Texture environment modes. Cf/Af are the current colour and alpha, Ct/At the
  texel colour and alpha. The "colour"/"alpha" flags come from the base format
  recorded at `glTexImage2D` level 0: ALPHA has alpha only; LUMINANCE and RGB
  have colour only; LUMINANCE_ALPHA and RGBA have both.

  | Mode | RGB | A |
  |---|---|---|
  | MODULATE (the game's only mode) | Cf·Ct (Cf if the format has no colour) | Af·At (Af if the format has no alpha; GL_RGB ⇒ Af) |
  | REPLACE | Ct / Cf | At / Af |
  | DECAL | mix(Cf, Ct, At) for RGBA, Ct for RGB | Af |
  | BLEND | Cf·(1−Ct): env colour is fixed at (0,0,0,0) because `glTexEnvfv` is not imported | Af·At / Af |
  | ADD | Cf+Ct | Af·At / Af |

  * `GL_COMBINE` and its pnames are logged; COMBINE is drawn as MODULATE.
  * DECAL on non-RGB(A) formats is undefined in the spec. It is logged and
    drawn as for RGB.
  * The result is clamped before fog.
* The texture matrix is applied per vertex: (s,t,0,1) → T·(s,t,0,1), with
  (s,t,q) interpolated and divided per fragment.
* **Completeness (GLES 1.1 §3.8.10):** texturing enabled with an incomplete
  texture draws *as if texturing were disabled*. GLES2 would sample black
  instead. Complete means:
  * level 0 has been specified with a non-zero size, and
  * the min filter is NEAREST/LINEAR, or every level down to 1×1 has been
    specified.

  This case is logged (`texture-incomplete`). Per-level sizes are not
  re-checked.
* `glDeleteTextures` of the bound texture rebinds 0.
* `glTexImage2D` validation follows GLES 1.1:
  * target must be `TEXTURE_2D`;
  * formats: ALPHA, RGB, RGBA, LUMINANCE, LUMINANCE_ALPHA;
  * `internalformat` must equal `format` (the game always passes matching
    values);
  * types: `UNSIGNED_BYTE` and the three packed 16-bit types with their
    formats;
  * border must be 0.

  Non-power-of-two sizes are logged. GLES 1.1 core would reject them, and a
  device with `OES_texture_npot` would accept them; they are forwarded as with
  that extension.
* `glTexParameteri` accepts the GLES 1.1 pnames and values; `MIRRORED_REPEAT`
  is not in GLES 1.1 core and is rejected. `GL_GENERATE_MIPMAP` has no GLES2
  parameter, so it is emulated with `glGenerateMipmap` after each level-0
  upload, and this is logged.

### Fog (GLES 1.1 §3.10)

* Modes EXP (the default, and the game's: it never sets `GL_FOG_MODE`), EXP2
  and LINEAR:
  * EXP: f = exp(−d·c)
  * EXP2: f = exp(−(d·c)²)
  * LINEAR: f = (e−c)/(e−s)
* f is clamped to [0,1], and **RGB = f·Cr + (1−f)·Cfog; alpha is unchanged**.
  Fog is applied after texturing.
* **c = |z_eye|**, the approximation the spec explicitly permits. z_eye is
  interpolated as a varying (it is affine in eye space, so interpolation is
  exact on the primitive's plane), and f is evaluated per fragment. The spec
  also allows per-vertex f; which one the original devices used is unknown.
  The two agree whenever z_eye is constant across a primitive.
* `w_eye` is assumed to be 1 (affine modelview). The game's `glMultMatrixf`
  matrices are runtime values and have not been checked.
* LINEAR with e = s is undefined; scale 1 is used, as in Mesa swrast.
* The fog colour is clamped to [0,1] when specified. A negative density raises
  INVALID_VALUE. `GL_FOG_COLOR` via `glFogf` raises INVALID_ENUM.

## 4. Pass-through, validation and tracked-only state

| Entry / state | Handling |
|---|---|
| `glBlendFunc` | GLES 1.1 factor sets checked first. GLES2 also allows SRC_COLOR as source, DST_COLOR as destination and the CONSTANT_* factors; those raise INVALID_ENUM here and the factors are unchanged |
| `glBindBuffer`, `glBufferData`, `glGenBuffers` | Targets ARRAY/ELEMENT only (ES3 has more). Usage STATIC_DRAW/DYNAMIC_DRAW only (no STREAM_DRAW in GLES 1.1) |
| `glEnable`/`glDisable` | BLEND, DEPTH_TEST, CULL_FACE, SCISSOR_TEST, DITHER, STENCIL_TEST, POLYGON_OFFSET_FILL, SAMPLE_* are forwarded. TEXTURE_2D, FOG and ALPHA_TEST are emulated. `GL_MULTISAMPLE` (0x809D, no GLES2 enable) is tracked only: multisampling follows the EGL config, which is single-sampled here, like a default 2011 `GLSurfaceView`. Lighting, lights, clip planes, COLOR_MATERIAL, NORMALIZE, RESCALE_NORMAL, point/line smooth, logic op, SAMPLE_ALPHA_TO_ONE and POINT_SPRITE are tracked, and enabling one is logged as not emulated. Unknown caps raise INVALID_ENUM |
| `GL_ALPHA_TEST` | Enabling it is logged at draw time. With no `glAlphaFunc` import the function stays `GL_ALWAYS`, so it has no effect, and that is exact rather than an approximation |
| `glHint` | PERSPECTIVE_CORRECTION, POINT_SMOOTH, LINE_SMOOTH and FOG are tracked only (no GLES2 equivalent). GENERATE_MIPMAP is forwarded |
| `glTexEnvf` | See §3. The float param is truncated to an enum (8448.0 → `GL_MODULATE`) |
| `glPixelStorei` | PACK/UNPACK_ALIGNMENT ∈ {1,2,4,8}; the pack alignment is tracked |
| `glReadPixels` | RGBA/UNSIGNED_BYTE is forwarded. RGB/UNSIGNED_BYTE (the game's unused `G0ReadFrameBuffer`) is legal in GLES only when it is the implementation's read format, so it is served from an RGBA read and repacked with the current pack alignment, and this is logged |
| `glClear*`, `glCullFace`, `glDepthFunc`, `glDepthMask`, `glDepthRangef`, `glFinish`, `glGenTextures`, `glLineWidth`, `glScissor`, `glViewport` | Forwarded unchanged: same semantics and valid values in both APIs. `glDepthRangef(-0.004, 0.996)` clamps to (0, 0.996) in both |

**Errors and diagnostics.** A GLES 1.1 validation failure behaves as GLES
specifies: the command is ignored, the first error is kept until
`smgl_take_error()` reads it, and GLES2 is not called. Every diagnostic
category is written to stderr once per entry point (prefix `smgl: [category]
glName:`) and counted on every occurrence.

Unsupported state is never accepted silently. It is always either emulated
exactly, or tracked and logged. Calls made before `smgl_init` are logged and
ignored.

With `SMGL_DEBUG=1` in the environment, `glGetError()` is checked after every
GLES2 call the emulator makes, and failures are logged (`backend-gl-error`).

## 5. Deviations from a GLES 1.1 device (known, not hidden)

1. **Perspective-correction hint.** Texcoords are always interpolated
   perspective-correctly. A 2011 GLES1 driver given `GL_FASTEST` (the game's
   choice) was allowed to interpolate affinely.
2. **Fog is per fragment with |z_eye|.** The device might have used per-vertex
   fog or the true radial distance (see §3).
3. **DITHER** is forwarded, but llvmpipe does not dither 8-bit targets. Device
   output may differ by ±1 LSB.
4. **Line and point rasterisation** follow the GLES2 driver. Smooth lines are
   not emulated.
5. **NPOT textures** are accepted, where GLES 1.1 core would reject them; this
   is logged.
6. **Texture completeness** checks only whether each level is present, not the
   sizes or formats of each level.
7. **Matrix stacks** are deeper than the GLES 1.1 minimum (see §3); this is
   logged.
8. **Unsupported features** are drawn as documented above, and every one is
   logged: COMBINE, BLEND with a non-zero env colour, colour/normal arrays.
9. **Floating point** differs by design. Matrices are float on the CPU, the
   same as GLES 1.1 drivers, but the shader's MV·v and P·eye are evaluated by
   the GPU and may fuse multiply-adds. None of this has been compared with the
   2011 driver.

## 6. Tests

| CTest | What it asserts |
|---|---|
| `rendering.matrix_math` | Matrix math against hand-computed matrices, GL-free, 60 checks: identity/layout, A·B vs B·A, a general 4×4 product, glMultMatrixf post-multiplication, T·S vs S·T, exact scaling of int16 by 1/128, Rx/Ry/Rz(90) and Rz(60), R(120°, (1,1,1)) as an axis permutation (also with an unnormalised axis), zero axis, an exact frustum/ortho including the game's `glOrthof(0,W,H,0,-1,1)`, INVALID_VALUE cases, and stack push/pop/overflow/underflow. Runs in the AArch64 cross build under qemu |
| `rendering.backend` | The table has 45 entries and each equals `smgl_<Name>`. Calls before init are ignored and logged. Needs no context |
| `rendering.clear` | (a) Clear colour. RGB read-back conversion with pack alignment 4 (padding untouched) and 1 |
| `rendering.untextured` | (b) Client-array triangle with 20-byte float stride and UBYTE indices; exact coverage with the diagonal skipped. Colour clamping. No draw without `GL_VERTEX_ARRAY` |
| `rendering.ortho` | (c) `glOrthof(0,64,64,0,-1,1)` + translate/scale (z scale 0, as the game uses) gives exactly the predicted pixel rectangle. The same holds with `glRotatef(90,0,0,1)` |
| `rendering.vbo_short` | (d) VBO with 3×`GL_SHORT` stride 10 + element buffer at offset 4, the game's degenerate-joined strip `{i,i,i+1,i+3,i+2,i+2}`, `glScalef(1/128)`. The ARRAY_BUFFER is unbound before the draw, which tests the latch rule. The gap between the two quads stays clear |
| `rendering.texture` | (e) 2×2 RGBA, NEAREST/REPEAT, MODULATE (`8448.0f`), texture-matrix translate of 0.5: every pixel matches the predicted texel × colour. RGB texture: MODULATE keeps Af, REPLACE gives Ct,Af. The colour is clamped before MODULATE. An incomplete texture draws untextured; the same texture becomes complete after setting the LINEAR min filter. Deleting the bound texture rebinds 0 |
| `rendering.fog` | (f) EXP with the game's density bits `0x3ca3d70a` at z_eye=−25 ⇒ f=e^−0.5 ⇒ (155,0,100,204). EXP at d=0.05, z=−10. EXP2 ⇒ e^−0.25. LINEAR 0..40 at z=−10 ⇒ 0.75, applied after MODULATE with an RGB texture. Fog off. Invalid fog parameters rejected |
| `rendering.pushpop` | (g) Push/translate/scale/draw, pop/draw: exact pixel rectangles and a bit-identical restored matrix. PROJECTION overflow and underflow (error, depth and matrix unchanged, logged), the beyond-minimum diagnostic, TEXTURE stack restore |
| `rendering.blend` | (h) (ONE,ONE) with saturation. (SRC_ALPHA, ONE_MINUS_SRC_ALPHA) including destination alpha. A GLES1-invalid factor is rejected and the previous factors stay |
| `rendering.depth` | (i) LEQUAL with equal and greater depth. The toon range (−0.004, 0.996) is clamped: z=0.006 fails (it would pass unclamped) and z=0.002 passes. `glDepthMask(GL_FALSE)` does not write |
| `rendering.state` | 18 GLES 1.1 validation errors, first-error-sticks. Tracked-only state (MULTISAMPLE, hints, shade model) causes no GLES2 call or error. Colour/normal arrays and ALPHA_TEST are diagnosed while drawing is unaffected. The LIGHTING, COMBINE, NULL-matrix and NPOT diagnostics |
| `rendering.png` | `sm_host_write_png` writes top-down; the file is read back with libpng and compared byte-for-byte with `sm_host_read_rgba_topdown` |

Each GL scene creates its own 64×64 pbuffer with a 16-bit depth buffer.
Scenes exit 77 (SKIP) when no EGL/GLES2 context can be created, and `png`
also skips without libpng. Every scene ends by checking that
`glGetError() == GL_NO_ERROR`, i.e. that nothing invalid was forwarded.

**Tolerances**, per 8-bit channel:

* **±1 (quantisation):** GLES specifies round-to-nearest conversion of
  c·255, so ties and one extra rounding in texel·colour or blending can move
  one step.
* **±2 (fog):** in addition, GLSL ES only bounds `exp()` to a few ulp, and
  the fog blend adds one more rounding.
* **Coverage:** tests put every primitive edge on integer window coordinates,
  halfway between pixel centres, so no sample lies on an edge. The one
  exception is the triangle's diagonal, which is skipped explicitly.
* **Matrix math:** exact wherever all intermediates are exact binary32. Trig
  results use 1e-6.

**Test sensitivity.** Deliberately breaking the emulator was detected in each
case:

* reversed fog mix, by `fog`;
* no texture matrix, by `texture`;
* normalised shorts, by `vbo_short`;
* pre- instead of post-multiplication, by `ortho`;
* no colour clamp, by `texture`;
* RGB alpha taken from the texel, by `texture`.

**Commands and results** (2026-09-27):

```sh
cmake -S . -B <dir> -DSM_SANITIZE=ON && cmake --build <dir> -j && ctest --test-dir <dir> --output-on-failure
cmake -S . -B <dir> -DCMAKE_TOOLCHAIN_FILE=cmake/aarch64-linux-gnu.toolchain.cmake && cmake --build <dir> -j && ctest --test-dir <dir>
```

* Host GCC 13, ASan+UBSan: 22/22 pass, 13 of them rendering, including
  `ctest -j8`. There were no compiler warnings, no LeakSanitizer reports with
  Mesa 25.2.8 llvmpipe, and so no `ASAN_OPTIONS` override. With
  `SMGL_DEBUG=1` there were no backend errors.
* Host without sanitizers, and clang 18: 22/22 pass and no warnings in
  rendering code. clang's ASan runtime is not installed here.
* AArch64 cross: `sm_rendering` is skipped with a STATUS message (no arm64
  GLES2/EGL), and `rendering.matrix_math` passes under `qemu-aarch64`.

## 7. Open issues

* **No reference frames.** Every expectation comes from the GLES 1.1 spec, not
  from the original app. A device capture (`docs/TESTING.md`) is needed before
  claiming visual equivalence, especially for fog, perspective hints and depth
  precision.
* **Android build.** `android/app/src/main/cpp/CMakeLists.txt` adds every
  `reconstructed/*` module, so with the NDK's `libGLESv2` found, `sm_rendering`
  (and a `GLESv2` link) would join `libsnailmail.so` next to `GLESv1_CM`. This
  is harmless, since the objects are only pulled in if referenced, but it is
  a decision for the lead. This was not tested (no NDK here).
* The game's `glMultMatrixf` inputs (`tMatrix`) have not been checked for
  being affine, which the fog |z_eye| assumption relies on.
* The depth-buffer size of the original default EGL config is still open
  (`docs/PLATFORM_BOUNDARIES.md` Q3). Tests use 16 bits.

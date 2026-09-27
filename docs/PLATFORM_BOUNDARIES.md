# Platform boundaries of `libsnailmail.so`

Scope: everything the original native library needs from outside itself —
imported C/C++/GL symbols, the JNI surface, the Java callbacks it drives, and
the behaviour of the Java shell around it — so that the arm64-v8a port can
re-provide each boundary without an ARM32 interpreter. Game logic is out of
scope here; where a boundary function immediately hands off to game code, the
hand-off address is given and the game side is left to the native-analysis
work area.

| Short name | SHA-256 | Notes |
|---|---|---|
| `v7a` (primary) | `e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466` | ARMv7-A, VFPv2, softfp |
| `v5` | `96dbeaeb20c60d687301ca769656727467371489db5e3ed744a93248bc8d8136` | ARMv5TE, soft-float |

All addresses are `v7a` unless prefixed `v5:`. Evidence IDs refer to
`analysis/evidence/platform.jsonl`. Confidence words follow
`docs/CONVENTIONS.md`; anything not marked is **established** (read directly
from bytes, smali or a tool run recorded below).

Machine-readable companions (regenerate with the tools named):

| File | Generator | Content |
|---|---|---|
| `analysis/native/platform_imports.json` | `tools/validation/platform/import_census.py` | every import, every call site (v7a + v5), recovered r0–r3 arguments, GOT data references, text relocations |
| `analysis/native/gl_usage.json` | `tools/validation/platform/gl_census.py` | every GL call site with recovered constant arguments and a state summary |
| (stdout) | `tools/validation/platform/callsite_args.py NAME...` | recovered arguments at every call of any internal function |
| (stdout) | `tools/validation/abi/abi_audit.py` | instruction/ABI census (see `docs/ABI_PORTING.md`) |

Method: capstone 5.0.9 in ARM mode over `.text` ranges that the ELF mapping
symbols mark `$a` (literal pools `$d` are never decoded); PLT stubs are mapped
to imports through `.rel.plt` and every stub is decoded and its GOT slot
checked against the relocation; arguments are recovered by a per-function
forward constant propagation with block merges (`tools/validation/platform/armelf.py`).
Values that are not provably constant are reported as `unresolved`,
`global_load` or `caller_arg`, never guessed.

---

## 1. Import census (EV-PLAT-0001…0005, 0030)

* `DT_NEEDED`: `libc.so libstdc++.so libm.so libGLESv1_CM.so libdl.so liblog.so`.
  No symbol is imported from `libdl`/`liblog` (the needed entries are unused).
* 97 undefined dynamic symbols (98 entries including the null symbol), the
  **same set in v7a and v5**; 94 `R_ARM_JUMP_SLOT`, 2 `R_ARM_GLOB_DAT`
  (`__sF`, `__stack_chk_guard`), 2 `R_ARM_ABS32` inside `.text` literal pools
  (`__cxa_call_unexpected` at 0x7f540, `__gnu_Unwind_Find_exidx` at 0x7f678).
* 614 direct call sites in each build; every import is referenced; **no import
  is ever used as a function pointer** (no relocated word and no PC-relative
  address formation equals a PLT stub; v5 has two literal words numerically
  equal to stub addresses at v5:0x809bc/0x80114 without relocations — plain
  constants, reported separately in the JSON).
* Both builds carry `DT_TEXTREL`: 12 relocations patch `.text` literal pools of
  the statically linked libgcc EHABI unwinder (`get_eit_entry`,
  `_Unwind_VRS_Pop`, `__gnu_unwind_pr_common`). Android 6.0+ refuses
  `DT_TEXTREL` for targetSdk ≥ 23 — the original could not be loaded by a
  modern target anyway; the port's checker rule E4 forbids it.

| Category | Imports | v7a sites | v5 sites | Imports (v7a/v5 call sites) |
|---|---|---|---|---|
| GLES 1.x | 45 | 283 | 283 | see §3 |
| stdio | 14 | 125 | 125 | sprintf 50, fwrite 18, fseek 12, fread 9, fprintf 7, fopen 6, fclose 6, ftell 6, printf 3, vsprintf 3, fputc 2, putchar 2, fdopen 1, `__sF` data |
| string | 8 | 121 | 119 | atoi 22, strcpy 22, memcpy 21/19, strcat 20, strlen 20, strtod 12, memmove 2, memset 2 |
| stack protector | 2 | 39 | 39 | `__stack_chk_fail` 39, `__stack_chk_guard` (55/53 GOT loads) |
| stdlib | 7 | 21 | 21 | malloc 5, free 4, abort 4 (libgcc unwinder only), lrand48 3, srand48 2, qsort 2, exit 1 |
| libm | 10 | 10 | 12 | one call each; v5 calls `sqrt` 3× (v7a inlines `vsqrt.f64`, libm only on NaN) |
| posix | 4 | 8 | 8 | chdir 3, getcwd 3, dup 1, gettimeofday 1 |
| C++ runtime | 7 | 7 | 7 | `_Znwj` 1 (AppInit, `new cRGame`, size 0x3a6468), guard acquire/release 1 each (static local in `tVector::Cross`), `__cxa_type_match`/`__cxa_begin_cleanup`/`__cxa_call_unexpected`/`__gnu_Unwind_Find_exidx` referenced only by the libgcc personality/unwinder |

**Reachability (likely dead code, EV-PLAT-0030).** All `fopen`, `chdir`,
`getcwd`, `exit`, `printf`, `putchar`, `fprintf`, `fputc` and the `__sF` use
sit in functions that have *no* direct caller, *no* relocated pointer and *no*
PC-relative address formation anywhere in the binary: `RShellGetFileSize(char*)`
0x1bbc0, `RShellDatInit` 0x1c018 → `RShellLoadFileHeader`, `RShellChangeDir`
0x1c138, `RShellPushDir`/`PopDir`, `RShellStop` 0x1b6ac (`exit(-1)`),
`gRegisterInit` 0x217ec, `gRegisterFindFile`, `gRegisterGetFileSize(char*)`,
`gRegisterMakeKeyEmail`, `cRSubTracks::Export` 0x74a98, `cRDirectX::Export`
(fopen "w", dev export of `.x`/`Segments/%s.txt` files). Function pointers in
this binary exist only in 42 vtables, 11 `.init_array` entries and 3 GOT
entries (EV-PLAT-0042), none of which reach these functions. Status: *likely*
unreachable in the shipped game — keep them out of the port's platform layer
unless a caller is found.

---

## 2. JNI surface and the Java callback table (EV-PLAT-0016, 0017, 0050)

The authoritative JNI map is owned by the JNI work area (`docs/JNI_MAP.json`);
this section only records what the platform layer must honour.

**Exports (18, identical names in both builds):** see the header of
`android/app/src/main/cpp/jni_bridge.cpp` for the full list with addresses.
Never invoked by the original Java code (smali: declaration only):
`ADGLSurfaceView.nativePause` (0x15500), `ADRenderer.nativeDone` (0x1555c).
`SnailMailApplication.JNIOFInit` is declared in Java but has **no** native
symbol in either build and is never called.

**Callbacks.** `JAVA_RegisterFunctions` (0x13ca4, called from `nativeInit`
0x15658 and `nativeReInit` 0x155f8) stores `env` in `gJavaEnv` (0x91038) and
the `ADRenderer` jobject in `gJavaObj` (0x9103c) **without `NewGlobalRef`**,
calls `GetObjectClass` (stored, also as a local reference, in `gJavaClass`
0x91034) and resolves 26 methods with `GetMethodID` from the table
`gJAVAFunction` (0x8b3f0, 26 × {jmethodID, name*, sig*}):

| # | Method | Descriptor | Native wrapper (v7a) |
|---|---|---|---|
| 0 | JAVALoadSample | `(Ljava/lang/String;)I` | `JAVALoadSample(char*)` 0x1495c ← `PfmAudioLoadSample` |
| 1 | JAVAPlaySample | `(IF)I` | 0x14230 ← `PfmAudioPlaySample` 0x14290 |
| 2 | JAVAStopSample | `(I)V` | `PfmAudioStopSample` 0x14044 |
| 3 | JAVASaveFile | `(Ljava/lang/String;[BI)I` | `JAVACSaveFile` 0x14858 ← `PfmSaveFile` |
| 4 | JAVADeleteFile | `(Ljava/lang/String;)V` | `JAVACDeleteFile` 0x14eac |
| 5 | JAVALoadFile | `(Ljava/lang/String;[BI)V` | `JAVACLoadFile` 0x15038 ← `PfmLoadFile` 0x151a0 |
| 6 | JAVAFindFile | `(Ljava/lang/String;)I` | `JAVACFindFile` 0x147c8 ← `PfmFindFile` |
| 7 | JAVAFileSize | `(Ljava/lang/String;)I` | `JAVACFileSize` 0x14fb0 |
| 8 | JAVASetMusicVolume | `(F)V` | `PfmAudioSetMusicVolume` 0x14088 |
| 9 | JAVAPlayMusic | `(Ljava/lang/String;)V` | `PfmAudioPlayMusic` 0x14f30 |
| 10–13 | JAVAStopMusic / UnPauseMusic / PauseMusic / MusicRestart | `()V` | 0x14004 / 0x13fc4 / 0x13f84 / 0x13f44 |
| 14–16 | JAVAUnZip / UnJpg / UnPng | `([B[B)V` | `JAVAC_UnZip` 0x14b60, `JAVAC_UnJpg` 0x149e8, `JAVAC_UnPng` 0x144c0 (from `PfmLoadFileDat` 0x14c74) |
| 17–22 | JAVAOpenFeint{Open, LastLoggedInUserID, Submit, Unlock, IsUserLoggedIn, IsOnline} | see shell | 0x13f04, 0x146c8, 0x1442c, 0x14638, 0x141e0, 0x14190 |
| 23, 24 | JAVATime, JAVATimeHi | `()I` | both called by `JAVATime()` 0x14118 |
| 25 | JAVAVibrate | `(I)V` | 0x13eb8 ← `PfmVibrate` (300 ms) |

Consequences for the port (platform rules, not game logic):

* The cached local reference `gJavaObj` is used from later native frames
  (every callback above). That was tolerated by pre-ICS Dalvik (direct
  pointers) but is invalid JNI; the port must hold a global reference, and use
  a `JNIEnv*` only on the thread it belongs to (all callbacks are issued from
  the GL thread, `nativeRender`).
* Float arguments to `Call{Int,Void}Method` are promoted to double
  (`vcvt.f64.f32` + `vstr d7,[sp]` at 0x1423c/0x14270 and 0x14094/0x140c4),
  as C varargs require (EV-PLAT-0046). Reconstructed code gets this for free
  from the compiler; hand-written `jvalue` arrays must use `.f`.
* `JNIDatInit` (0x15244) reads the private `java.io.FileDescriptor.descriptor`
  field (`FindClass` → `NewGlobalRef` → `GetFieldID` → `GetIntField`), then
  `dup`, `fdopen(fd,"rb")` (0x15324) and seeks inside the APK to the
  `asm.mp3` range; at 0x1543c it calls `DeleteLocalRef` on the *global*
  reference returned by `NewGlobalRef` (EV-PLAT-0019). Accessing a non-SDK
  field is restricted on modern Android (*likely* greylisted, needs device
  test); the port should obtain the descriptor via a public API
  (`ParcelFileDescriptor`/`AAsset`), which is a Java-shell + JNI change to be
  agreed with the JNI owner.
* `CBOFOStatePtr` in `JAVAOpenFeintSubmit/Unlock` is a native pointer carried
  as a Java `int`; the callbacks write through it (`strbne r3,[r5]` at 0x7d42c
  and 0x7d474). See `docs/ABI_PORTING.md` §1.

---

## 3. Graphics — OpenGL ES 1.x (EV-PLAT-0008…0015)

API evidence: manifest `<uses-feature android:glEsVersion="0x00010001"/>`,
`DT_NEEDED libGLESv1_CM.so`, 45 imported `gl*` functions (list in
`gl_usage.json.api_evidence`). The Java view never calls
`setEGLContextClientVersion`, so `GLSurfaceView` creates its default
(ES 1.x) context with its default EGL config chooser. Nothing in the binary
indicates ES 2.0 or any extension use (no `glGetString`, no extension entry
points). 283 call sites; 440 arguments resolved to constants, 185 are
runtime values (mostly sizes, pointers and computed floats).

### 3.1 Fixed-function state actually set

| State | Values seen (sites) | Where |
|---|---|---|
| `glEnable` | `GL_TEXTURE_2D` (9), `GL_DEPTH_TEST` (4), `GL_BLEND`, `GL_CULL_FACE`, `GL_FOG`, `GL_SCISSOR_TEST`, `GL_DITHER`, `GL_MULTISAMPLE` (1 each) | `InitGL` 0x7ab84 enables DITHER, MULTISAMPLE, DEPTH_TEST |
| `glDisable` | `GL_DEPTH_TEST` (3), `GL_BLEND` (2), `GL_CULL_FACE` (2), `GL_FOG`, `GL_ALPHA_TEST` (1 each) | ALPHA_TEST only disabled, in `InitGL` 0x7acac |
| client state | enable: `GL_VERTEX_ARRAY` (7), `GL_TEXTURE_COORD_ARRAY` (7); disable: same (5/5) + `GL_COLOR_ARRAY` (1, `cRSplashManager::Render` 0x79e4c) | |
| `glMatrixMode` | `GL_MODELVIEW` (9), `GL_TEXTURE` (6), `GL_PROJECTION` (5) | texture matrix used for UV scaling |
| `glBlendFunc` (all in `G0SetBlend(int)` 0x7b6f0) | mode 0: blend off, depth write on; 1,2,5,7: (`SRC_ALPHA`,`ONE_MINUS_SRC_ALPHA`); 3: (`ONE`,`ONE`); 4,6: (`SRC_ALPHA`,`SRC_COLOR`); 8: (`ZERO`,`ONE_MINUS_SRC_ALPHA`); 9: (`ONE_MINUS_DST_COLOR`,`ONE_MINUS_SRC_ALPHA`); every blending mode also does `glEnable(GL_BLEND)`, `glDepthMask(GL_FALSE)`; >9 logs and keeps the previous factors | jump table 0x7b718 (`addls pc,pc,r0,lsl#2`) |
| `glTexEnvf` | (`GL_TEXTURE_ENV`, `GL_TEXTURE_ENV_MODE`, `GL_MODULATE`) — param passed as float 8448.0 (0x46040000) | `G0RenderCamera` 0x7c5c4 |
| fog | `glEnable(GL_FOG)`, `glFogfv(GL_FOG_COLOR, (*ptr)+20)`, `glFogf(GL_FOG_DENSITY, 0.02f)`, `glHint(GL_FOG_HINT, GL_DONT_CARE)`, clear colour = the same fog colour fields; **no `GL_FOG_MODE` call** ⇒ ES 1.x default `GL_EXP` applies; START/END never set | `G0RenderCamera` 0x7c5ec–0x7c634, gated on a bool argument and a flag byte |
| depth | `glDepthFunc(GL_LEQUAL)` (3), `glDepthMask` TRUE (5)/FALSE (3), `glClearDepthf(1.0)` (2), `glDepthRangef` (0,1) ×2, (−0.004, 0.996) and (−1, 1) — ES clamps both ends to [0,1] | toon outline pass `G0RenderToon` 0x7c2c0 |
| cull | `glCullFace(GL_FRONT)` after `glEnable(GL_CULL_FACE)` | `G0SetCull(bool)` 0x7c668 |
| shading/hints | `glShadeModel(GL_FLAT)` (3); `GL_PERSPECTIVE_CORRECTION_HINT`/`GL_LINE_SMOOTH_HINT` = `GL_FASTEST` | `InitGL` |
| pixel store | `glPixelStorei(GL_UNPACK_ALIGNMENT, 1)` (2) | texture set (re)load |
| textures | `glTexImage2D(GL_TEXTURE_2D, …)` internal = format ∈ {`GL_RGB`, `GL_RGBA`} (one site runtime), type `GL_UNSIGNED_BYTE` always; `glTexParameteri` MIN/MAG = `GL_LINEAR`, WRAP_S/T = `GL_REPEAT` (2+2) or `GL_CLAMP_TO_EDGE` (1+1); no mipmaps, no `GL_GENERATE_MIPMAP` | `G0TextureLoad` 0x7b24c, `cRSplashManager` |
| buffers | `glBindBuffer` ARRAY (12) / ELEMENT_ARRAY (15); `glBufferData` usage always `GL_STATIC_DRAW` (9); no `glDeleteBuffers`/`glBufferSubData` import | `InitGL` builds a shared 3072-byte u16 index buffer of degenerate-joined quads `{i,i,i+1,i+3,i+2,i+2}` for 1024 vertices (0x7abb4–0x7ac30) |
| draw | `glDrawElements` modes `GL_TRIANGLE_STRIP` (6), `GL_TRIANGLES` (3), `GL_LINES` (1); index type always `GL_UNSIGNED_SHORT` | |
| vertex formats | `glVertexPointer` 3×`GL_FLOAT` stride 20 (8) / 3×`GL_SHORT` stride 10 (4 + 2 runtime); `glTexCoordPointer` 2×`GL_FLOAT` stride 20 / 2×`GL_SHORT` stride 10; `glScalef(1/128)` ×2 in `G0RenderObject` for the short format | interleaved xyz+uv |
| matrices | `glOrthof(0, W, H, 0, −1, 1)` shape (right/bottom runtime), `glFrustumf` only in the game's own 5-float `gluPerspective` 0x7b0c8 (called from `G0RenderCamera` 0x7c4a0 with constant 3rd/4th arguments 0.3f and 52.0f), `glRotatef(angle, 0,0,1)`, `glMultMatrixf` (runtime matrices) | |
| viewport/scissor | `glViewport` computed from device size and camera rect; `glScissor(0,0,W,H)` + `glEnable(GL_SCISSOR_TEST)` in `G0RenderCamera` | |
| other | `glLineWidth(1.0)`; `glClear(COLOR|DEPTH)` (3); `glClearColor` (0,0,0,0)/(0,0,0,1)/fog colour; `glColor4f` constant (1,1,1,1) in `InitGL` and bytes/255 in `G0SetColour` 0x7acfc | |
| `glReadPixels` | (`GL_RGB`,`GL_UNSIGNED_BYTE`) in `G0ReadFrameBuffer` 0x7bf24 — no caller, no pointer: *likely* unused screenshot helper | |
| `glFinish` | only in `G0Finish` | |

### 3.2 What the missing imports imply — and what they do not

Established: the library cannot set colour or normal arrays
(`glColorPointer`, `glNormalPointer` absent), cannot configure lighting or
materials (`glLight*`, `glMaterial*` absent, `GL_LIGHTING` never enabled),
cannot configure the alpha test (`glAlphaFunc` absent; `GL_ALPHA_TEST` only
ever disabled), uses one texture unit (`glActiveTexture` absent), never
changes `GL_TEXTURE_ENV_MODE` from `GL_MODULATE` at runtime (single call) and
never uses mipmaps or compressed textures. Per-vertex colour is therefore
constant per draw (`glColor4f`), and the `glDisableClientState(GL_COLOR_ARRAY)`
is a defensive no-op.

Not implied: that the game looks unlit/flat (shading may be baked into
textures or computed on the CPU — e.g. `G0RenderToon` draws outlines with
`GL_LINES` and an offset depth range); that GL state is only set through the
listed wrappers (state persists across frames, so the *order* of wrapper
calls matters and must come from the game code); that the default EGL config
of 2011 (colour depth, 16-bit depth buffer) equals today's default (open
question Q3); that ES 1.x is unavailable on target devices (Android still
ships `libGLESv1_CM`; a port may keep ES 1.1 as the first step and only later
move to ES 2/3 with an emulation of exactly the state in §3.1).

---

## 4. Audio (EV-PLAT-0020, 0021)

All audio goes through Java: `SoundPool(8 streams, STREAM_MUSIC, 0)` for
effects and one looping `MediaPlayer` for music, both opened from
`<name>.ogg` APK assets with `AssetManager.openFd` (assets must stay stored).

| Native entry (v7a) | Behaviour |
|---|---|
| `RShellSoundRegister(char*,int)` 0x1a490 → `PfmAudioLoadSample` → `JAVALoadSample` | name → SoundPool id (−1 on failure) |
| `RShellSoundPlay` 0x1a534 / `RShellVoicePlay(int,…)` 0x1a4c0 | id −1 → return 0; volume × `PfmNormalizeSfx` / `PfmNormalizeVoice` (0x8b3e8/0x8b3e0, initial 1.0) → `PfmAudioPlaySample` 0x14290: × `PfmSampleVolume` (0x8b3ec, 1.0) → `JAVAPlaySample(id, vol)`; **pan/pitch arguments are dropped** (Java plays left=right=vol, priority 1, no loop, rate 1.0) |
| `RShellSoundStopSample` / `StopLooped` 0x1a5a8/0x1a5ac | both `b PfmAudioStopSample` → `SoundPool.stop(streamId)` |
| `RShellMusicPlay(char*,int,char*)` 0x1a688 | ignores a request equal to the current name (`Rstrcmp`); else stop, copy name, `JAVAPlayMusic`; names seen: `"mainmenu"` (6 callers), `"introtext"` (`cRSplash::Init`), others runtime (`music1`–`music4` strings at 0x83f00–0x83f18) |
| `RShellMusicVolume` → `JAVASetMusicVolume`; Pause/UnPause/Stop wrappers | flags gate UnPause (0x1a5e8) |
| `JNIAudioInit` 0x13ad0 | empty (`bx lr`) |

Sample names: 58 of the 170 `.ogg` basenames exist as C strings in v7a
`.rodata`; 52 of them are referenced from the `gSFXBank` table
(`.data` 0x8bacc, 12-byte records starting with a `char*`); `mainmenu`,
`introtext`, `music1-4` are music. The other 112 (voice lines such as
`imgoingpostal`) are not in the binary — *hypothesis*: they come from data
loaded by `cRVoiceManager::Init` 0x772e0 (it builds names with `strcat`).

---

## 5. Input (EV-PLAT-0022…0025)

* **Touch** — `JNIMouseEvent(action, x, y)` 0x14318 (static native; x in r3,
  y on the stack): returns unless `gGameValid` (0x14e9a8) is non-zero; maps to
  a virtual 640×480 space: `x·640/gG0DeviceScreenWidth`,
  `y·480/gG0DeviceScreenHeight` (floats; `vmul` then `vdiv`); Java action
  0 (down) → `cRMouse::ClickiPhone(true,x,y)` + `ClickOn()`; 1 (move, Java
  `ACTION_MOVE`) → `ClickiPhone` only when game state word at `Game+0x718fc`
  is 2, then `ClickOn()`; 2 (Java `ACTION_UP`) → `ClickOff()` (no coordinate
  update). Mouse object = `*Game + 552`. Multi-touch actions are dropped in
  Java.
* **Keys** — `JNIKey(keycode)` 0x13d78: A–Z (29–54) → `cKeyPad::ConvertCode(
  (keycode+68)&0xff)` = lower-case ASCII; 0–9 (7–16) → `ConvertCode(keycode+41)`
  = ASCII digit; SPACE 62 → 57, ENTER 66 → 28, DEL 67 → 14 (DirectInput scan
  codes `DIK_SPACE`/`DIK_RETURN`/`DIK_BACK`); delivered to `KeySet(uint8)`
  0x20c54 only when `*(Game)+0xbf0 == 2`. BACK is swallowed in Java.
* **Accelerometer** — Java normalises, low-pass filters (α = 0.3) and sends
  (−x, −y, z) at `SENSOR_DELAY_FASTEST`; native 0x142bc forwards to
  `cAccelerometer::Input(Game+3028, x, y, z)` when `Game` is non-null.
* **Screen size** — `nativeResize(w,h)` 0x1556c stores `(float)w` into
  `gG0DeviceScreenWidth` and `gG0ScreenWidth`, `(float)h` into
  `gG0DeviceScreenHeight` and `gG0ScreenHeight`.
* Threading: touch/key/sensor natives run on the UI (or sensor) thread and
  write game globals that the GL thread reads, with no locking — a port must
  either keep that (same memory model caveats) or queue events to the GL
  thread; either choice is a behaviour decision to record.

---

## 6. Timing (EV-PLAT-0026…0028, 0045)

* The game clock is Java `System.nanoTime()`: `JAVATime()` 0x14118 calls
  `JAVATime` and `JAVATimeHi` and returns `((u64)hi<<32 | lo) / 1000`
  (`__aeabi_uldivmod`, unsigned) — microseconds. `GetTime()` 0x7d110 is a
  tail call to it; callers: `appRender` 0x156c0, `cRLoadingBar::ScriptAI`
  (3), `cRResourceManager::AI` (2).
* `appRender` 0x15690 (from `nativeRender` when `AppInit` succeeds) runs a
  fixed-step loop against `gTimeLast` (u64 at 0x91058): step 16666 µs
  (`movw #0x411a`), one `FontAI()+cRGame::AI()` tick normally, up to four when
  behind, and resynchronises `gTimeLast = now` when more than about four steps
  behind; then `G0Render()`. *Likely* reading of the branch structure
  (0x156d0–0x1581c); the native-analysis area owns the exact semantics.
* `gettimeofday` has one call site, `_getTime()` 0x154c0 (ms =
  `tv_sec*1000 + tv_usec/1000` in 32-bit arithmetic, wraps), used only by
  `nativePause`, which Java never invokes. So wall-clock time does not reach
  game logic in the shipped app.

---

## 7. Files and persistence (EV-PLAT-0018, 0029, 0030)

* **Read-only game data**: the APK asset `asm.mp3` (an archive, not audio),
  opened by Java `openFd` and passed as (FileDescriptor, start, length) to
  `JNIDatInit`; native keeps a `FILE*` from `fdopen(dup(fd),"rb")` in
  `gDatFP`, reads a 244-byte header (`fread(buf,1,0xf4,fp)` 0x15368), mallocs
  and reads the directory, then **rewrites each 24-byte directory record's
  field +4 from an offset into an absolute 32-bit pointer** (0x153e0–0x15414)
  and hashes it into `gDatHash`. Later reads: `PfmLoadFileDat` 0x14c74
  (`fseek(SEEK_SET)` + `fread`), with optional `JAVAUnZip/UnJpg/UnPng`
  decoding in Java.
* **Writable files** are exclusively app-private, through Java
  `openFileOutput/openFileInput/deleteFile` (callbacks 3–7). Native writers
  pass **raw memory**:

| File | Buffer | Size | Writers (v7a) |
|---|---|---|---|
| `asm.cfg` | `gConfig` (0x90ef4) | 0x130 | `SetGameState` 0x7a780, `cROptions::UnInit` 0x59ed0, `cRGUI::UnInit` 0x540a0, `cRHighScore::AI` 0x570c0, `cRSubHighScore::MiniSave` 0x55920, `cRSubGame::Init` ×2 (runtime name) — all via `gRegisterSaveFile` 0x21638 → `PfmSaveFile` |
| `of.cfg` | `gOFOData` (0x380aa4) | 0x2d00 | `OFOSave` 0x7d708 (also reached from Java via `JNIOFOSave`) |
| `RandTable.bin` | `gRMathRand2Table` (0xd10b0) | 0x3ffe | `gRMathRand2Init` 0x19630, first run only (§8) |
| `hs_%08i.bin`, `tt_%03i.bin` (names from `cRSubHighScore::MiniFileName` 0x54f14/0x54f2c) | high-score / time-trial blobs | runtime | `cRSubHighScore::MiniSave` 0x558ec via `RShellSaveFile`; deleted by `MiniDelete` 0x55150 |
| text formats | `"%i %i %f %i %i %i %i %f %i %i \"%s\"\r\n"`, `"Number:%i\n"`, `"ID:%s ,%i ,%i\n"` | runtime | `SaveMiniData` 0x55014, `OF{Load,Save}HighScores` |

  Because `asm.cfg`/`of.cfg` are memory images of C structs, their byte
  layout (sizes, padding, endianness, any pointer-sized field) is part of the
  save-file format; see `docs/ABI_PORTING.md` §1–§3.
* `fopen`/`chdir`/`getcwd` paths are all in likely-dead functions (§1).

---

## 8. Randomness (EV-PLAT-0031, 0032, 0048)

* `Rand()` 0x167f0 = `gRMathRand2Table[idx = (idx+1) mod 8191] & 0x7fff`
  (u16 index `gRMathRandIndex` 0xd50b0, unsigned modulo); `RAND(max,label)`
  0x181f8 = `Rand() · 2^-15 · max`; `RAND`/`SRAND` together have 66 call
  sites (level building, AI, particles). `RandSeed(int s)` 0x19588 stores `s % 8191` (C remainder,
  truncating; negative seeds give a negative remainder stored as u16) and
  calls `srand48(s)`; callers `cRClickStart::AI`, `cRSubGame::BuildLevel`.
* The table (8191 × u16) is produced once by `gRMathRand2Init` 0x195d0 —
  `(uint16_t)lrand48()` ×8191 — then **saved as `RandTable.bin`** and loaded on
  every later start. `lrand48` is called nowhere else except dead
  `gRegisterInit`. `RMathInit` (which runs it) is called from `AppInit`
  (0x16674) before any `RandSeed`, so the first-run table comes from the libc's
  unseeded default state — *likely*; bionic's default is *likely* the BSD
  `0x1234ABCD330E` (glibc's is 0: verified on this host, see
  `docs/ABI_PORTING.md` §9). A predicted table for the BSD default is written
  by `tools/validation/abi/c/run_abi_c_checks.sh`
  (`RandTable.predicted.*.bin`, FNV-1a 0xb001bee8) for comparison with a table
  pulled from a device running the original.
* Determinism consequence: after the table exists, game randomness depends
  only on the table and the seeds; `srand48` has no further observable
  effect. Replays/tests should pin the table file.

---

## 9. Debug / stdio output (EV-PLAT-0033)

Native `wprintf(char*, …)` 0x19948 is an empty function (it is not the C
library's `wprintf`); `JNIDebug` returns 0, so Java `wprintf` prints nothing.
`RShellPrintText/Warning/Error` format with `vsprintf` into a 4096-byte stack
buffer (overflow if the text is longer) and pass it to the empty `wprintf`.
The only real stdio output (`printf`, `putchar`, `fwrite` to `stdout` via
`__sF`) is in dead `gRegister*` code; on Android app stdout goes to
`/dev/null` anyway. The port may route diagnostics to `__android_log_print`
without changing behaviour.

---

## 10. Android shell of the port (`android/`)

* Package/namespace `com.sandlotgames.snailmail` preserved (JNI names and the
  26 callbacks unchanged, verified by `tools/validation/platform/check_shell_parity.py`);
  `applicationId com.sandlotgames.snailmail.port`; arm64-v8a only;
  16 KB-aligned, uncompressed native library.
* OpenFeint removed: `MyOpenFeintDelegate` deleted, `SnailMailApplication`
  no longer initialises the SDK, manifest drops its activities and the
  INTERNET/NETWORK_STATE/GET_ACCOUNTS permissions. The seven
  `JAVAOpenFeint*` callbacks remain with their descriptors and report "no
  user / offline / failed" (the original's own failure paths), never success.
* JNI bridge (`android/app/src/main/cpp/jni_bridge.cpp`): 4 entry points
  implemented from established trivial bodies (`JNIAudioInit`, `JNIDebug`,
  `JNIDatUnInit`, `nativeDone`); the other 14 log at ERROR and throw
  `IllegalStateException` until their reconstructed code exists. The app
  therefore stops at `JNIDatInit` in `onCreate` today — by design.
* Other deliberate changes are marked `PORT-CHANGE` in the sources (single
  `super.onCreate`, manifest `exported`/`configChanges`/theme); modern
  lifecycle work is listed as `TODO(port)` (audio focus, deprecated
  SoundPool/Vibrator/wake-lock APIs, sensor rate cap and unregistering,
  EGL-context loss path, non-SDK `FileDescriptor.descriptor`).

---

## 11. Open questions

* Q1 — bionic (2011) default `rand48` state: confirm `0x1234ABCD330E` by pulling
  `RandTable.bin` from a device that ran the original, or from the bionic
  source of that era.
* Q2 — `gConfig` / `gOFOData` field layout: do they contain pointer-sized or
  `long` fields? (Needed for save compatibility; native-analysis area.)
* Q3 — EGL config: what colour/depth sizes did `GLSurfaceView`'s default
  chooser pick on 2011 devices vs today (depth precision affects the
  −0.004 depth-range outline trick)?
* Q4 — Is `FileDescriptor.descriptor` still accessible to JNI for
  targetSdk 35 (greylist status)? Irrelevant if the port passes the fd by a
  public API.
* Q5 — Voice line names: confirm they come from archive data read by
  `cRVoiceManager::Init`.

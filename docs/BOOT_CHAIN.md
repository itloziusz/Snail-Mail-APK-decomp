# Boot chain and frame loop

Scope: how `libsnailmail.so` goes from `System.loadLibrary` to the steady-state
frame loop, and what each JNI entry point does. The native part below was
traced statically in `v7a` (`sha256 e43bc913…a466`). Unless a line says
otherwise, every address is `v7a:`. The `v5` build has the same symbols and
structure at different addresses, but no claim here has been re-verified on
`v5`.

Confidence labels follow `docs/CONVENTIONS.md`:

* **established**: read directly from instructions or relocated data.
* **likely**: strong static inference.
* **hypothesis**: plausible, needs evidence.

`EV-NAT-xxxx` IDs refer to `analysis/evidence/native.jsonl`.

These tools produced the evidence:

* `tools/decompilation/elf_audit.py`
* `function_index.py`, whose resolved data refs are in `analysis/native/xrefs.v7a.json`
* `classes.py`
* Ghidra 11.4.2 pseudocode in `analysis/native/generated/decomp/v7a/`. This is regenerable, used only as a reading aid, and every claim was checked against the disassembly.

## Managed side (merged from `docs/APK_AUDIT.md` §10)

Full detail, smali line references and evidence IDs are in `docs/APK_AUDIT.md`
§10 and `docs/JNI_MAP.json`. Thread identities follow Android framework
semantics and are **likely**; call order inside each thread is **established**
from smali.

| # | Thread | Step | Native effect | Confidence |
|---|---|---|---|---|
| M1 | main | `SnailMailApplication.onCreate` → `OpenFeint.initialize(...)` | none (library not loaded yet) | established |
| M2 | main | `SnailMailActivity.<clinit>` → `System.loadLibrary("snailmail")` | dynamic linker runs 11 `.init_array` constructors (§0.a); no `JNI_OnLoad` | established |
| M3 | main | `onCreate`: `JNIDebug()` (returns 0), wake lock, fullscreen, `new ADGLSurfaceView` → `setRenderer` (GL thread started), `setContentView`, `new SoundPool(8,3,0)` | — | established (order) |
| M4 | main | `onCreate`: `openFd("asm.mp3")` → **`JNIDatInit(fd,(int)start,(int)len)`** | §1 | established |
| M5 | main | `onCreate`: accelerometer registered (`SENSOR_DELAY_FASTEST`, never unregistered) | `JNIAccelerometer` calls begin | established |
| M6 | main | `onResume` → `mGLView.onResume()`, wake lock acquire; surface created after the window is laid out | — | likely (framework) |
| M7 | GL | `onSurfaceCreated`, first in process → **`nativeInit`**; later → **`nativeReInit`** (+ no-op `JNIAudioInit` after a fresh `onCreate`) | §2 | established (branching), likely (thread) |
| M8 | GL | `onSurfaceChanged(w,h)` → **`nativeResize`** | §2 | established |
| M9 | GL | `onDrawFrame` → **`nativeRender(HasFocus ? 0 : 1)`** every frame (`RENDERMODE_CONTINUOUSLY`) | §3 | established |
| M10 | main | `onPause`: `mGLView.onPause()`, wake lock release, **`JNIResourceManagerInvalidate`**, music pause | marks resources for reload (§3.3) | established |
| M11 | main | `onStop`: `SoundPool.release()`, **`JNIDatUnInit`** (no-op); `onRestart`: new `SoundPool`, no `JNIDatInit` | archive stays open for process lifetime | established |

Ordering conclusions:

* `JNIDatInit` (M4) completes before `nativeInit` (M7): **likely**. The surface
  cannot exist until `onCreate`/`onResume` have returned on the main thread. The
  app itself does no synchronisation.
* `nativeReInit` cannot precede the first `nativeRender`: **likely**.
  `onSurfaceCreated`, `onSurfaceChanged` and `onDrawFrame` run in one GLThread
  loop iteration.
* `nativeReInit` can still run **before `Game` is allocated**. `AppInit`
  allocates `Game` only in `gAppState` 0, after `G0StartBlackCount` (= 2) black
  frames. If the surface is recreated within those first frames, `nativeReInit`
  writes `*(Game+0x718b4)` with `Game == NULL`. This is a **hypothesis** about a
  narrow start-up crash in the original; it is not observed.

## 0. Load time (dynamic linker)

| step | what | evidence | confidence |
|---|---|---|---|
| 0.1 | No `JNI_OnLoad` export; no `DT_INIT`/`DT_FINI`/`DT_FINI_ARRAY`; no `dlopen`/`dlsym` imports. JNI binding is by `Java_…` symbol name only. | `elf_audit.v7a.json` (`exports.has_JNI_OnLoad=false`, `dynamic_flags`) | established (EV-NAT-0005) |
| 0.2 | `DT_TEXTREL` is set. 12 relocations patch `.text` (2 `R_ARM_ABS32` to `__cxa_call_unexpected` and `__gnu_Unwind_Find_exidx`, 10 `R_ARM_RELATIVE`). All of them sit in the libgcc unwinder at `0x7f540`–`0x7fab0`. Game code has no text relocations. | `elf_audit.v7a.json` `relocations.textrel` | established (EV-NAT-0004) |
| 0.3 | `DT_INIT_ARRAY` holds 11 entries, each an `R_ARM_RELATIVE` slot with no 0/-1 sentinels. Bionic runs them in ascending index order: this ordering is **likely**, based on platform behaviour and not observed here. | `elf_audit.v7a.json` `init_array` | established (entries), likely (order) (EV-NAT-0006) |

### 0.a INIT_ARRAY constructors

`tColour::tColour()` (`0x17db0`) and `tColourSmall::tColourSmall()` (`0x1808c`) are 4-byte `bx lr` stubs. Constructor loops that only call them have **no observable effect**; this is established from the size and instructions (EV-NAT-0007).

| # | slot | target | what it initialises (globals written) | observable effect |
|---|---|---|---|---|
| 0 | `0x8ab5c` | `0x13b4c` `_GLOBAL__I_Ad.cpp` | Default field values in `gConfig` (`0x90ef4`, 304 B): floats 0.6, 0.75, 0.3, 10.0, 2.5, −1.5, −1.0, 270.0 and ints 40, 0x1fe, 1… | `gConfig` defaults, later overwritten by `asm.cfg` if present (step 3, state 2). Field meaning is open. |
| 1 | `0x8ab60` | `0x1822c` `_GLOBAL__I_RMaths.cpp` | `tMatrix::tMatrix(float)` on `gUnitMatrix` (arg 1.0); no-op `tColour` on `tColWhite` | `gUnitMatrix` is set; the exact content comes from `0x16d68` and is **likely** the identity scaled by 1.0 |
| 2 | `0x8ab64` | `0x1a46c` `_GLOBAL__I_RShell.cpp` | `RShellMemory[0] = 0` | Memory-pool header cleared |
| 3 | `0x8ab68` | `0x1d294` `_GLOBAL__I_RObject.cpp` | no-op `tColour` on `GTempColour` | none |
| 4 | `0x8ab6c` | `0x21f70` `_GLOBAL__I_Font.cpp` | `cRBod::cRBod()` (`0x20744`) ×128 over `RFont3D` (stride 0x2c): sets the vptr to `_ZTV5cRBod`+8, flags `0x2000020`, colour white, zeroes fields, `gBodCount++`; no-op `tColour` ×512 over `FontPrintBuffer` | `RFont3D[128]` constructed; `gBodCount` = 128 after static init (**likely**) |
| 5 | `0x8ab70` | `0x2ce58` `_GLOBAL__I_RSprite.cpp` | no-op `tColour` over `gRSpriteManager` entries and `gSpriteDummy` | none |
| 6 | `0x8ab74` | `0x2e190` `_GLOBAL__I_Game.cpp` | empty function | none |
| 7 | `0x8ab78` | `0x73b08` `_GLOBAL__I_SubGame.cpp` | no-op `tColour` over the 10 `gLocColourLookup*` tables | none |
| 8 | `0x8ab7c` | `0x76f6c` `_GLOBAL__I_Voice.cpp` | Zeroes one word in each of 16 records inside `gVoiceManager` (stride 0x18, words `0x378814`…`0x378994`) | Voice slots cleared |
| 9 | `0x8ab80` | `0x799fc` `_GLOBAL__I_LoadingBar.cpp` | `gLoadingBar+0x34` (`0x37c9cc`) = 0 | Loading bar inactive |
| 10 | `0x8ab84` | `0x7a858` `_GLOBAL__I_GL.cpp` | no-op `tColourSmall` on `GLColour`, `BufferColour` | none |

Two further facts about static state:

* There is no `__cxa_atexit` import and no FINI entry, so no global destructors exist.
* Every other global is zero-initialised `.bss` (`0x31fbdc` bytes; see `classes.v7a.json` → `bss`).

## 1. `JNIDatInit` (Activity `onCreate`) `0x15244`

Arguments, established from register use:

* `r0` = `JNIEnv*`
* `r1` = `jclass`
* `r2` = `java.io.FileDescriptor` object
* `r3` = start offset, 32-bit. It is stored to `gJavaAssetStart`.
* `[sp]` = length, 32-bit. It is stored to `gJavaAssetLength`.

This agrees with the JNI worker's `docs/JNI_MAP.json`, which lists `JNIDatInit` as `private static native` with descriptor `(Ljava/io/FileDescriptor;II)V`. The same file gives `nativeRender` the descriptor `(I)V`.

The steps, all established (EV-NAT-0010):

1. Makes these JNI calls through the env function table:
   * `FindClass("java/io/FileDescriptor")` at `+0x18`
   * `NewGlobalRef` at `+0x54`
   * `GetFieldID(cls, "descriptor", "I")` at `+0x178`
   * `GetIntField(fd, …)` at `+0x190`

   If the class or field lookup fails, or the fd argument is null, it only logs through `wprintf` and returns.
2. `gJavaAssetFid = dup(fd)`, `gJavaAssetStart = start`, `gJavaAssetLength = len`, `gDatFP = fdopen(gJavaAssetFid, "rb")`. If `gDatFP == 0` it returns.
3. `fseek(gDatFP, start, SEEK_SET)`, then `fread(hdr, 1, 0xF4, gDatFP)`, then `gDat = RShellMemoryMalloc(*(int*)(hdr+8), "NOT GIVEN")`.
4. Seeks back to `start` and reads `hdr+8` bytes into `gDat`. `gDat[0]` is the entry count, followed by 24-byte records. Each record's word at `+4` is rebased from a file-relative offset to an absolute pointer (`gDat + off`).
5. Builds the name index:
   * `cRHash::Init(&gDatHash, gDat[0], DatHashGetString)`, with `this`=`r0`, count=`r1`, function pointer=`r2` loaded from GOT at `0x153c4`.
   * `cRHash::Add(&gDatHash, name, i)` for each record.
6. Calls `DeleteLocalRef` (`+0x5c`) on the class and on the global ref. The `DeleteLocalRef` on a global ref is harmless but sloppy.

Globals written: `gJavaAssetFid`, `gJavaAssetStart`, `gJavaAssetLength`, `gDatFP`, `gDat`, `gDatHash`.

Required predecessor: none native. It runs before the GL thread starts, per the managed side.

The archive stays open through `gDatFP` for the life of the process. `JNIDatUnInit` (`0x13c70`) only calls the no-op `wprintf` twice and never closes it (established).

Later reads go through `RShellLoadFile` (`0x1b980`) → `RShellDatFind` → `PfmLoadFileDat` (`0x14c74`). `PfmLoadFileDat` does `fseek`/`fread` at `gJavaAssetStart + offset`. It then decodes through the Java callbacks `JAVAC_UnZip`, `JAVAC_UnPng` or `JAVAC_UnJpg`, selected by a switch on the record type.

If a name is not in the archive, the game falls back to real files via `PfmLoadFile` → `JAVACLoadFile` → Java `JAVALoadFile`. The record format belongs to the assets worker.

## 2. GL thread: surface callbacks

| entry | addr | body (all established from disassembly) | globals R/W | required predecessor |
|---|---|---|---|---|
| `nativeInit` | `0x15650` | `JAVA_RegisterFunctions(env, thiz)` (`0x13ca4`); `cRResourceManager::Init(&gResourceManager)` (`0x7e8b4`: count=0, 1200 entries ×0x8c, entry state=0, manager state=0, flag `+0x29048`=0); `G0StartBlackCount = 2` | W `gJavaEnv`, `gJavaObj`, `gJavaClass`, `gJAVAFunction[*].methodID`, `gResourceManager`, `G0StartBlackCount` | none native. It does **not** call `importGLInit`/`appInit`; game init is deferred into the per-frame `AppInit` state machine |
| `JAVA_RegisterFunctions` | `0x13ca4` | Caches env and thiz; `GetObjectClass` (`+0x7c`); `GetMethodID` (`+0x84`) for 26 entries of `gJAVAFunction` (`0x8b3f0`, 12-byte records `{methodID, name, sig}`), entries 0–25 `JAVALoadSample … JAVAVibrate`. For example, `JAVATime` is at `+0x114` and `JAVATimeHi` at `+0x120`. | as above | Called on every surface creation, so the cached `JNIEnv*` is refreshed per GL thread |
| `nativeReInit` | `0x155f0` | `JAVA_RegisterFunctions`; `InitGL()` (`0x7ab84`); `cRResourceManager::ReInit(&gResourceManager)` (`0x7ed5c`: splash setup, `Invalidate` = every entry state 1, manager state=1, flag=1); `Game->[+0x718b4] = 4`; `G0StartBlackCount = 2` | R `Game`; W `gResourceManager`, `G0StartBlackCount`, `*(Game+0x718b4)` | `Game != NULL`. There is no null check, so a `nativeReInit` before `AppInit` state 0 has run would fault. That is a **hypothesis**, see Managed side: reachable only if the surface is recreated during the first black frames (**hypothesis**) |
| `JNIAudioInit` | `0x13ad0` | `bx lr` (no-op) | — | — |
| `nativeResize(w,h)` | `0x1556c` | `wprintf(...)` (no-op); `gG0DeviceScreenWidth = gG0ScreenWidth = (float)w`; `gG0DeviceScreenHeight = gG0ScreenHeight = (float)h`. No GL call here; `G0Render` issues `glViewport` every frame | W the four screen floats | Must precede `AppInit` state 0, because `RShellInit` reads `gG0ScreenWidth/Height`. GLSurfaceView guarantees `onSurfaceChanged` before the first `onDrawFrame` (**likely**) |
| `nativePause` | `0x15500` | NDK San Angeles sample leftover: toggles `_ZL12sDemoStopped`, stores `_getTime()` (gettimeofday, ms) in `sTimeStopped` or adjusts `sTimeOffset`. All three statics are referenced only here, in both resolvers (EV-NAT-0015) | R/W `sDemoStopped`, `sTimeStopped`, `sTimeOffset` | **No effect on the game** (established: no other readers). Pausing works through the `pauseFlag` argument of `nativeRender` |
| `nativeDone` | `0x1555c` | `appDeinit()` (`bx lr`) then tail-calls `importGLDeinit()` (`bx lr`) | — | no-op |
| `JNIResourceManagerInvalidate` | `0x142fc` | `cRResourceManager::Invalidate(&gResourceManager)`: every used entry's state=1, and `gLoadingBar+0x35`=1 (`0x37c9cd`) | W `gResourceManager`, `gLoadingBar` | The actual reload only starts once the manager state becomes non-zero (`ReInit`) |

`InitGL` (`0x7ab84`) sets up GL state and a shared index buffer. Its calls, all established from the operands at `0x7ac00`–`0x7acd8`:

1. Builds a 6-index-per-quad table for 256 quads in `RShellMemoryScratch()`.
2. Uploads it: `glGenBuffers(1, &gSpriteIndexArrayVBO)`, `glBindBuffer(0x8893 GL_ELEMENT_ARRAY_BUFFER)`, `glBufferData(0x8893, 0xC00, table, 0x88E4 GL_STATIC_DRAW)`, then unbind.
3. `glEnable(0x0BD0 GL_DITHER)`, `glEnable(0x809D GL_MULTISAMPLE)`, `glShadeModel(0x1D00 GL_FLAT)`.
4. `glClearColor(0,0,0,0)`, `glClearDepthf(1.0)`.
5. `glEnable(0x0B71 GL_DEPTH_TEST)`, `glDepthFunc(0x0203 GL_LEQUAL)`, `glDepthRangef(0,1)`.
6. `glHint(0x0C50 GL_PERSPECTIVE_CORRECTION_HINT, 0x1101 GL_FASTEST)`, `glHint(0x0C52 GL_LINE_SMOOTH_HINT, GL_FASTEST)`.
7. `glDisable(0x0BC0 GL_ALPHA_TEST)`, `glColor4f(1,1,1,1)`.

It also resets `gBindTextureRefLast` and `gG0BlendMode` to −1. The platform worker owns the GL census.

## 3. Per-frame: `nativeRender(env, thiz, pauseFlag)` `0x1549c`

```
nativeRender:  if (AppInit() == 0) return;  appRender(pauseFlag != 0)     (tail call)
```
Established by the instructions at `0x154a4`–`0x154bc` (EV-NAT-0020).

### 3.1 `AppInit()` `0x1639c`: boot gate and state machine (runs every frame)

Order, all established:

1. If `G0StartBlackCount > 0`, decrement it, call `G0Black()` (`glClearColor(0,0,0,0)` and `glClear(COLOR|DEPTH)`), and return 0. This gives two black frames after every `nativeInit` or `nativeReInit`.
2. If `gGameValid && gOFOLoadFlag`, clear the flag and call `OFOLoad()` (`0x7d9c0`, reads `of.cfg`).
3. If `gOFOSaveFlag`, call `OFOSave()` (`0x7d6d8`, writes `of.cfg`) and clear the flag. The Java OpenFeint callbacks set this flag through `JNIOFOSubmitCB`/`JNIOFOUnlockCB`.
4. If `cRResourceManager::AI(&gResourceManager)` (`0x7eb4c`) returns 0, return 0 (see 3.3).
5. If the loading bar is active (`gLoadingBar+0x34`):
   * if `+0xbd` is set, return 1;
   * otherwise call `cRLoadingBar::AI()`;
   * if `+0x24` is set, return 0;
   * if `+0xd3` is set, clear it and return 0.
6. `switch (gAppState)` through the jump table at `0x16470` (`addls pc,pc,r3,lsl#2`, 11 cases). Every case except 10 then does `gAppState++` and returns 0. Each state is therefore one frame, and nothing renders through `appRender` until state 10.

| `gAppState` | work (callee addresses) | effect |
|---|---|---|
| 0 | `RShellInit` `0x1b718`: memory pool, music and sound tables, `RShellScratch = Malloc(2 MiB)`, `RShellMusicMemoryBuffer = Malloc(0x64000)`, input centre = screen/2.<br>`RMathInit` `0x19698`: 32768-entry `RMathSin`/`RMathCos` float tables from libm `sin`/`cos` (double); `gRMathRand2Init` loads `RandTable.bin` or generates it with unseeded `lrand48` and saves it.<br>`Game = operator new(0x3a6468)` (`0x16684`–`0x166a0`, 3,826,792 B) + `cRGame::cRGame()` `0x1605c`, which installs 13 vtables.<br>`InitGL()`; `cRLoadingBar::Init()` `0x7a660` | Engine core and game object exist |
| 1 | `cRGame::Init0` `0x3a230`: textures, objects, sound bank (`gSFXBank`), keyboard, fade, `LoadPaths`, accelerometer, cheat, voice manager | |
| 2 | `gRegisterLoadFile("asm.cfg", gConfig)`, which goes `PfmLoadFile` → `JAVACLoadFile` → Java `JAVALoadFile` | **Config and save loading.** It overwrites the INIT_ARRAY defaults. `SetGameState` `0x7a720` later writes `asm.cfg` back (0x130 bytes) |
| 3 | `cRGame::Init1` `0x320bc`: overlay, landscape import (`Help.txt`, `Splash.txt`, `Starmap*.txt`), SM tracks, `cRDirectX::Init` | |
| 4 | `cRGame::Init2` `0x31804`: fonts, sprite sets, galaxy/GUI/logo/splash open, options apply, cameraman, sub-tracks | |
| 5 | `cRGame::Init3` `0x2f6d8` (8.5 KB): level geometry tiles and `ObjectProc*` builders, `cRWorld::Init`, DirectX model loads | |
| 6 | `cRGame::Init4` `0x32318`: flash, border stack, high scores, star manager, tips | |
| 7 | `cRGame::Init5` `0x2f4f4`: `RShellSetMouse`, `cRObjects::BuildObjects`, `cRBackdrop::Open` | |
| 8 | `G0TextureSetLoad(1)` `0x7b4b8`: `glGenTextures` for the texture set, then loads every texture | GPU upload |
| 9 | `cRGame::InitLast` `0x2f438`: backdrop init, keypad open, options apply, `OFONewUser`, `OFInit`, sets `G0GameInitFlag`.<br>`cRLoadingBar::Finish`; `gGameValid = 1`; `cRFade::StartOn` | Input is now accepted: `JNIMouseEvent` requires `gGameValid` |
| 10 | `return 1` (no increment) | Steady state |

Boot is therefore 2 black frames, then 10 init frames (states 0–9), then gameplay frames. Loading-bar and resource-manager gating can add frames.

### 3.2 `appRender(pause)` `0x15690`: fixed-timestep update, then render

All established from the disassembly at `0x15690`–`0x15834` (EV-NAT-0021, 0022):

* If `pause != 0` and the auto-quit counter `*(Game+0x718b4)` is 0, set it to 4. `cRQuit::AI` (`0x5f50c`) turns a positive counter into an injected `KeySet(1)`, which is DirectInput `DIK_ESCAPE`, so the game opens its in-game quit/pause menu (**likely**).
* `Game->f44 = Game->f3c; Game->f3c = Game->f40`, two floats with an open meaning.
* Clock: `T = GetTime()` (`0x7d110`, a tail branch to `JAVATime` `0x14118`). That function combines the Java `JAVATime()` (low 32 bits) and `JAVATimeHi()` (high 32 bits) of `System.nanoTime()` (smali `ADRenderer.smali:982-1013`: `JAVATime` stores `System.nanoTime()` and returns `long-to-int`; `JAVATimeHi` returns `(int)(JTime >> 32)` via `shr-long`, **established**, EV-JNI) and divides by 1000 with `__aeabi_uldivmod`. **Units: microseconds, monotonic.** `gettimeofday` is used only by the dead `_getTime`/`nativePause` path.
* Fixed step `S = 0x411A = 16666 µs` (60 Hz), with `L = gTimeLast` (u64). Number of updates `n` and new `gTimeLast`:

  | condition | n | new `gTimeLast` |
  |---|---|---|
  | `T ≤ L` | 1 | `L` |
  | `L < T ≤ L+S` | 1 | `L+S` |
  | `L+S < T ≤ L+2S` | 2 | `L+2S` |
  | `L+2S < T ≤ L+3S` | 3 | `L+3S` |
  | `T > L+3S` | 4 | `T` (resync, drops time) |

  At least one update runs per rendered frame, even when the render rate is above 60 Hz. The inference that the simulation then runs faster than real time is **likely**. The first frame has `L = 0`, so it runs 4 updates and resyncs.
* Each update is `FontAI()` (`0x21944`) then `cRGame::AI(Game)` (`0x3af98`). `G0RenderNextFlag` is 0 for the first n−1 updates and 1 for the last. Text and OSD printers read the flag, so only the last update emits text.
* Render: `G0RenderAvailable = 1; G0Render(); G0RenderAvailable = 0`. `G0Render` (`0x7ad84`) does the following, unless `*(Game+0x328)` is set:
  1. `glViewport(0,0,devW,devH)`
  2. clears to black with depth 1.0
  3. tail-calls `cRGame::Render` (`0x3a724`)

  `PfmSwapBuffers` is a no-op (`bx lr`, `0x13b3c`): GLSurfaceView swaps.

### 3.3 `cRResourceManager::AI` `0x7eb4c`: GL resource reload after context loss (established)

| state | behaviour |
|---|---|
| 0 | idle; returns 1 |
| 1 | splash, then state 2 |
| 2 | splash, then `cRBackdrop::MakeVBO(Game+0x4d2e0)`, then state 3 |
| 3 | For each entry marked 1, reload by type: 0 = texture (`G0TextureLoad`), 2 = sample (`RShellSoundRegister`), 4 = objects (`cRObjects::ReBuildObjects`). The time budget per frame is `0x1046A` = 66,666 µs (`0x7ec3c`); when it is exceeded the function renders the splash and returns 0. When done: state 4. |
| 4 | final splash, `SetSplash("")`, state 0, `cRLoadingBar::AI`, `JAVAMusicRestart()` |

### 3.4 `cRGame::AI` `0x3af98`: game update (one call per update above)

Steps, established:

1. Input and global AI:
   * `KeyboardAI`
   * `cRFade::AI` (`Game+0x24`)
   * `RShellInputRegisterKeyboard(0,0,0)`
   * `cKeyPad::AI` (`+0xbf0`)
   * `cRMouse::AI` (`+0x228`)
   * `cRCheat::AI`
   * `cRVoiceManager::AI`
   * the frame counter `+0x2d8` is incremented
2. Inner accumulator: `+0x2d4 += 1.0f`, then `while (+0x2d4 > 1.0f) { +0x2d4 -= 1.0f; tick }` (1.0f literal at `0x3b238`). This gives one tick per call after a one-call warm-up. Why the accumulator exists is a **hypothesis**: other code might scale it for slow motion. No other writer has been identified yet.
3. Each tick:
   1. `cRFlash::AI` (`+0x25c`)
   2. `cRBackdrop::AI`
   3. `cRGalaxy::AI` for galaxy `Game[+0x392960]` (stride 0x8e6c, base `+0x392964`)
   4. walk the `cRBod` list headed at `+0x35c`, calling each object's **virtual slot 0** (`ldr pc,[vptr]` at `0x3b12c`, `0x3b17c`), and virtual slot 0 of the object at `+0xd4` (`0x3b194`)
   5. `cAccelerometer::AI`
   6. `cRGalaxy::Render`
   7. the `cRSprite::AI` lists (3 heads at `0x14cfd4`)
   8. `cREnemyManager::Init(+0x3a4648)`

**Screen/state manager (likely).** The per-frame dispatch is object-list based. Screens and actors are `cRBod` subclasses whose single virtual method is `AI()`. There are 42 vtables of 12 bytes each: offset-to-top 0, typeinfo 0 (RTTI absent), and one slot. The one exception is `cRGame`, whose slot is `cRGame::LevelInit(int)` (`classes.v7a.json`).

The persistent "resume" state is `SetGameState(state,a,b,c)` (`0x7a720`). It writes `gConfig+0xcc..0xe4` and saves `asm.cfg`. No function-pointer state table was found. The only function pointers materialised in data or code are:

* the 42 vtable slots
* the 11 INIT_ARRAY entries
* 3 GOT-held pointers: `DatHashGetString`, `TexturesFunctionGetName`, and `ObjectProcVertexCompare` (qsort)

Source: `elf_audit.v7a.json`, `xrefs.v7a.json` (EV-NAT-0024).

## 4. Input and other JNI entries (brief)

| entry | addr | behaviour | gating |
|---|---|---|---|
| `JNIMouseEvent(action, x, y?)` | `0x14318` | Scales x by `640.0/gG0DeviceScreenWidth`, giving a virtual 640-wide space (established). From the pseudocode (**likely**): action 0 → `cRMouse::ClickiPhone` + `ClickOn`; action 1 → `ClickiPhone` only when `*(Game+0x718fc)==2`, then `ClickOn`; any other action → `ClickOff`. The target is `cRMouse` at `Game+0x228`. The argument rendering is unreliable, so re-derive from the disassembly. | returns unless `gGameValid` |
| `JNIKey(keycode)` | `0x13d78` | Android keycode → ASCII → scancode: A–Z (29..54) `+0x44` gives 'a'..'z', and 0–9 (7..16) `+0x29` gives '0'..'9', both passed through `cKeyPad::ConvertCode(Game+0xbf0)`. Fixed mappings: 0x3e→0x39 (space), 0x43→0x0e (backspace), 0x42→0x1c (enter); these are DirectInput `DIK_*` values. `KeySet(code)` fires only when `*(Game+0xbf0) == 2`. For any other keycode, the path `0x13db4`→`0x13e04` calls `KeySet(r4)` with `r4` never written: the value is whatever the JNI caller left in the callee-saved register. This is an original bug (established); a port must pick a defined behaviour. | **no `Game` null check**: `0x13db8`–`0x13dc0` and `0x13dd8`–`0x13de4` dereference `Game` untested (established) |
| `JNIAccelerometer(x,y,z)` | `0x142bc` | `cAccelerometer::Input(Game+0xbd4, x, y, z)` | returns if `Game == 0` |
| `JNIDebug` | `0x13b44` | returns 0 | — |
| `JNIOFOInit(str)` | `0x7d81c` | tail-calls `OFONewUser` | — |
| `JNIOFOSave` | `0x7d71c` | tail-calls `OFOSave` (writes `of.cfg` on the caller's thread) | — |
| `JNIOFOSubmitCB` / `JNIOFOUnlockCB` | `0x7d44c` / `0x7d404` | set `gOFOSaveFlag`. The save happens in the next `AppInit` on the GL thread | — |

Threading, a **hypothesis**: input and accelerometer entries run on the UI thread and write into `Game` with no locking. This matches the original's behaviour; the port should decide whether to queue those events.

## 5. Directly established order vs static inference

* **Established, within native code:**
  * all call sequences above (the order of `bl` instructions)
  * the `AppInit` switch table
  * the fixed-step arithmetic
  * the `cRResourceManager` state transitions
  * the INIT_ARRAY contents
* **Inferred:**
  * the order in which Java invokes the entry points (managed side)
  * that bionic runs INIT_ARRAY in index order before `loadLibrary` returns
  * GLSurfaceView's callback ordering (`onSurfaceCreated` → `onSurfaceChanged` → `onDrawFrame`)
  * that `nativeRender`'s third argument is a pause flag, which comes from the brief and from use (it arms the ESC injection)
* **Not yet traced:**
  * the contents of `cRGame::Init0–5` beyond their direct callees
  * `cRGame::Render`
  * the virtual `AI()` implementations
  * `gConfig` field semantics
  * `cRLoadingBar` script (`gLoadingScript`)

## 6. First unresolved boundaries

| site | kind | what is known | how to resolve |
|---|---|---|---|
| `0x3b12c`, `0x3b17c`, `0x3b194` (`cRGame::AI`) | virtual call, slot 0 | Target set = slot 0 of the 42 vtables (37 distinct `AI()` implementations plus `cRBod::AI` and `cRGame::LevelInit`) | Map which classes are linked into `Game+0x35c`: the `cLinkedList<cRBod>::Add` callers in `Init1/2/4` |
| `0x52ff0`, `0x542b8`, `0x54360`, `0x5934c`, `0x5a05c`, `0x5a100`, `0x5b34c`, `0x5cb9c`, `0x63524`, `0x72800` | virtual slot 0 (`ldr pc,[r3]`) in `cRSubGolb::Create`, `cRGUI::Init`, `cRLogo::Init`, `cROptions::Init`, `cRStarManager::Init`, `cRParcelManager::AI`, `cRSubGame::AddRing`, `cRSubGame::AI` | same target set | same |
| `0x7d1dc` (`cRHash::Search`) | callback `[this+0x808]` | Stored by `cRHash::Init` (`0x7d354`). Only 2 candidates: `DatHashGetString` (`gDatHash`, from `JNIDatInit` and the dead `RShellDatInit`) and `TexturesFunctionGetName` (from `cRTextures::Init`) | per-instance; **likely** resolved |
| 60+ `mov lr,pc; ldr pc,[rX,#off]` sites in `JAVA*`/`JNI*` glue | `JNIEnv` function table | offsets map to JNI function indices | JNI worker (`docs/JNI_MAP.json`) |
| `gJAVAFunction` method IDs | Java callbacks | 26 names and signatures at `0x8b3f0` | JNI worker |
| `PfmLoadFileDat` `0x14c74` switch | decoder selection by record type | table resolved (switch) | assets worker |
| `Game+0x718b4`, `+0x718fc`, `+0x392960`, `+0xbf0` | fields of the 3.8 MB `cRGame` | only accesses are known | Build a `cRGame` layout from the constructor `0x1605c` and accessors |

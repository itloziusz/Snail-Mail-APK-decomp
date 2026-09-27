# APK audit: managed shell and JNI contract

Scope: the Android side of `com.sandlotgames.snailmail` 1.00: manifest, DEX,
resources, signing, the Java↔native (JNI) boundary, and the managed lifecycle.
The machine-readable JNI contract is in [`docs/JNI_MAP.json`](JNI_MAP.json).
The evidence ledger is `analysis/evidence/jni.jsonl` (`EV-JNI-xxxx`).

Confidence labels follow `docs/CONVENTIONS.md`:

* **established** means the claim was observed directly in bytes or instructions.
* **likely** means strong static inference that has not been observed directly.
* **hypothesis** means the claim is plausible and still needs evidence.

Claims that rest on Android framework behaviour rather than on these bytes
(thread assignment, GLSurfaceView internals, Bitmap pixel layout) are marked
**likely** at most.

| Artifact | SHA-256 |
|---|---|
| APK `original/com.sandlotgames.snailmail-1.00.apk` | `0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7` |
| `classes.dex` | `b430e061e63dba6860b8d2bf11d6840556a737bc816c82b59f7dd111ad2f7459` |
| `v7a` `lib/armeabi-v7a/libsnailmail.so` | `e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466` |
| `v5` `lib/armeabi/libsnailmail.so` | `96dbeaeb20c60d687301ca769656727467371489db5e3ed744a93248bc8d8136` |

## Reproduction

```sh
# DEX/APK side (needs dexdump); writes analysis/dex/managed_shell_scan.json
tools/inventory/managed_shell_scan.py --apk original/com.sandlotgames.snailmail-1.00.apk \
    --dex work/apk_unzip/classes.dex --smali work/apktool/smali > analysis/dex/managed_shell_scan.json
# Native JNI boundary (capstone + pyelftools); writes analysis/dex/jni_native_scan.json
tools/inventory/jni_native_scan.py --bin v7a=work/apk_unzip/lib/armeabi-v7a/libsnailmail.so \
    --bin v5=work/apk_unzip/lib/armeabi/libsnailmail.so \
    --managed analysis/dex/managed_shell_scan.json > analysis/dex/jni_native_scan.json
# Rebuild the curated outputs (numbers are pulled from the two scans above)
tools/inventory/build_jni_map.py        # -> docs/JNI_MAP.json (58 entries)
tools/inventory/build_jni_evidence.py   # -> analysis/evidence/jni.jsonl (70 claims)
# Verify every address/size/table entry recorded in docs/JNI_MAP.json (exit 1 on mismatch)
tools/inventory/jni_native_scan.py ... --check-map docs/JNI_MAP.json   # "90 record(s) checked, 0 problem(s)"
```

Both scans are deterministic. Two runs produce byte-identical output.
Manual cross-checks used `aapt dump xmltree|badging`, `apksigner verify --print-certs`,
`dexdump -d`, and `llvm-objdump --triple=armv7-linux-androideabi` or
`--triple=armv5te-linux-androideabi`.

---

## 1. Manifest (EV-JNI-0054, established)

Values come from `aapt dump xmltree`, which is authoritative. The apktool text
output agrees with it.

| Item | Value |
|---|---|
| package | `com.sandlotgames.snailmail` |
| versionCode / versionName | `1` / `1.00` |
| installLocation | `auto` (raw 0) |
| uses-sdk | `minSdkVersion=6`. `targetSdkVersion` is **absent**, so the platform treats it as 6. `maxSdkVersion` is absent. |
| application | `android:name=com.sandlotgames.snailmail.SnailMailApplication`, label `@string/app_name` = "Snail Mail", icon `@drawable/icon` (ldpi/mdpi/hdpi), `debuggable=false` |
| launcher activity | `com.sandlotgames.snailmail.SnailMailActivity` (MAIN/LAUNCHER), `screenOrientation=landscape` (0), `configChanges=keyboardHidden|orientation` (0xa0) |
| other activities | `com.openfeint.internal.ui.IntroFlow`, `com.openfeint.api.ui.Dashboard`, `com.openfeint.internal.ui.Settings`, `com.openfeint.internal.ui.NativeBrowser`. All use `Theme.NoTitleBar` and `configChanges=0xa0`. |
| services / receivers / providers | none |
| permissions | `INTERNET`, `ACCESS_NETWORK_STATE`, `WRITE_EXTERNAL_STORAGE`, `GET_ACCOUNTS`, `WAKE_LOCK`, `VIBRATE`. aapt also implies `READ_EXTERNAL_STORAGE`. |
| uses-feature | `glEsVersion=0x00010001` (OpenGL ES 1.1), `android.hardware.sensor.accelerometer` required, `android.hardware.touchscreen` required. Landscape is implied. |
| supports-screens | small, normal, large, anyDensity = true (xlarge not declared) |
| native-code (aapt) | `armeabi`, `armeabi-v7a` |

Permission use found in the DEX:

* `WAKE_LOCK`: `newWakeLock(26 = 0x1a = FULL_WAKE_LOCK, "DoNotDimScreen")` in `SnailMailActivity`.
* `VIBRATE`: `ADRenderer.JAVAVibrate`.
* `INTERNET` and `ACCESS_NETWORK_STATE`: OpenFeint (`ConnectivityManager` in `OpenFeintInternal.smali:3409`).
* `GET_ACCOUNTS`: OpenFeint `Util5.getAccountNameEclair`.
* `WRITE_EXTERNAL_STORAGE`: OpenFeint `WebViewCache`, `db/DB`, `Util`.

The game shell itself needs only WAKE_LOCK and VIBRATE.

Consequences for running the original today:

* The APK ships only 32-bit ARM code, so it cannot run on arm64-only devices.
* `targetSdkVersion` < 23 is refused for installation by Android 14 and later.
  This is a platform rule, not derived from the APK.

## 2. Resources of interest

* `string/app_name` = "Snail Mail". `string/hello` = "Hello World, SnailMailActivity!" is template residue.
* The remaining 37 strings are OpenFeint UI and error strings (`of_*`).
* `layout/main.xml` is an empty vertical LinearLayout. It is not used, because the activity calls
  `setContentView(ADGLSurfaceView)`.
* `drawable/sandlotloading.jpg` (70,352 B): resource id `0x7f02001a` never appears as an operand in
  the DEX. It is unreferenced by code, although it could be looked up by name (not observed).
* `drawable*/of_*` PNGs, `layout/of_*`, `menu/of_dashboard`: OpenFeint UI.

## 3. DEX summary (EV-JNI-0001..0005)

353 class_defs, DEX version 035, 397,560 bytes.

| Group | Packages | Classes | Classification |
|---|---|---|---|
| Game shell | `com.sandlotgames.snailmail` | 18 (9 real, 9 `R`/`R$*`) | original game code |
| OpenFeint SDK 1.6 | `com.openfeint.api{,.resource,.ui}`, `com.openfeint.internal{,.db,.notifications,.request,.request.multipart,.resource,.ui}` | 252 | optional external service (defunct) |
| Jackson JSON | `org.codehaus.jackson{,.annotate,.impl,.io,.sym,.type,.util}` | 67 | middleware (used by OpenFeint only) |
| Apache commons-codec | `org.apache.commons.codec{,.binary}` | 10 | middleware (OpenFeint only) |
| Google API client escape | `com.google.api.client.escape` | 6 | middleware (OpenFeint only) |

Game-shell classes (not counting R):

* `SnailMailApplication`
* `SnailMailActivity`
* `ADGLSurfaceView` (source file `SnailMailActivity.java`)
* `ADRenderer`, `ADRenderer$1`, `ADRenderer$2`, `ADRenderer$2$1` (source file `SnailMailActivity.java`)
* `AccelerometerListener`
* `MyOpenFeintDelegate`

Native methods: **19**, all in the game shell (EV-JNI-0002). The full list with
descriptors is in `JNI_MAP.json` (`direction: java_to_native`).

Code loading, reflection and process APIs were scanned across all 353 smali files (EV-JNI-0003..0005):

| Location | API | Nature |
|---|---|---|
| `SnailMailActivity.<clinit>` (`SnailMailActivity.smali:46-48`) | `System.loadLibrary("snailmail")` | the only native-library load |
| `com/openfeint/internal/ui/WebNav$ActionHandler.smali:701,713` | `Class.getMethod` + `Method.invoke` | OpenFeint web-UI action dispatch on its own class. No class loading. |
| `com/openfeint/internal/Util.smali:790-803` (from `createSymbolic`/`moveWebCache`) | `Runtime.exec("ln -s …")` | shell symlink for the OpenFeint web cache |

The scan found no `System.load`, `Runtime.load*`, `DexClassLoader`,
`PathClassLoader`, `DexFile`, `Class.forName`, `ClassLoader.loadClass` or
`ProcessBuilder`. OpenFeint loads no code dynamically.

OpenFeint credentials are embedded: the product key, secret and app id are
`static final` strings in `SnailMailApplication.smali:7-13` (EV-JNI-0057). They
are deliberately not reproduced here. The server base URL is
`https://api.openfeint.com` (`OpenFeintInternal.smali:178`, EV-JNI-0058).

## 4. Native libraries

| | v7a | v5 |
|---|---|---|
| ELF | ELF32 ARM LE ET_DYN, EABI5, `Tag_CPU_arch` v7 | ELF32 ARM LE ET_DYN, `Tag_CPU_arch` v5TE (soft-float) |
| Size in APK | 670,897 B (deflated) | 707,235 B (deflated) |
| NEEDED | libc, libstdc++, libm, libGLESv1_CM, libdl, liblog | same |
| Dynamic flags | `DT_SYMBOLIC`, `DT_TEXTREL` | same |
| JNI exports | 18 `Java_*`, no `JNI_OnLoad` | same set |

Neither library imports `__android_log_*`, `dlopen` or `dlsym`. liblog and
libdl are NEEDED but unused (EV-JNI-0060). Native `wprintf` is a no-op in both
builds (EV-JNI-0061). The GL imports are fixed-function ES 1.x only (45 `gl*`
symbols in each build). Detailed ABI and GL analysis belongs to the platform workstream.

## 5. Assets overview (from `analysis/apk/inventory.json`)

* `assets/` holds 171 entries, all **stored** (uncompressed):
  * 170 Ogg Vorbis files, 2,977,992 B in total. `JAVALoadSample` and `JAVAPlayMusic` open them as `<name>.ogg`.
  * `assets/asm.mp3`, 8,188,993 B. Its content type is `unknown-binary` and it is flagged as not MPEG.
* `asm.mp3` is the game data archive opened by `JNIDatInit`.
  * Its data starts at APK offset 58,804 (local header 58,759 + 30-byte header + 14 name bytes + 1 extra byte). That is the value `AssetFileDescriptor.getStartOffset()` reports.
  * `openFd()` works only on stored entries. The `.mp3` extension is in aapt's default no-compress list, which is the likely reason for the name.
* The native side reads a 244-byte probe at the asset start. It uses u32[0] as the entry count (734 in this file) and u32[2] as the directory byte size (34,701), a word that is also `record[0].data_offset`. See EV-JNI-0018. The archive format is documented by the asset workstream in `docs/ASSET_FORMATS.md`.
* That document also records the texture loader uploading the decoded TGA pixels as `GL_RGBA`. This agrees with the RGBA byte order of the Java decoders (section 9).

## 6. Signing (EV-JNI-0055, established)

* **v1 (JAR) scheme only.** v2, v3 and v4 are absent. `apksigner verify` reports "Verifies".
* Signer: `CN=Daniel Berstein, OU=CEO, O=Sandlot Games, L=Bothell, ST=Washington, C=US`, self-signed.
  * RSA 1024, `sha1WithRSAEncryption`, serial `0x4d6459b0`.
  * Valid 2011-02-23 to 2036-02-17.
  * Certificate SHA-256 `5b3cca3a9cb98df1c410e9ee5f948a8dc2910bfd761455baf8e2d820eeb8ac3b`.
* `META-INF/MANIFEST.MF` has 236 `SHA1-Digest` entries, one for every non-META-INF entry.
* A port necessarily uses a different signing key and should use a different package name. App-private save files (`openFileOutput`) therefore do not carry over.

## 7. Nested payload check (EV-JNI-0056, established)

Every non-Ogg APK entry outside `assets/`, `lib/` and `classes.dex` was scanned for
trailing data and embedded ZIP, ELF, DEX or Ogg magics
(`managed_shell_scan.json:apk_non_ogg_entry_checks`):

* `com/openfeint/api/doc-files/*` holds **6 PNGs**:
  * `appSettings`, `devDashAppName`, `myOpenFeintSampleProject`, `OpenFeintSDKProject`, `ProjectErr`, `sourceLink`.
  * All are valid, end with IEND and have 0 trailing bytes.
  * Chunks are only IHDR/iCCP/pHYs/IDAT/IEND.
* The same directory holds **8 `.htm` and 2 `.lbi` files**. They are plain text; the only non-ASCII bytes are UTF-8 punctuation (NBSP, en-dash, a mis-typed dagger).
* Together these are the OpenFeint SDK "Getting started" documentation, packaged by accident because the SDK was added as source. Nothing references them.
* All `res/` PNGs have no trailing data. `res/drawable/sandlotloading.jpg` has 0 bytes after EOI.
* No embedded magic numbers were found in any of these entries.

## 8. Dependency graph

```text
Java side
 SnailMailApplication ──► OpenFeint.initialize ──► OpenFeintInternal ─► Client (Apache HttpClient, ThreadPoolExecutor)
       │                                            ├─► Jackson / org.json / commons-codec / google escape
       │                                            ├─► android.webkit (Dashboard/IntroFlow), android.database (SQLite)
       │                                            └─► AccountManager, ConnectivityManager, SharedPreferences
 SnailMailActivity ──► System.loadLibrary("snailmail") ──► libsnailmail.so
       ├─► ADGLSurfaceView (GLSurfaceView) ─► ADRenderer (GLSurfaceView.Renderer, javax.microedition.khronos.*)
       ├─► SoundPool / MediaPlayer / AssetManager (audio)
       ├─► BitmapFactory, ZipInputStream (decoding for native)
       ├─► SensorManager (AccelerometerListener), PowerManager, Vibrator, InputMethodManager
       └─► OpenFeint API (Score, Leaderboard, Achievement, Dashboard, CurrentUser)
Native side (libsnailmail.so)
 libGLESv1_CM (graphics) · libm · libc (stdio on dup'ed APK fd, malloc, time) · libstdc++ (new, guards)
 static libgcc/EHABI unwinder · JNI back into ADRenderer (26 methods) for audio, files, image/zip decode, time, OpenFeint
```

| Dependency | Class |
|---|---|
| `com.sandlotgames.snailmail.*`, libsnailmail.so | original game code |
| OpenFeint SDK 1.6, Jackson, commons-codec, google escape | middleware for an **optional external service that is defunct**. OpenFeint was shut down in Dec 2012 (external fact). |
| Android framework: Activity/View/GLSurfaceView, Sensor, Power, Vibrator, IME, Context file APIs | Android platform |
| Apache HttpClient (`org.apache.http`, 313 refs from OpenFeint only) | Android platform (legacy). Removed from the default boot classpath for newer target SDKs. |
| libGLESv1_CM, EGL (via GLSurfaceView), `BitmapFactory` | graphics |
| `SoundPool`, `MediaPlayer` (Ogg) | audio |
| `java.util.zip.ZipInputStream` (Java inflate for native), `BitmapFactory` (PNG/JPEG) | compression / image codec |
| libc, libm, libstdc++, libdl, liblog | runtime |
| unresolved | none at the JNI level. Only the internal archive format and the engine semantics of the state bytes remain open. |

Score submission and achievement unlock, the only OpenFeint paths that carry
native pointers, run only when `OFIsUserLoggedIn()` is true (EV-JNI-0040). With
the servers gone, login cannot succeed, so the submit, unlock and login
callbacks are dormant.

The native code can still call `JAVAOpenFeintOpen` (`Dashboard.open()`),
`JAVAOpenFeintIsOnline` and `JAVAOpenFeintLastLoggedInUserID` from engine AI
code. A port can stub OpenFeint as "not logged in", with "online" meaning
network state only and the dashboard as a no-op, without changing game
behaviour (**likely**).

## 9. JADX vs smali verification of `com.sandlotgames.snailmail.*`

All 9 non-R classes were compared method by method with smali, 84 methods in
total. JADX output matches smali control flow and constants. The one exception
is cosmetic: JADX invents `throws` clauses such as
`onCreate(...) throws IOException` and `onPause() throws IllegalStateException`.
No `dalvik.annotation.Throws` exists on these methods (EV-JNI-0068).

Specific confirmations:

| Item | Result | Evidence |
|---|---|---|
| `ADGLSurfaceView.onTouchEvent` mapping | **Confirmed.** `ACTION_DOWN`(0)→`JNIMouseEvent(0,x,y)`, `ACTION_UP`(1)→`JNIMouseEvent(2,…)`, `ACTION_MOVE`(2)→`JNIMouseEvent(1,…)`. All other actions (CANCEL, OUTSIDE, POINTER_*, and any action value with pointer-index bits) are ignored. The method always returns true. The JADX warning comes from the packed-switch targets being laid out out of key order: payload at DEX file offset 0x0266dc, keys 0,1,2 map to code units 0x09, 0x22, 0x16. | EV-JNI-0024, `ADGLSurfaceView.smali:303-394` |
| `onKeyDown` | **Confirmed.** Logs first. BACK with `getRepeatCount()==0` returns `true` without calling super. Otherwise, for `getAction()==ACTION_DOWN` (always true in onKeyDown): DPAD 19-22 are only logged; 29-54 (A-Z), 7-16 (0-9), 62 (SPACE) and 67 (DEL) call `JNIKey(keycode)`; 66 (ENTER) hides the soft keyboard and then calls `JNIKey`. Every non-BACK path, including those that called JNIKey, ends with `return super.onKeyDown(keycode, event)`. | EV-JNI-0026, `ADGLSurfaceView.smali:54-301` |
| `SnailMailActivity.onCreate` order | **Confirmed**, including `super.onCreate` twice (`:163`, `:176`), `newWakeLock(0x1a,"DoNotDimScreen")`, `requestWindowFeature(1)` after the first super call, `setFlags(0x400,0x400)`, `new SoundPool(8,3,0)`, and `openFd("asm.mp3")` → `(int)getStartOffset()`, `(int)getLength()` via `long-to-int` → `JNIDatInit(getFileDescriptor(), start, length)`. | EV-JNI-0021..0023 |
| static initialiser | `AudioInitFlag=false; MusicVolume=0.0f; System.loadLibrary("snailmail")` | EV-JNI-0003 |
| `ADRenderer.JAVATime` / `JAVATimeHi` | **Confirmed.** `JTime = System.nanoTime(); return (int)JTime;` / `return (int)(JTime >> 32)`. The smali is `shr-long` by `0x20` then `long-to-int`, an arithmetic shift. The high word depends on the static written by the preceding `JAVATime()` call. | EV-JNI-0030 |
| `AccelerometerListener` math | **Confirmed.** `x*x+y*y+z*z` evaluated in **float** (`mul-float`/`add-float`), widened to double for `Math.sqrt`; `n==0.0 → 1.0`; each component `(float)(c/(double)n)`; low-pass `last += (cur-last)*0.3f` in float (`0x3e99999a`); `JNIAccelerometer(-last_x, -last_y, +last_z)`. Sensor is `getSensorList(TYPE_ACCELEROMETER=1).get(0)` and `registerListener(this, sensor, 0)`, where rate 0 = `SENSOR_DELAY_FASTEST`, with no Handler. `stop()` exists but is **never called**. | EV-JNI-0028 |
| `JAVAUnZip` loop | **Confirmed.** A single `getNextEntry()`, so only the first entry is read. `do { read = zin.read(tmp,0,4096); if (read != -1) for (i<read) Buffer[index++] = tmp[i]; } while (read != -1); zin.close();` Any exception, including an `ArrayIndexOutOfBounds` from an undersized `Buffer`, is swallowed and logged, and then `close()` is skipped. | EV-JNI-0035 |
| `JAVAUnPng` / `JAVAUnJpg` | **Confirmed.** `decodeByteArray(src,0,src.length,{inDither=true, inPreferredConfig=ARGB_8888})` then `bmp.copyPixelsToBuffer(ByteBuffer.wrap(dst))`. There is no null check. In memory the bytes are R,G,B,A per pixel with premultiplied alpha (**likely**, platform behaviour, EV-JNI-0064). | EV-JNI-0032 |
| `onPause` / `onResume` / `onStop` / `onRestart` / `onStart` / `onDestroy` | **Confirmed.** See section 10. | EV-JNI-0050 |
| OpenFeint anonymous callbacks | **Confirmed.** Success forwards `CBOFOStatePtr` and failure forwards 0. `ADRenderer$2` (`Achievement.LoadCB`) has **no** `onFailure`, so a failed load never calls native code. | EV-JNI-0038 |

## 10. Managed lifecycle and startup order

*(Written so the lead can merge it into `docs/BOOT_CHAIN.md`.)*

### 10.1 Process start (main thread)

1. `SnailMailApplication.<init>` sets `instance = this` (`SnailMailApplication.smali:19-31`).
2. `SnailMailApplication.onCreate` (`:48-92`) runs
   `OpenFeint.initialize(this, new OpenFeintSettings("Snail Mail", key, secret, id, {RequestedOrientation:0}), new MyOpenFeintDelegate())`.
   * `OpenFeintInternal` constructs a main-thread `Handler` and a `Client`. The `Client` has its own main-thread `Handler` and a 2-4 thread executor (`OpenFeintInternal.smali:160,279`; `request/Client.smali:67`).
   * It posts a `/xp/devices` device-session request and a `login()` runnable. Network work runs on executor threads, and completions are posted back to the main thread (EV-JNI-0041, **likely**).
   * With no saved user, `launchIntroFlow()` is deferred until a device session succeeds, which cannot happen with the defunct servers, so IntroFlow should never appear (EV-JNI-0069, **likely**).
   * The native library is **not** loaded yet.
3. The `SnailMailActivity` class is initialised (`<clinit>`, `SnailMailActivity.smali:31-52`): `AudioInitFlag=false`, `MusicVolume=0`, and **`System.loadLibrary("snailmail")`**.
   * No `JNI_OnLoad` exists, so loading only runs the ELF `.init_array` (11 static constructors, native workstream).
   * Class init could also be triggered earlier by `MyOpenFeintDelegate.userLoggedIn → SnailMailActivity.wprintf`. That is irrelevant today.
4. `SnailMailActivity.<init>` sets `instance = this`.

### 10.2 `SnailMailActivity.onCreate` (main thread, `SnailMailActivity.smali:119-283`)

1. `ActivityInitFlag = true`. This field is write-only (EV-JNI-0062).
2. `wprintf("*** OnCreate")` calls **`JNIDebug()`**, the first native call. It returns 0, so nothing is printed (EV-JNI-0020).
3. `wl = PowerManager.newWakeLock(26, "DoNotDimScreen")`.
4. `super.onCreate`; `requestWindowFeature(FEATURE_NO_TITLE)`; `getWindow().setFlags(FLAG_FULLSCREEN, FLAG_FULLSCREEN)`; `super.onCreate` again.
5. `new ADGLSurfaceView(this)` (`ADGLSurfaceView.smali:11-41`):
   * `new ADRenderer()`
   * **`setRenderer(renderer)`**, which creates and starts the GLThread (**likely**, framework behaviour)
   * `requestFocus()`
   * `setFocusableInTouchMode(true)`
   * Render mode is the default, `RENDERMODE_CONTINUOUSLY`. No EGL config chooser is set, so the default is used.
6. `setContentView(mGLView)`.
7. `AudioInitFlag = true`; `Sp = new SoundPool(8, STREAM_MUSIC, 0)`.
8. `getAssets().openFd("asm.mp3")` then **`JNIDatInit(fd, (int)start, (int)length)`**. This opens the archive, builds the directory hash and caches a dup'ed fd, `gDatFP`, and the offsets. On `IOException` it only logs, and the archive stays closed.
9. `Accelerometer = new AccelerometerListener(this); Accelerometer.start()` registers `SENSOR_DELAY_FASTEST` on the main Looper. It is never unregistered.

### 10.3 Resume (main thread)

* `onStart`: log, then super.
* `onResume`: log, `super.onResume()`, `mGLView.onResume()`, `wl.acquire()`.
* The window is added and laid out, and the SurfaceView surface is created. `surfaceCreated` and `surfaceChanged` are delivered on the main thread and forwarded to the GLThread (**likely**, framework).

### 10.4 GL thread (`ADRenderer`, `ADRenderer.smali:1319-1426`)

* **`onSurfaceCreated`**:
  * `SnailMailActivity.ActivityInitFlag = false`.
  * First time in the process (static `SurfaceCreatedFirstTime == false`): **`nativeInit()`** runs. It calls `JAVA_RegisterFunctions`, which caches the GL thread's `JNIEnv*`, the `ADRenderer` `thiz` and its class as raw locals, and 26 method IDs. Then it runs `cRResourceManager::Init` and sets `G0StartBlackCount=2`. After that, `SurfaceCreatedFirstTime = true` and `AudioInitFlag = false`.
  * Later calls (the EGL context was lost on pause, or a new Activity started in the same process): **`nativeReInit()`** runs. It calls `JAVA_RegisterFunctions` again, then `InitGL`, then `cRResourceManager::ReInit`, which reloads textures through the `JAVAC_Un*` callbacks, and sets `*(Game+0x718b4)=4` and `G0StartBlackCount=2`. If `AudioInitFlag` is set, which happens only after a fresh `onCreate`, it also calls **`JNIAudioInit()`** (a no-op) and clears the flag.
* **`onSurfaceChanged(w,h)`** calls **`nativeResize(w,h)`**. Width and height are stored as floats in `gG0DeviceScreen*` and `gG0Screen*`.
* **`onDrawFrame`** calls **`nativeRender(HasFocus ? 0 : 1)`**, which runs `AppInit()` and then `appRender(flag)`. All 26 native→Java callbacks (audio, files, decoders, time, OpenFeint) are issued from here or from `nativeReInit`, on the GL thread (EV-JNI-0014/0015).
* BOOT_CHAIN asks "[MANAGED?]": can `nativeReInit` run before the first `nativeRender`/`AppInit`?
  * `SurfaceCreatedFirstTime` is a process-wide static that is set only after `nativeInit` returns.
  * GLSurfaceView's `GLThread.guardedRun` calls `onSurfaceCreated`, then `onSurfaceChanged`, then `onDrawFrame` in the same loop iteration.
  * So the first `nativeRender` follows `nativeInit` before any later surface creation can take the `nativeReInit` path.
  * This is **likely**, based on framework behaviour. It is not guaranteed by app code.

### 10.5 Input and sensors (main thread)

* `onTouchEvent` calls `JNIMouseEvent(type,x,y)`.
* `onKeyDown` calls `JNIKey(keycode)`.
* `onSensorChanged` calls `JNIAccelerometer(x,y,z)`.
* `onWindowFocusChanged` sets `HasFocus`.
* These entry points only write engine state. They never call back into Java and make no GL calls (EV-JNI-0014).

### 10.6 Pause, stop, restart, destroy (main thread)

* **`onPause`**: log, then `mGLView.onPause()`, `wl.release()`, **`JNIResourceManagerInvalidate()`** (marks resource records invalid), `MusicPlayer.pause()` if playing, `super.onPause()`.
* **`onStop`**: log, `Sp.release(); Sp = null`, **`JNIDatUnInit()`** (a no-op: the archive stays open, EV-JNI-0019), `super.onStop()`.
* **`onRestart`**: `Sp = new SoundPool(8,3,0)`, log, `super.onRestart()`. `JNIDatInit` is **not** called again, which works only because `JNIDatUnInit` does nothing.
  * Sample IDs held by native code refer to the released pool, and nothing on the Java side reloads them.
  * `nativeReInit` does not reach `JAVALoadSample`, so sound effects may stay silent after a stop/restart cycle unless the engine reloads samples elsewhere (EV-JNI-0052, **hypothesis**).
* **`onDestroy`**: log, then super. No native teardown happens: `nativeDone` and `nativePause` are exported but never invoked (EV-JNI-0048).
* `onWindowFocusChanged(b)`: `HasFocus = b`, a non-volatile static read by the GL thread.

### 10.7 Cross-thread ordering: `JNIDatInit` (main) vs `nativeInit` (GL)

| Question | Answer | Confidence |
|---|---|---|
| Does `JNIDatInit` run before `nativeInit`? | Yes, in practice. `setRenderer` starts the GLThread inside `onCreate` (program order `ADGLSurfaceView.smali:29` < `SnailMailActivity.smali:247`). The GLThread cannot call `onSurfaceCreated` until the SurfaceView's surface exists. The surface is created only after the Activity's window is added and traversed, which happens after `onCreate`/`onStart`/`onResume` return on the same main thread. `JNIDatInit` runs synchronously inside `onCreate`, so it has returned before `surfaceCreated` is delivered. The hand-off goes through the `GLThreadManager` monitor, which gives a happens-before edge for the globals `JNIDatInit` wrote. | **likely**: strong inference from GLSurfaceView/ViewRootImpl behaviour, not observed. Confirm with a device trace (EV-JNI-0051). |
| Is it guaranteed by the app? | No. The app does no explicit synchronisation. It relies on framework sequencing. If `openFd` fails, `nativeInit` runs with `gDatFP == NULL`, and `PfmLoadFileDat` logs `"***ERROR*** gDatFP==0"` and returns without loading. | established (code) |
| Other races | Input, sensor and `JNIResourceManagerInvalidate` (main) write engine globals that the GL thread reads, without locks. `JNIResourceManagerInvalidate` is safe only if `GLSurfaceView.onPause()` has parked the GL thread. Current AOSP waits for the pause acknowledgement, and early 2.x releases might not have (**hypothesis** on version). OpenFeint callbacks (main) write `*(u8*)ptr` and `gOFOSaveFlag` (dormant). | established for the writes, likely for the thread identities |

## 11. JNI contract summary

The full per-method records (descriptor, symbol, v7a/v5 address and size,
arguments, thread, behaviour, lifetime, hazards, evidence) are in
[`docs/JNI_MAP.json`](JNI_MAP.json).

### 11.1 Java → native (19 declared, 18 exported)

| Java method | Descriptor | Kind | v7a | v5 | Caller / thread |
|---|---|---|---|---|---|
| `SnailMailApplication.JNIOFInit` | `()V` | static | MISSING | MISSING | never invoked |
| `SnailMailActivity.JNIDebug` | `()I` | static | 0x13b44/8 | 0x1440c/8 | `wprintf`, any thread |
| `SnailMailActivity.JNIDatInit` | `(Ljava/io/FileDescriptor;II)V` | static | 0x15244/588 | 0x15b30/596 | onCreate / main |
| `SnailMailActivity.JNIDatUnInit` | `()V` | static | 0x13c70/52 | 0x1453c/52 | onStop / main |
| `SnailMailActivity.JNIOFOSave` | `()V` | static | 0x7d71c/4 | 0x85b24/4 | `ADRenderer.JAVAOpenFeintOpen` / GL (re-entrant) |
| `SnailMailActivity.JNIResourceManagerInvalidate` | `()V` | static | 0x142fc/28 | 0x14bd0/28 | onPause / main |
| `ADGLSurfaceView.JNIMouseEvent` | `(IFF)V` | static | 0x14318/276 | 0x14bec/288 | onTouchEvent / main |
| `ADGLSurfaceView.nativePause` | `()V` | static | 0x15500/92 | 0x15df8/92 | never invoked |
| `ADGLSurfaceView.JNIKey` | `(I)V` | instance | 0x13d78/268 | 0x1463c/228 | onKeyDown / main |
| `AccelerometerListener.JNIAccelerometer` | `(FFF)V` | instance | 0x142bc/64 | 0x14b90/64 | onSensorChanged / main |
| `ADRenderer.JNIAudioInit` | `()V` | instance | 0x13ad0/4 | 0x14398/4 | onSurfaceCreated / GL |
| `ADRenderer.nativeDone` | `()V` | instance | 0x1555c/16 | 0x15e54/16 | never invoked |
| `ADRenderer.nativeInit` | `()V` | instance | 0x15650/60 | 0x15f4c/60 | onSurfaceCreated (1st) / GL |
| `ADRenderer.nativeReInit` | `()V` | instance | 0x155f0/96 | 0x15eec/96 | onSurfaceCreated (later) / GL |
| `ADRenderer.nativeRender` | `(I)V` | instance | 0x1549c/36 | 0x15d90/40 | onDrawFrame / GL |
| `ADRenderer.nativeResize` | `(II)V` | instance | 0x1556c/132 | 0x15e64/136 | onSurfaceChanged / GL |
| `ADRenderer.JNIOFOSubmitCB` | `(I)V` | instance | 0x7d44c/72 | 0x8584c/72 | OpenFeint callback / main |
| `ADRenderer.JNIOFOUnlockCB` | `(I)V` | instance | 0x7d404/72 | 0x85804/72 | OpenFeint callback / main |
| `MyOpenFeintDelegate.JNIOFOInit` | `(Ljava/lang/String;)V` | instance | 0x7d81c/32 | 0x85c24/32 | `userLoggedIn` / main |

Symbol names follow `Java_com_sandlotgames_snailmail_<Class>_<method>`. No
overloads exist, so no `__<sig>` suffix is needed.

### 11.2 Native → Java (26 callbacks, all `ADRenderer` public instance methods)

The binding is set up by `JAVA_RegisterFunctions(JNIEnv*, jobject)` (v7a
0x13ca4/212, v5 0x14570/204), which is called from `nativeInit` and
`nativeReInit` (EV-JNI-0008..0011):

```c
gJavaEnv = env; gJavaObj = thiz;                 /* raw, no NewGlobalRef   */
gJavaClass = (*env)->GetObjectClass(env, thiz);  /* raw local ref          */
for (i = 0; i < 26; i++)                          /* gJAVAFunction[27], 12 B */
    gJAVAFunction[i].id = (*gJavaEnv)->GetMethodID(gJavaEnv, gJavaClass,
                                                   gJAVAFunction[i].name, gJAVAFunction[i].sig);
```

Every callback goes through `_JNIEnv::CallVoidMethod` (v7a 0x13e84, which
calls `CallVoidMethodV`) or `_JNIEnv::CallIntMethod` (v7a 0x140e4, which calls
`CallIntMethodV`), with `env = gJavaEnv` and `obj = gJavaObj`.

| # | Java method | Signature | Native wrapper (v7a) | Native work around the call |
|---|---|---|---|---|
| 0 | JAVALoadSample | `(Ljava/lang/String;)I` | `JAVALoadSample(char*)` 0x1495c | NewStringUTF/DeleteLocalRef |
| 1 | JAVAPlaySample | `(IF)I` | `JAVAPlaySample(int,float)` 0x14230 | float passed as double vararg |
| 2 | JAVAStopSample | `(I)V` | `PfmAudioStopSample(int)` 0x14044 | – |
| 3 | JAVASaveFile | `(Ljava/lang/String;[BI)I` | `JAVACSaveFile` 0x14858 | NewByteArray+SetByteArrayRegion, NewStringUTF |
| 4 | JAVADeleteFile | `(Ljava/lang/String;)V` | `JAVACDeleteFile` 0x14eac | NewStringUTF |
| 5 | JAVALoadFile | `(Ljava/lang/String;[BI)V` | `JAVACLoadFile` 0x15038 | size via #7, NewByteArray, GetByteArrayRegion |
| 6 | JAVAFindFile | `(Ljava/lang/String;)I` | `JAVACFindFile` 0x147c8 | NewStringUTF |
| 7 | JAVAFileSize | `(Ljava/lang/String;)I` | `JAVACFileSize` 0x14fb0 | NewStringUTF |
| 8 | JAVASetMusicVolume | `(F)V` | `PfmAudioSetMusicVolume` 0x14088 | float→double vararg |
| 9 | JAVAPlayMusic | `(Ljava/lang/String;)V` | `PfmAudioPlayMusic` 0x14f30 | NewStringUTF |
| 10 | JAVAStopMusic | `()V` | `PfmAudioStopMusic` 0x14004 | – |
| 11 | JAVAUnPauseMusic | `()V` | `PfmAudioUnPauseMusic` 0x13fc4 | – |
| 12 | JAVAPauseMusic | `()V` | `PfmAudioPauseMusic` 0x13f84 | – |
| 13 | JAVAMusicRestart | `()V` | `JAVAMusicRestart` 0x13f44 | – |
| 14 | JAVAUnZip | `([B[B)V` | `JAVAC_UnZip(dst,dstLen,src,srcLen)` 0x14b60 | 2×NewByteArray, Set/GetByteArrayRegion |
| 15 | JAVAUnJpg | `([B[B)V` | `JAVAC_UnJpg(dst,dstLen,src,srcLen,w,h)` 0x149e8 | rows copied bottom-up (flip) |
| 16 | JAVAUnPng | `([B[B)V` | `JAVAC_UnPng(...)` 0x144c0 | rows copied bottom-up (flip) |
| 17 | JAVAOpenFeintOpen | `()V` | `JAVAOpenFeintOpen` 0x13f04 | Java re-enters `JNIOFOSave` |
| 18 | JAVAOpenFeintLastLoggedInUserID | `([B)V` | `…LastLoggedInUserID(char*,int)` 0x146c8 | NewByteArray(64), GetByteArrayRegion |
| 19 | JAVAOpenFeintSubmit | `(Ljava/lang/String;II)V` | `JAVAOpenFeintSubmit(char*,int,int)` 0x1442c | 3rd arg is a native pointer |
| 20 | JAVAOpenFeintUnlock | `(Ljava/lang/String;I)V` | `JAVAOpenFeintUnlock(char*,int)` 0x14638 | 2nd arg is a native pointer |
| 21 | JAVAOpenFeintIsUserLoggedIn | `()I` | 0x141e0 | result normalised to 0/1 |
| 22 | JAVAOpenFeintIsOnline | `()I` | 0x14190 | result normalised to 0/1 |
| 23 | JAVATime | `()I` | `JAVATime()` 0x14118 | combined with #24: `((u64)hi<<32 \| (u32)lo)/1000` gives µs |
| 24 | JAVATimeHi | `()I` | (same) | – |
| 25 | JAVAVibrate | `(I)V` | `JAVAVibrate(int)` 0x13eb8 | `PfmVibrate` passes 300 ms |

The only other JNI use is `JNIDatInit`. It calls `FindClass("java/io/FileDescriptor")`,
then `NewGlobalRef`, then `GetFieldID("descriptor","I")`, then `GetIntField`. It
ends by calling `DeleteLocalRef` on both the local class and the **global** ref
(EV-JNI-0016/0017). `JNIOFOInit` uses `GetStringUTFChars` and never releases it
(EV-JNI-0042).

Across both binaries the complete set of JNIEnv functions used is FindClass,
NewGlobalRef, DeleteLocalRef, GetObjectClass, GetMethodID, CallIntMethodV,
CallVoidMethodV, GetFieldID, GetIntField, NewStringUTF, GetStringUTFChars,
NewByteArray, GetByteArrayRegion and SetByteArrayRegion. The library never
checks for exceptions (EV-JNI-0013). Slot indices were checked against the JNI
specification's `JNINativeInterface` order. The table is rebuilt in code and
spot-checked, for example FindClass=6 at 0x18, GetMethodID=33 at 0x84 and
NewStringUTF=167 at 0x29c. Confidence is high.

## 12. JNI porting hazards for arm64

| ID | Hazard | What the original does | Required handling in the arm64 port | Conf. |
|---|---|---|---|---|
| HZ-01 | Cached `JNIEnv*` and raw local refs | `gJavaEnv`/`gJavaObj`/`gJavaClass` are saved in `nativeInit`/`nativeReInit` and reused by every later callback in other native frames. This worked on pre-ICS Dalvik, where local refs were direct pointers. It is invalid under indirect-reference runtimes. | Keep the renderer in a `NewGlobalRef`, cache `jclass` as a global ref, and get `JNIEnv` per call via `JavaVM::GetEnv`, capturing the `JavaVM` in `JNI_OnLoad`. Callbacks are GL-thread only today; keep them there or attach the thread. | established |
| HZ-02 | Native pointer carried in a Java `int` | `JAVAOpenFeintSubmit(…, int CBOFOStatePtr)` / `JAVAOpenFeintUnlock(…, int)` receive `&gOFOData[…]`. `JNIOFOSubmitCB(int)`/`JNIOFOUnlockCB(int)` store `2` through it on the **main** thread. A 64-bit pointer truncates. | Change the descriptor to `J` (jlong), or pass an index into `gOFOData`, or, recommended, stub OpenFeint as not-logged-in so the path never runs. It is dormant already (EV-JNI-0040). | established |
| HZ-03 | `FileDescriptor` → int fd | `JNIDatInit` reads the private field `FileDescriptor.descriptor` via JNI and `dup()`s it. | Use `AAssetManager_open` + `AAsset_openFileDescriptor64` (or `ParcelFileDescriptor.getFd`). No reflection on non-SDK fields. | established |
| HZ-04 | `AssetFileDescriptor` offsets truncated to int | `(int)getStartOffset()` / `(int)getLength()` are stored in 32-bit globals and used as the fseek base. | Use `off64_t`/`jlong` and `pread`/`fseeko`. | established |
| HZ-05 | 64-bit time split in two | `System.nanoTime` returns two `jint`s through a Java static, recombined as `u64/1000` (µs) by `__aeabi_uldivmod`. Callers use both halves. | Replace with `clock_gettime(CLOCK_MONOTONIC)` returning `uint64_t` µs natively. No JNI needed. | established |
| HZ-06 | `byte[]` buffers sized by native | `JAVAUnZip`/`UnJpg`/`UnPng`/`LoadFile`/`LastLoggedInUserID` fill arrays whose size native chose. Overflow is swallowed by `UnZip` and truncated silently. For `UnPng`/`UnJpg`/`UserID` the exception is left pending and never checked. | Decode natively with explicit lengths (zlib, libpng/libjpeg or stb). If JNI is kept, check `ExceptionCheck` after every call. | established |
| HZ-07 | Pixel contract of the image callbacks | ARGB_8888 `copyPixelsToBuffer` gives RGBA byte order with **premultiplied** alpha. Rows are copied **bottom-up** behind an 18-byte TGA header (type 2, 32 bpp, descriptor 8). | A native decoder must reproduce RGBA order, premultiplication and the row flip. Validate against a device capture. | likely |
| HZ-08 | 32-bit pointers inside data structures | Each archive directory record's `name_offset` (u32 at archive offset 4+24*i) is rewritten in place to an absolute pointer (`JNIDatInit`). | Keep offsets or build a separate `char*` table. Coordinate with the native and asset workstreams. | established |
| HZ-09 | Unsynchronised cross-thread state | Touch, key, sensor and pause-invalidate (main) and OpenFeint callbacks (main) write engine globals that the GL thread reads. The Java statics `HasFocus`/`AudioInitFlag` are non-volatile. | Marshal input to the GL thread (`queueEvent` or a lock-free queue). Use atomics for flags. | established |
| HZ-10 | JNI misuse | `DeleteLocalRef(globalRef)` in `JNIDatInit`; `GetStringUTFChars` without release in `JNIOFOInit`; no exception checks; stale local refs. | Fix, and validate with `-Xcheck:jni`. | established |
| HZ-11 | softfp float passing | armeabi JNI passes `float` in core registers and on the stack (`JNIMouseEvent` x in r3, y on the stack), and varargs `Call*Method` promote float to double. | Nothing to do for C prototypes on AArch64. Relevant only for hand-written glue or emulation harnesses. | established |
| HZ-12 | Lifecycle quirks the port must decide on | `JNIDatUnInit` is a no-op and `onRestart` never re-inits. `SoundPool` is recreated in `onRestart` without reloading samples. The accelerometer is never unregistered. BACK is swallowed by the view. `super.onCreate` is called twice. | Reproduce the observable behaviour intentionally, or document the deviation. Do not "fix" `JNIDatUnInit` without also re-initialising. | established (code) / hypothesis (audio effect) |
| HZ-13 | Dead JNI surface | `JNIOFInit` has no implementation. `nativePause` and `nativeDone` are never called. `JNIAudioInit` and `nativeDone` are no-ops. `JNIDebug` returns 0. | Omit, or implement as no-ops. Keep `JNIDebug` returning 0 to preserve silent logging. | established |

## 13. Open questions

1. Confirm on a device (for example a 32-bit emulator image with `-Xcheck:jni`)
   the thread IDs of the JNI entries and the `JNIDatInit` → `nativeInit`
   ordering. This would raise EV-JNI-0041/0051 to established.
2. Whether sound effects survive `onStop` → `onRestart`, that is, whether the
   engine reloads samples after the `SoundPool` is recreated (EV-JNI-0052).
3. What the 5 indirect call sites under `nativeRender` dispatch to (cRGame::AI
   virtual calls). They are presumed to be the roots of the `JAVAVibrate`,
   `JAVAOpenFeintOpen`, `JAVAPlayMusic`, `JAVAStopMusic`, `JAVACDeleteFile` and
   `…LastLoggedInUserID` calls. Native workstream.
4. The meaning of `*(Game+0xbf0)==2` (key gate), `*(Game+0x718fc)==2` (drag
   gate), and whether `JNIKey`'s 57/28/14 are DirectInput scancodes. Native
   workstream.
5. Whether the engine's blend state expects premultiplied textures (HZ-07). This
   decides whether a native decoder must premultiply.
6. The `asm.mp3` archive layout is covered in `docs/ASSET_FORMATS.md`: 734
   records of 24 bytes starting at offset 4, and `dir_size` = 34,701.

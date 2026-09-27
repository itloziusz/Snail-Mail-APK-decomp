#!/usr/bin/env python3
"""Write analysis/evidence/jni.jsonl (EV-JNI-xxxx), one claim per line.

Claims are hand-curated from the smali/dexdump/disassembly review; locations
cite smali file:line, DEX offsets, or v7a:/v5: module-relative addresses as
required by docs/CONVENTIONS.md.  Usage (repo root): tools/inventory/build_jni_evidence.py
"""
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..") + os.sep

V7 = "e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466"
V5 = "96dbeaeb20c60d687301ca769656727467371489db5e3ed744a93248bc8d8136"
APK = "0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7"
DEX = "b430e061e63dba6860b8d2bf11d6840556a737bc816c82b59f7dd111ad2f7459"
S = "work/apktool/smali/com/sandlotgames/snailmail/"
O = "work/apktool/smali/com/openfeint/internal/"

E = []


def ev(claim, sha, loc, obs, interp, conf="established", contra="", nxt=""):
    E.append({"id": "EV-JNI-%04d" % (len(E) + 1), "claim": claim, "binary_sha256": sha,
              "location": loc, "observation": obs, "interpretation": interp,
              "confidence": conf, "contradictions": contra, "next_step": nxt})


# 0001
ev("classes.dex defines 353 classes: 18 game shell, 252 OpenFeint, 67 Jackson, 10 commons-codec, 6 google-api-client escape.",
   DEX, "classes.dex header class_defs_size; analysis/dex/managed_shell_scan.json classes_per_group",
   "dexdump header class_defs_size=353; per-package counts from dexdump class descriptors.",
   "Game-specific managed code is 9 real classes + 9 R classes; everything else is third-party middleware.")
# 0002
ev("Exactly 19 methods in the DEX carry ACC_NATIVE, all in package com.sandlotgames.snailmail.",
   DEX, "dexdump access flags; analysis/dex/managed_shell_scan.json native_methods",
   "19 methods with access containing NATIVE; smali `.method ... native` declarations match 1:1.",
   "No native methods in OpenFeint/Jackson/codec code; the JNI surface is the game shell only.")
# 0003
ev("The only native-library load in the DEX is System.loadLibrary(\"snailmail\") in SnailMailActivity.<clinit>; no System.load, Runtime.load*, DexClassLoader, PathClassLoader, DexFile, Class.forName, ClassLoader.loadClass anywhere.",
   DEX, S + "SnailMailActivity.smali:46-48",
   "Pattern scan of all 353 smali files (managed_shell_scan.json sensitive_api_references) finds 4 hits: loadLibrary (SnailMailActivity:48), Runtime.exec (OpenFeint Util:803), Class.getMethod + Method.invoke (OpenFeint WebNav$ActionHandler:701,713).",
   "No dynamic code loading; libsnailmail.so is loaded when SnailMailActivity is first initialised.")
# 0004
ev("OpenFeint WebNav$ActionHandler dispatches web-UI actions by reflection on its own class (getMethod(actionName, Map.class).invoke(this, options)).",
   DEX, O + "ui/WebNav$ActionHandler.smali:701,713",
   "invoke-virtual getClass()->getMethod(String, Class[]) then Method.invoke, guarded by mActionList.contains(actionName).",
   "Reflection for UI dispatch only, not code loading.")
# 0005
ev("OpenFeint Util.createSymbolic runs Runtime.exec(\"ln -s <dst> <src>\") (called from Util.moveWebCache).",
   DEX, O + "Util.smali:202-233,790-803,744",
   "StringBuilder \"ln -s \" + dst + \" \" + src passed to Util.run -> Runtime.getRuntime().exec(cmd).",
   "Shell process spawn for web-cache symlinks; irrelevant to the port (OpenFeint to be stubbed).")
# 0006
ev("18 of the 19 declared natives are exported as Java_* symbols in both libraries; SnailMailApplication.JNIOFInit has no export in v7a or v5 and no invoke site in the DEX.",
   V7, "v7a/v5 .dynsym; analysis/dex/jni_native_scan.json declared_natives_without_export; " + S + "SnailMailApplication.smali:33",
   "Java_com_sandlotgames_snailmail_SnailMailApplication_JNIOFInit absent from both .dynsym tables; managed scan lists zero invoke sites.",
   "Harmless dead declaration (UnsatisfiedLinkError only if called). v5 sha " + V5 + " identical result.")
# 0007
ev("Native methods are bound by symbol-name lookup only: no JNI_OnLoad export and no RegisterNatives/GetJavaVM call in either binary.",
   V7, "v7a/v5 .dynsym; jni_native_scan.json jnienv_call_sites",
   "No JNI_OnLoad symbol; tracked JNIEnv call sites never use offsets 0x35c (RegisterNatives) or 0x36c (GetJavaVM).",
   "No JavaVM* is ever obtained, so native code cannot attach threads or re-fetch env.")
# 0008
ev("JAVA_RegisterFunctions stores the raw JNIEnv* and raw thiz local ref into gJavaEnv/gJavaObj and GetObjectClass(thiz) into gJavaClass, with no NewGlobalRef.",
   V7, "v7a:0x13ce8 (str env->gJavaEnv), 0x13cec (str thiz->gJavaObj), 0x13cf4 (ldr pc,[env,#0x7c] GetObjectClass), 0x13d10 (str -> gJavaClass); v5:0x14570",
   "GOT slots 0x400/0x2d4/0x1d0 resolve to gJavaEnv 0x91038, gJavaObj 0x9103c, gJavaClass 0x91034 (.bss); only NewGlobalRef site in the binary is in JNIDatInit.",
   "Cached local references outlive their native frame (JNI spec violation); cached env is thread-bound.")
# 0009
ev("JAVA_RegisterFunctions calls GetMethodID for 26 records of gJAVAFunction ({jmethodID, name, sig}, 12 bytes each) using gJavaClass; table is 324 bytes with an empty 27th sentinel.",
   V7, "v7a:0x13d18-0x13d48 (loop, ldr pc,[r12,#0x84] at 0x13d38); gJAVAFunction v7a:0x8b3f0 size 324; v5:0x946e8",
   "Loop pointer runs table+8 .. table+320 step 12; name/sig pointers are R_ARM_RELATIVE relocated .rodata strings.",
   "Method IDs are re-fetched on every nativeInit/nativeReInit.")
# 0010
ev("All 26 gJAVAFunction name/signature pairs match exactly one public instance method of ADRenderer.",
   V7, "jni_native_scan.json gJAVAFunction.entries[].managed_matches; " + S + "ADRenderer.smali:64-1285",
   "Cross-check of table strings against dexdump method list: 26/26 unique matches in both binaries.",
   "gJavaObj must be the ADRenderer instance (thiz of nativeInit/nativeReInit).")
# 0011
ev("JAVA_RegisterFunctions is called only by nativeInit and nativeReInit.",
   V7, "v7a:0x15658 (bl in nativeInit), v7a:0x155f8 (bl in nativeReInit); v5 same callers",
   "Direct call graph reverse edges of JAVA_RegisterFunctions.",
   "Env/obj cache is refreshed on every onSurfaceCreated on the GL thread.")
# 0012
ev("Every Java callback goes through _JNIEnv::CallVoidMethod (CallVoidMethodV, slot 0xf8) or _JNIEnv::CallIntMethod (CallIntMethodV, slot 0xc8) with env=*gJavaEnv and obj=*gJavaObj.",
   V7, "helpers v7a:0x13e84 (ldr pc,[r12,#0xf8]) and v7a:0x140e4 (ldr pc,[r12,#0xc8]); 26 helper call sites listed in jni_native_scan.json",
   "Register tracker reports env_source env/gJavaEnv and obj_source glob/gJavaObj at all 26 helper call sites in v7a and v5.",
   "Single chokepoint for native->Java; easy to re-implement.")
# 0013
ev("The complete set of JNIEnv functions used by the library is: FindClass, NewGlobalRef, DeleteLocalRef, GetObjectClass, GetMethodID, CallIntMethodV, CallVoidMethodV, GetFieldID, GetIntField, NewStringUTF, GetStringUTFChars, NewByteArray, GetByteArrayRegion, SetByteArrayRegion.",
   V7, "jni_native_scan.json jnienv_call_sites (v7a 56 sites, v5 56 sites; plus 26 helper call sites each)",
   "No ExceptionCheck/ExceptionOccurred/ExceptionClear, no ReleaseStringUTFChars, no GetStaticMethodID, no DeleteGlobalRef. Completeness cross-check: every other `ldr pc,[..]`/`blx rN`/`bx rN` in both full disassemblies is a PLT stub, an EHABI unwinder/libgcc helper, the cRHash::Search callback, or a C++ virtual call `ldr pc,[r3]` in engine code.",
   "Pending Java exceptions are never detected by native code.")
# 0014
ev("UI/main-thread JNI entries (JNIKey, JNIMouseEvent, JNIAccelerometer, JNIResourceManagerInvalidate, JNIDatInit, JNIDatUnInit, JNIOFOSubmitCB, JNIOFOUnlockCB, JNIOFOInit) cannot reach any gJavaEnv user or GL import.",
   V7, "jni_native_scan.json java_exports[].reaches_cached_env_users / reaches_gl_imports / reachable_indirect_call_sites",
   "Their direct-call closures (1-13 functions) contain zero indirect call sites and no gJavaEnv loads or gl* PLT calls, in v7a and v5.",
   "No cross-thread use of the cached JNIEnv; cross-thread interaction is limited to plain memory writes.")
# 0015
ev("nativeRender reaches 17 JAVA* wrappers and all GL imports; nativeReInit reaches the texture-load wrappers (JAVAC_UnPng/UnJpg/UnZip, FileSize/FindFile/LoadFile); JNIOFOSave reaches JAVACSaveFile.",
   V7, "jni_native_scan.json java_exports (nativeRender 433 functions, 5 indirect sites; nativeReInit 38; JNIOFOSave 6)",
   "Remaining wrappers (Vibrate, OpenFeintOpen, StopMusic, PlayMusic, DeleteFile, LastLoggedInUserID, IsOnline) are reached from address-taken engine AI methods (e.g. cRPlayer::AI) that the game loop calls indirectly.",
   "All native->Java callbacks execute on the GL thread.", "likely", "",
   "Resolve the 5 indirect call sites under nativeRender to confirm the AI dispatch.")
# 0016
ev("JNIDatInit: FindClass(\"java/io/FileDescriptor\"), NewGlobalRef, GetFieldID(\"descriptor\",\"I\"), GetIntField(fd), dup(), fdopen(\"rb\") -> gDatFP; stores start->gJavaAssetStart, length->gJavaAssetLength, dup fd->gJavaAssetFid.",
   V7, "v7a:0x15270,0x1528c,0x152b4,0x152ec (JNI), 0x152f0 bl dup@plt, 0x1530c/0x15318/0x15320 stores, 0x15324 bl fdopen@plt, 0x15334 str gDatFP; v5:0x15b5c-0x15bd8",
   "String args resolved by tracker: r1=\"java/io/FileDescriptor\", r2=\"descriptor\", r3=\"I\"; mode string at v7a:0x80e58 = \"rb\".",
   "Archive is read through a private dup'ed descriptor with stdio.")
# 0017
ev("JNIDatInit releases the NewGlobalRef result with DeleteLocalRef.",
   V7, "v7a:0x1528c (NewGlobalRef -> [sp,#4]), v7a:0x15430-0x1543c (ldr r1,[sp,#4]; DeleteLocalRef); v5:0x15d30",
   "Second DeleteLocalRef argument is the value returned by NewGlobalRef.",
   "JNI misuse: the global ref leaks and CheckJNI-enabled runtimes may report an error.",
   "established", "", "Observe under -Xcheck:jni on a 32-bit device/emulator if available.")
# 0018
ev("JNIDatInit reads a 244-byte probe at the asset start, allocates the directory (size = probe u32 at +8), re-reads it from the start, then rewrites the u32 at archive offset 4+24*i (record i's name_offset in docs/ASSET_FORMATS.md) into an absolute 32-bit pointer before hashing it; count = u32 at offset 0.",
   V7, "v7a:0x1534c-0x15414",
   "fseek(start); fread(sp+12,1,244); RShellMemoryMalloc([sp+0x14]); fseek(start); fread(gDat,1,size); loop p=gDat+24*i: [p+4]+=gDat; cRHash::Add(&gDatHash,[p+4],i) while i < *(int*)gDat.",
   "Directory relocation assumes 32-bit pointers (arm64 hazard). Consistent with docs/ASSET_FORMATS.md section 2 (asset workstream owns the format).")
# 0019
ev("JNIDatUnInit only calls wprintf twice, and native wprintf is a no-op; the archive is never closed.",
   V7, "v7a:0x13c70-0x13c94; wprintf v7a:0x19948 (push {r0-r3}; add sp,#16; bx lr)",
   "No fclose/close/free in JNIDatUnInit.", "onStop->onRestart keeps working without re-init.")
# 0020
ev("JNIDebug returns constant 0 in both builds, so SnailMailActivity.wprintf never prints.",
   V7, "v7a:0x13b44 (mov r0,#0; bx lr); v5:0x1440c; " + S + "SnailMailActivity.smali:93-115",
   "wprintf prints only if JNIDebug()==1.", "Managed debug logging permanently off.")
# 0021
ev("SnailMailActivity.onCreate order: ActivityInitFlag=true; wprintf x2; newWakeLock(26,\"DoNotDimScreen\"); super.onCreate; requestWindowFeature(1); getWindow().setFlags(1024,1024); super.onCreate (again); new ADGLSurfaceView(this); setContentView; AudioInitFlag=true; new SoundPool(8,3,0); openFd(\"asm.mp3\") -> JNIDatInit; new AccelerometerListener(this).start().",
   DEX, S + "SnailMailActivity.smali:119-283",
   "Instruction order in smali; 0x1a=26=PowerManager.FULL_WAKE_LOCK (deprecated since API 17), feature 1=FEATURE_NO_TITLE, 0x400=FLAG_FULLSCREEN, SoundPool(maxStreams 8, STREAM_MUSIC 3, srcQuality 0).",
   "Matches JADX output.")
# 0022
ev("super.onCreate(savedInstanceState) is invoked twice in onCreate (before and after requestWindowFeature/setFlags).",
   DEX, S + "SnailMailActivity.smali:163,176", "Two invoke-super Activity.onCreate instructions.",
   "Original-code quirk confirmed (not a JADX artefact).")
# 0023
ev("asm.mp3 start offset and length are narrowed with long-to-int before JNIDatInit; the AssetFileDescriptor is never closed; IOException is caught and only logged.",
   DEX, S + "SnailMailActivity.smali:219-249,271-282",
   "getStartOffset()J -> long-to-int v5; getLength()J -> long-to-int v3; no close() on v0.",
   "Works because asm.mp3 is stored uncompressed (inventory compress=stored; .mp3 is in aapt's no-compress list).")
# 0024
ev("ADGLSurfaceView.onTouchEvent maps ACTION_DOWN(0)->JNIMouseEvent(0), ACTION_UP(1)->JNIMouseEvent(2), ACTION_MOVE(2)->JNIMouseEvent(1); all other actions do nothing; always returns true.",
   DEX, S + "ADGLSurfaceView.smali:303-394; classes.dex packed-switch payload at file offset 0x0266dc",
   "Payload ident 0x0100 size 3 first_key 0 targets +4,+0x1d,+0x11 from 0x0005 -> 0x09 (const 0), 0x22 (const 2), 0x16 (v4=1).",
   "Confirms JADX despite its 'incorrect switch cases order' warning.")
# 0025
ev("Native JNIMouseEvent: returns unless gGameValid; X=x*640/gG0DeviceScreenWidth, Y=y*480/gG0DeviceScreenHeight; type 0 -> ClickiPhone(true,X,Y)+ClickOn; type 1 -> ClickiPhone only when *(Game+0x718fc)==2, then ClickOn; otherwise ClickOff.",
   V7, "v7a:0x14318-0x1440c (consts 0x44200000=640.0, 0x43f00000=480.0); v5:0x14bec-0x14cf0 (same constants via __mulsf3/__divsf3)",
   "x arrives in r3 and y on the stack (softfp).", "Input is normalised to a 640x480 virtual screen.")
# 0026
ev("onKeyDown: BACK with repeatCount 0 returns true without super; otherwise (getAction()==ACTION_DOWN) keycodes 29-54, 7-16, 62, 67, 66 call JNIKey (66 also hides the IME); super.onKeyDown's result is returned for every non-BACK path.",
   DEX, S + "ADGLSurfaceView.smali:54-301",
   "packed-switch with the single key 0; all branches fall to :goto_1 invoke-super.",
   "BACK key is swallowed by the view (likely prevents exiting via BACK); DPAD keys are only logged.")
# 0027
ev("Native JNIKey converts A-Z via cKeyPad::ConvertCode((char)(keycode+68)), 0-9 via ConvertCode((char)(keycode+41)), SPACE->57, ENTER->28, DEL->14 and calls KeySet only when *(int*)(Game+0xbf0)==2.",
   V7, "v7a:0x13d78-0x13e74; v5:0x1463c",
   "Other keycodes reach KeySet with uninitialised r4 (not reachable from Java).",
   "57/28/14 equal DirectInput DIK_SPACE/DIK_RETURN/DIK_BACK (intent is a hypothesis).", "established", "",
   "v5 mapping not re-derived by hand.")
# 0028
ev("AccelerometerListener: x*x+y*y+z*z in float, sqrt in double, n==0 -> 1.0, divisions in double narrowed to float, low-pass last+=(cur-last)*0.3f in float, JNIAccelerometer(-last_x,-last_y,last_z); registerListener(listener, first TYPE_ACCELEROMETER sensor, rate 0 = SENSOR_DELAY_FASTEST); stop() is never invoked.",
   DEX, S + "AccelerometerListener.smali:147-342 (math), :344-367 (registerListener rate 0), :369-380 (stop, no callers)",
   "mul-float/add-float then float-to-double; div-double; double-to-float; const 0x3e99999a; neg-float on x,y only.",
   "Matches JADX. Listener leaks across Activity recreation.")
# 0029
ev("Native JNIAccelerometer forwards (x,y,z) to cAccelerometer::Input(Game+0xbd4) when Game != NULL.",
   V7, "v7a:0x142bc-0x142f0", "z taken from the stack (softfp).", "Pure state write.")
# 0030
ev("ADRenderer.JAVATime stores System.nanoTime() in static long JTime and returns (int)JTime; JAVATimeHi returns (int)(JTime >> 32) using shr-long by 32 then long-to-int.",
   DEX, S + "ADRenderer.smali:982-1015; dexdump 0x026eec, 0x026f10",
   "sput-wide/sget-wide JTime; const/16 v2,0x20; shr-long/2addr; long-to-int.",
   "Arithmetic shift; low word is the raw low 32 bits.")
# 0031
ev("Native JAVATime() calls JAVATime then JAVATimeHi and returns ((uint64)hi<<32 | (uint32)lo)/1000 via __aeabi_uldivmod (microseconds); GetTime() tail-calls it and callers consume both r0 and r1.",
   V7, "v7a:0x14118-0x1417c; GetTime v7a:0x7d110; caller appRender v7a:0x156c0-0x156d8; v5:0x149cc-0x14a30",
   "orr r0,r6(=0),r4(lo); mov r1,r7(hi); r2=1000,r3=0; bl __aeabi_uldivmod.", "Engine time base is uint64 microseconds of CLOCK_MONOTONIC-derived nanoTime.")
# 0032
ev("JAVAUnPng/JAVAUnJpg decode with BitmapFactory.decodeByteArray(src, 0, src.length, opts{inDither=true, inPreferredConfig=ARGB_8888}) and copy with Bitmap.copyPixelsToBuffer(ByteBuffer.wrap(dst)).",
   DEX, S + "ADRenderer.smali:1017-1070,1091-1144", "No null check on the decoded bitmap.",
   "Output is the raw ARGB_8888 pixel memory (RGBA byte order).")
# 0033
ev("Native JAVAC_UnPng/UnJpg allocate dst/src byte arrays, upload src with SetByteArrayRegion, call the Java decoder, then copy row i of the Java buffer to dst+(h-1-i)*(dstLen/h) with GetByteArrayRegion (vertical flip); the width argument is unused.",
   V7, "v7a:0x144c0-0x14624 (UnPng), 0x149e8-0x14b4c (UnJpg); v5:0x14da4, 0x152d0",
   "rowBytes = __divsi3(dstLen, h); loop offsets i*rowBytes and dst + (h-1)*rowBytes - i*rowBytes.",
   "Pixel rows are stored bottom-up.")
# 0034
ev("PfmLoadFileDat dispatches on a type argument (0 raw read, 1 JAVAC_UnZip, 2 JAVAC_UnJpg, 3 JAVAC_UnPng) and for images writes an 18-byte TGA header (image type 2, 32 bpp, descriptor 8, width/height) before the pixels.",
   V7, "v7a:0x14cb4-0x14ccc (jump table), 0x14d28-0x14d74 (header stores), fseek offset base gJavaAssetStart 0x14cd8-0x14cf8",
   "strb 2 @+2, strh w @+12, strh h @+14, strb 32 @+16, strb 8 @+17; decode target dst+18.",
   "Decoded images are presented to the engine as bottom-up 32-bit TGA.")
# 0035
ev("JAVAUnZip inflates only the first ZIP entry, copies bytewise into Buffer until read()==-1, swallows all exceptions (including ArrayIndexOutOfBounds) and does not close the stream on error.",
   DEX, S + "ADRenderer.smali:1146-1283", "Single getNextEntry(); do/while(read != -1); catch Exception -> wprintf.",
   "Oversized entries are silently truncated.")
# 0036
ev("Native JAVAC_UnZip(dst,dstLen,src,srcLen): NewByteArray(dstLen), NewByteArray(srcLen), SetByteArrayRegion(src), CallVoidMethod(JAVAUnZip,dstArr,srcArr), GetByteArrayRegion(dstArr,0,dstLen,dst), DeleteLocalRef x2.",
   V7, "v7a:0x14b60-0x14c60; v5:0x1544c", "", "Output length is whatever native requested.")
# 0037
ev("Native passes pointers into gOFOData as the int CBOFOStatePtr: OFAddArcade passes gOFOData+360*cur+0x10c; OFAddAchievement passes gOFOData+360*cur+0x144+idx; the byte is set to 1 before submission.",
   V7, "v7a:0x7df30-0x7dfd0 (OFAddArcade), v7a:0x7dbe8-0x7dc74 (OFAddAchievement)",
   "add r2,r5,#268 / add r1,r7,#324 then tail-call JAVAOpenFeintSubmit/Unlock; strb 1 before.",
   "A 32-bit address round-trips through Java int.")
# 0038
ev("Java forwards CBOFOStatePtr unchanged: SubmitToCB.onSuccess -> JNIOFOSubmitCB(ptr), onFailure -> JNIOFOSubmitCB(0); UnlockCB.onSuccess -> JNIOFOUnlockCB(ptr), onFailure -> (0); LoadCB.onSuccess -> unlock or JNIOFOUnlockCB(ptr) if already unlocked; LoadCB has no onFailure override (APICallback.onFailure is empty).",
   DEX, S + "ADRenderer$1.smali:41-74; ADRenderer$2.smali:55-102; ADRenderer$2$1.smali:41-82; " + O + "APICallback.smali:19-25",
   "", "Achievement load failure never reaches native code.")
# 0039
ev("JNIOFOSubmitCB/JNIOFOUnlockCB: if ptr != 0 store byte 2 at ptr; always set gOFOSaveFlag=1.",
   V7, "v7a:0x7d404-0x7d43c, 0x7d44c-0x7d484; v5:0x85804, 0x8584c-0x85884",
   "cmp r5,#0; movne r3,#2; strbne r3,[r5]; strb 1 -> GOT 0x258 (gOFOSaveFlag).", "")
# 0040
ev("Native only submits scores/unlocks when OFIsUserLoggedIn() (JAVAOpenFeintIsUserLoggedIn) returns true; otherwise it just sets gOFOSaveFlag.",
   V7, "v7a:0x7df94 (OFAddArcade), 0x7dc38 (OFAddAchievement), 0x7d84c (OFOUpdate)",
   "bl OFIsUserLoggedIn; cmp r0,#0; bne submit path.",
   "With OpenFeint servers gone no user can log in, so the pointer-callback path is dormant.")
# 0041
ev("OpenFeint request completions run on the Handler created in the Client and OpenFeintInternal constructors, which run inside OpenFeint.initialize from SnailMailApplication.onCreate (main thread).",
   DEX, O + "request/Client.smali:67-69; request/Client$5.smali:71,109; request/Client$3.smali:51; OpenFeintInternal.smali:160-162,279; " + S + "SnailMailApplication.smali:88",
   "Executor thread runs the HTTP request then posts onResponse to mMainThreadHandler.",
   "JNIOFOSubmitCB/UnlockCB/JNIOFOInit run on the main thread.", "likely", "",
   "Rare synchronous failure paths (OpenFeintInternal null, null resourceID, cached unlock) run on the caller's thread.")
# 0042
ev("JNIOFOInit calls GetStringUTFChars(userID, NULL) and never releases it, then OFONewUser strcpy's into gOFUser and gConfig+240 and sets gOFOLoadFlag.",
   V7, "v7a:0x7d81c-0x7d838 (ldr pc,[r3,#0x2a4]); OFONewUser v7a:0x7d7b0; v5:0x85c24",
   "No ReleaseStringUTFChars (slot 0x2a8) anywhere in the binary.", "UTF chars leak per login; unbounded copy.")
# 0043
ev("JAVAOpenFeintLastLoggedInUserID receives a 64-byte array from native and writes userID bytes plus a NUL without a bounds check; native copies back with GetByteArrayRegion and falls back to gConfig+240 when empty.",
   V7, "callers v7a:0x7e3e0,0x7e5e4 (mov r1,#64); wrapper v7a:0x146c8-0x147ac; " + S + "ADRenderer.smali:411-473",
   "", "Overflow would leave an ArrayIndexOutOfBoundsException pending in native code.")
# 0044
ev("onSurfaceCreated calls nativeInit the first time in the process (static SurfaceCreatedFirstTime) and nativeReInit afterwards, plus JNIAudioInit when AudioInitFlag is set (only onCreate sets it).",
   DEX, S + "ADRenderer.smali:1363-1426; SnailMailActivity.smali:191",
   "SurfaceCreatedFirstTime set true after nativeInit; AudioInitFlag cleared after first init and after JNIAudioInit.",
   "A new Activity instance in an old process goes straight to nativeReInit.")
# 0045
ev("nativeInit = JAVA_RegisterFunctions + cRResourceManager::Init(&gResourceManager) + G0StartBlackCount=2; nativeReInit = JAVA_RegisterFunctions + InitGL + cRResourceManager::ReInit + *(Game+0x718b4)=4 + G0StartBlackCount=2.",
   V7, "v7a:0x15650-0x1567c; v7a:0x155f0-0x1563c; v5:0x15f4c, 0x15eec", "", "")
# 0046
ev("nativeRender(flag) runs AppInit() every frame and, if it returns non-zero, appRender(flag != 0); onDrawFrame passes 1 when SnailMailActivity.HasFocus is false.",
   V7, "v7a:0x1549c-0x154bc; " + S + "ADRenderer.smali:1319-1347", "", "")
# 0047
ev("nativeResize(w,h) stores (float)w into gG0DeviceScreenWidth and gG0ScreenWidth and (float)h into gG0DeviceScreenHeight and gG0ScreenHeight.",
   V7, "v7a:0x1556c-0x155d4", "GOT 0x438/0x364/0x2b8/0x3b8.", "No GL calls in the resize entry itself.")
# 0048
ev("JNIAudioInit is bx lr; nativeDone calls appDeinit and importGLDeinit which are both bx lr; nativePause and nativeDone have no invoke site in the DEX.",
   V7, "v7a:0x13ad0, 0x1555c, 0x1568c, 0x15498; managed_shell_scan.json invoke_sites", "", "Dead or no-op JNI surface.")
# 0049
ev("JNIResourceManagerInvalidate -> cRResourceManager::Invalidate sets gLoadingBar byte +0x35 to 1 and field +8 of each 140-byte resource record to 1; no GL or JNI calls.",
   V7, "v7a:0x142fc-0x1430c; v7a:0x7e8f0-0x7e92c", "", "Marks GL resources for reload after the context is lost.")
# 0050
ev("Lifecycle bodies: onPause = mGLView.onPause, wl.release, JNIResourceManagerInvalidate, MusicPlayer.pause if playing, super.onPause; onResume = super, mGLView.onResume, wl.acquire; onStop = Sp.release, Sp=null, JNIDatUnInit, super; onRestart = new SoundPool(8,3,0), super; onStart/onDestroy = log + super.",
   DEX, S + "SnailMailActivity.smali:285-448", "", "Matches JADX.")
# 0051
ev("The GL thread is created by GLSurfaceView.setRenderer inside the ADGLSurfaceView constructor, which runs in onCreate before JNIDatInit; Renderer callbacks only start after the window surface exists (after onResume).",
   DEX, S + "ADGLSurfaceView.smali:17-29; SnailMailActivity.smali:179-247",
   "setRenderer precedes openFd/JNIDatInit in program order on the UI thread.",
   "JNIDatInit returns before surfaceCreated can be delivered, so nativeInit happens after it.", "likely", "",
   "Relies on documented GLSurfaceView/ViewRootImpl behaviour; confirm with a device trace.")
# 0052
ev("onRestart creates a new SoundPool while sample IDs obtained from the released pool remain in native state; no Java-side reload exists and nativeReInit does not reach JAVALoadSample.",
   DEX, S + "SnailMailActivity.smali:350-377,421-448; jni_native_scan.json nativeReInit reach",
   "", "Sound effects may be silent after stop/restart unless the engine reloads samples.", "hypothesis", "",
   "Check cRSound/RShell sample cache for reload logic; test on device.")
# 0053
ev("JAVALoadSample and JAVAPlayMusic open an AssetFileDescriptor per call and never close it.",
   DEX, S + "ADRenderer.smali:296-318,672-699", "", "fd leak per sample/music load.")
# 0054
ev("Manifest: package com.sandlotgames.snailmail, versionCode 1, versionName 1.00, installLocation auto, minSdkVersion 6, no targetSdkVersion, application SnailMailApplication, launcher SnailMailActivity (screenOrientation landscape, configChanges keyboardHidden|orientation 0xa0), 4 OpenFeint activities, 6 permissions, glEsVersion 0x10001, accelerometer+touchscreen required, supports-screens small/normal/large/anyDensity.",
   APK, "AndroidManifest.xml (aapt dump xmltree / badging)", "", "")
# 0055
ev("APK is signed with v1 (JAR) only; signer CN=Daniel Berstein, O=Sandlot Games, RSA 1024, sha1WithRSA, cert SHA-256 5b3cca3a9cb98df1c410e9ee5f948a8dc2910bfd761455baf8e2d820eeb8ac3b, valid 2011-02-23 to 2036-02-17.",
   APK, "META-INF/CERT.RSA, CERT.SF, MANIFEST.MF (apksigner verify --print-certs; openssl pkcs7)",
   "MANIFEST.MF lists 236 entries with SHA1-Digest.", "")
# 0056
ev("No nested payloads outside assets/lib/dex: the 6 OpenFeint doc-files PNGs are well-formed (IEND last, 0 trailing bytes), the 10 doc .htm/.lbi files are text (only UTF-8 punctuation), res JPEG has 0 bytes after EOI, and no ZIP/ELF/DEX/Ogg magic occurs inside any such entry.",
   APK, "com/openfeint/api/doc-files/*; managed_shell_scan.json apk_non_ogg_entry_checks", "", "doc-files are SDK documentation bundled by accident.")
# 0057
ev("OpenFeint product name/key/secret/app id are embedded as static final strings in SnailMailApplication.",
   DEX, S + "SnailMailApplication.smali:7-13,59-88", "Values intentionally not reproduced in docs.",
   "Credentials for a defunct service; must not be reused in a port.")
# 0058
ev("OpenFeint base URL is hard-coded as https://api.openfeint.com.",
   DEX, O + "OpenFeintInternal.smali:178", "",
   "The OpenFeint service was shut down (external fact, Dec 2012); all requests fail today.", "likely")
# 0059
ev("PfmLoadFileDat seeks to gJavaAssetStart + record offset, so archive offsets are relative to the asset's start inside the APK.",
   V7, "v7a:0x14cd8-0x14cf8", "ldr gJavaAssetStart; add r1,r10,r1; bl fseek@plt.", "32-bit start offset.")
# 0060
ev("Both libraries carry DT_TEXTREL and DT_SYMBOLIC and import no __android_log_*, dlopen or dlsym although liblog/libdl are NEEDED.",
   V7, "llvm-readelf -d / --dyn-syms (v7a and v5)", "", "Informational for the platform workstream.")
# 0061
ev("Native-side wprintf is a no-op in both builds, so all native debug strings are dead.",
   V7, "v7a:0x19948; v5 wprintf 0x1b724 called from JNIOFOSubmitCB", "", "")
# 0062
ev("HasFocus is written on the UI thread and read by onDrawFrame on the GL thread; AudioInitFlag written on UI, read/cleared on GL; SnailMailActivity.ActivityInitFlag is written but never read; ADRenderer.ActivityInitFlag is only initialised.",
   DEX, S + "SnailMailActivity.smali:129,191,456; ADRenderer.smali:28,1329,1372,1393,1410,1423",
   "All are plain (non-volatile) static fields.", "Benign data races; port should use atomics.")
# 0063
ev("JAVAPlaySample calls SoundPool.play(id, vol, vol, 1, 0, 1.0f); PfmAudioPlaySample scales vol by PfmSampleVolume and passes it to CallIntMethod as a double vararg.",
   V7, "v7a:0x14290-0x142b0, 0x14230-0x1427c; " + S + "ADRenderer.smali:790-818", "", "")
# 0064
ev("Bitmap.copyPixelsToBuffer on an ARGB_8888 bitmap yields bytes in R,G,B,A order with premultiplied alpha.",
   DEX, S + "ADRenderer.smali:1040-1061,1114-1135",
   "Not observable in these bytes; Android platform behaviour.",
   "Any native PNG/JPEG replacement must produce premultiplied RGBA to match original rendering.", "likely", "",
   "Validate with an on-device capture of a decoded texture.")
# 0065
ev("JNIOFOSave (only called by ADRenderer.JAVAOpenFeintOpen) branches to OFOSave: if gOFOValid, gRegisterSaveFile(\"of.cfg\", gOFOData, 11520).",
   V7, "v7a:0x7d71c, 0x7d6d8-0x7d708; " + S + "ADRenderer.smali:475-487", "", "Runs re-entrantly on the GL thread.")
# 0066
ev("JAVASaveFile writes with Context.openFileOutput(name, MODE_PRIVATE); JAVAFileSize uses FileInputStream.available(); JAVALoadFile does a single read(Data,0,length).",
   DEX, S + "ADRenderer.smali:98-148,208-254,820-904", "", "Saves live in the app-private files dir.")
# 0067
ev("JAVAPlayMusic creates a new MediaPlayer each call without releasing a previous one, prepares synchronously, loops, and applies MusicVolume.",
   DEX, S + "ADRenderer.smali:614-788", "", "Relies on native calling JAVAStopMusic first.")
# 0068
ev("JADX adds 'throws' clauses (e.g. onCreate throws IOException, onPause throws IllegalStateException) that do not exist in the DEX; otherwise the JADX output of all 9 non-R game-shell classes matches smali control flow.",
   DEX, S + "*.smali (no dalvik.annotation.Throws on these methods); work/jadx/sources/com/sandlotgames/snailmail/*.java",
   "Manual side-by-side comparison.", "Cosmetic decompiler artefact only.")
# 0069
ev("OpenFeint.initialize posts login(); with no saved user launchIntroFlow() only fires after a successful /xp/devices device-session request.",
   DEX, O + "OpenFeintInternal.smali:3466-3540 (launchIntroFlow sets mPostDeviceSessionRunnable)",
   "", "With the servers gone the IntroFlow Activity should never appear.", "likely", "", "Confirm on device with networking.")
# 0070
ev("JNIMouseEvent/JNIAccelerometer receive float arguments in core registers and on the stack (armeabi softfp), and the Call*Method varargs pass float as double.",
   V7, "v7a:0x1431c (vmov s14,r3), 0x14330 (vldr s15,[sp,#8]); 0x142c8 (ldr r12,[sp]); 0x14094/0x140c4 (vcvt.f64.f32; vstr d7)", "", "Only relevant for hand-written glue; C prototypes handle it on arm64.")

with open(ROOT + "analysis/evidence/jni.jsonl", "w") as f:
    for e in E:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
print(len(E))

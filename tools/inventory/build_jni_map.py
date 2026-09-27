#!/usr/bin/env python3
"""Build docs/JNI_MAP.json from the scan outputs plus curated annotations.

Numbers (addresses, sizes, gJAVAFunction entries, smali line numbers) are taken
from analysis/dex/jni_native_scan.json and analysis/dex/managed_shell_scan.json;
the interpretation text below is hand-curated from the disassembly/smali review
documented in docs/APK_AUDIT.md and analysis/evidence/jni.jsonl.

Usage (repo root, after running both scans):
    tools/inventory/build_jni_map.py
    tools/inventory/jni_native_scan.py --bin v7a=... --bin v5=... \
        --managed analysis/dex/managed_shell_scan.json --check-map docs/JNI_MAP.json
Output is deterministic.
"""
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..") + os.sep
scan = json.load(open(ROOT + "analysis/dex/jni_native_scan.json"))
managed = json.load(open(ROOT + "analysis/dex/managed_shell_scan.json"))
B = {b["tag"]: b for b in scan["binaries"]}
EXP = {t: {x["symbol"]: x for x in b["java_exports"]} for t, b in B.items()}
WR = {t: {x["name"]: x for x in b["java_callback_wrappers"]} for t, b in B.items()}
S = "work/apktool/smali/com/sandlotgames/snailmail/"
PKG = "Lcom/sandlotgames/snailmail/"

NAT = {(m["class"], m["name"]): m for m in managed["native_methods"]}


def addr(tag, sym):
    x = EXP[tag].get(sym)
    return {"addr": x["addr"], "size": x["size"]} if x else "MISSING"


def waddr(tag, sym):
    x = WR[tag][sym]
    return {"addr": x["addr"], "size": x["size"]}


def sym_for(cls, name):
    return "Java_com_sandlotgames_snailmail_%s_%s" % (cls, name)


def invoked(cls, name):
    m = NAT[(PKG + cls + ";", name)]
    return ["%s:%d" % (s["file"], s["line"]) for s in m["invoke_sites"]]


GL = ("GL thread (GLSurfaceView.GLThread; Renderer callback per documented GLSurfaceView "
      "contract)")
UI = "main/UI thread (Activity lifecycle / View input callback)"

entries = []
entries.append({
    "id": "JNI-META",
    "direction": "meta",
    "schema": "snailmail.jni_map/1",
    "binaries": {t: {"path": b["path"], "sha256": b["sha256"]} for t, b in B.items()},
    "dex_sha256": managed["dex"]["sha256"],
    "jni_function_table": ("JNINativeInterface order per jni.h/JNI spec; index = byte offset/4 on "
                           "32-bit ARM (offset/8 on arm64). Offsets used by this binary: "
                           "0x18 FindClass(6), 0x54 NewGlobalRef(21), 0x5c DeleteLocalRef(23), "
                           "0x7c GetObjectClass(31), 0x84 GetMethodID(33), 0xc8 CallIntMethodV(50), "
                           "0xf8 CallVoidMethodV(62), 0x178 GetFieldID(94), 0x190 GetIntField(100), "
                           "0x29c NewStringUTF(167), 0x2a4 GetStringUTFChars(169), "
                           "0x2c0 NewByteArray(176), 0x320 GetByteArrayRegion(200), "
                           "0x340 SetByteArrayRegion(208). All high confidence (table rebuilt "
                           "programmatically and spot-checked in tools/inventory/jni_native_scan.py)."),
    "binding": ("Static name binding only: 18 Java_* exports, no JNI_OnLoad, no RegisterNatives "
                "call (slot 0x35c never used), no GetJavaVM/AttachCurrentThread."),
    "cached_jni_state": {
        "gJavaEnv": {"v7a": B["v7a"]["jni_globals"]["gJavaEnv"]["addr"],
                     "v5": B["v5"]["jni_globals"]["gJavaEnv"]["addr"],
                     "meaning": "raw JNIEnv* of the thread that last ran nativeInit/nativeReInit (GL thread)"},
        "gJavaObj": {"v7a": B["v7a"]["jni_globals"]["gJavaObj"]["addr"],
                     "v5": B["v5"]["jni_globals"]["gJavaObj"]["addr"],
                     "meaning": "raw LOCAL reference to the ADRenderer instance (thiz of nativeInit/nativeReInit); never NewGlobalRef'd"},
        "gJavaClass": {"v7a": B["v7a"]["jni_globals"]["gJavaClass"]["addr"],
                       "v5": B["v5"]["jni_globals"]["gJavaClass"]["addr"],
                       "meaning": "raw LOCAL reference from GetObjectClass(thiz); only used inside JAVA_RegisterFunctions"},
        "gJAVAFunction": {"v7a": B["v7a"]["gJAVAFunction"]["addr"], "v5": B["v5"]["gJAVAFunction"]["addr"],
                          "size": 324,
                          "meaning": "27 x {jmethodID id; const char* name; const char* sig}; entries 0-25 filled by GetMethodID, entry 26 is an empty sentinel"},
    },
    "note": "Per-entry 'confidence' covers descriptor/symbol/address/behaviour claims; 'thread_confidence' is separate because thread identity rests on Android framework contracts.",
    "verify": "tools/inventory/jni_native_scan.py --bin v7a=... --bin v5=... --managed analysis/dex/managed_shell_scan.json --check-map docs/JNI_MAP.json",
    "evidence": ["EV-JNI-0006", "EV-JNI-0007", "EV-JNI-0008", "EV-JNI-0009", "EV-JNI-0013"],
    "confidence": "established",
})

j2n = [
    # (class, method, args, returns, thread, behavior, jobject, lifetime, hazards, evidence, confidence)
    ("SnailMailApplication", "JNIOFInit", [], "void",
     "n/a (never invoked)",
     "No native implementation exists in either binary (no Java_ export, no RegisterNatives). "
     "Invoking it would throw UnsatisfiedLinkError; the DEX never invokes it.",
     "n/a", "n/a",
     ["Port must not call it; keep the declaration unimplemented or drop it."],
     ["EV-JNI-0006"], "established"),
    ("SnailMailActivity", "JNIDebug", [], "jint: always 0",
     "Any thread that calls SnailMailActivity.wprintf: UI thread (lifecycle, input), GL thread "
     "(onSurfaceCreated/onDrawFrame/JAVA* methods), main thread OpenFeint callbacks.",
     "mov r0,#0; bx lr. Java wprintf prints only when JNIDebug()==1, so managed logging is "
     "permanently disabled. It is the first native call of a normal launch (wprintf in onCreate).",
     "jclass unused", "none",
     [], ["EV-JNI-0020", "EV-JNI-0021"], "established"),
    ("SnailMailActivity", "JNIDatInit",
     ["jclass (unused)", "java.io.FileDescriptor fd of the APK opened by AssetManager.openFd(\"asm.mp3\")",
      "jint start = (int)AssetFileDescriptor.getStartOffset()", "jint length = (int)AssetFileDescriptor.getLength()"],
     "void", UI + " - SnailMailActivity.onCreate, after setContentView and SoundPool creation",
     "FindClass(\"java/io/FileDescriptor\") -> NewGlobalRef(cls) -> GetFieldID(cls,\"descriptor\",\"I\") -> "
     "GetIntField(fd) -> dup() -> gJavaAssetFid=dupfd, gJavaAssetStart=start, gJavaAssetLength=length; "
     "gDatFP=fdopen(dupfd,\"rb\"); fseek(start); fread 244-byte header; gDat=RShellMemoryMalloc(hdr.u32[2]); "
     "fseek(start); fread directory; cRHash::Init(&gDatHash,count=u32@0,DatHashGetString); for each record i "
     "rewrite the u32 at archive offset 4+24*i (name_offset) into an absolute pointer and cRHash::Add(name,i). Finally "
     "DeleteLocalRef(cls) and DeleteLocalRef(globalRef) (sic).",
     "fd jobject used only during the call; not cached. The dup()ed int fd and FILE* are cached (gJavaAssetFid/gDatFP).",
     "Archive stays open for the process lifetime (JNIDatUnInit does not close it). A second onCreate in "
     "the same process leaks the previous FILE*/fd/directory.",
     ["Reflective read of private field FileDescriptor.descriptor via JNI (non-SDK field); port should "
      "use AAssetManager/AAsset_openFileDescriptor64 or ParcelFileDescriptor instead (hypothesis: "
      "greylisted on API 28+).",
      "start/length truncated from long to int in Java (fine for this 12.6 MB APK).",
      "Directory records store 32-bit absolute pointers in place of offsets: impossible with 64-bit "
      "pointers - keep offsets or a side table.",
      "NewGlobalRef result released with DeleteLocalRef (JNI misuse; global ref leak)."],
     ["EV-JNI-0016", "EV-JNI-0017", "EV-JNI-0018", "EV-JNI-0023", "EV-JNI-0059"], "established"),
    ("SnailMailActivity", "JNIDatUnInit", [], "void", UI + " - onStop",
     "Only two wprintf calls (native wprintf is a no-op). Does not close gDatFP or free gDat.",
     "jclass unused", "none",
     ["Do not 'fix' into a real close unless onRestart re-opens (onRestart never calls JNIDatInit)."],
     ["EV-JNI-0019"], "established"),
    ("SnailMailActivity", "JNIOFOSave", [], "void",
     "GL thread: only caller is ADRenderer.JAVAOpenFeintOpen(), which native code invokes from the GL "
     "thread (native->Java->native re-entry).",
     "b OFOSave: if gOFOValid -> gRegisterSaveFile(\"of.cfg\", gOFOData, 11520). Reaches JAVACSaveFile, "
     "i.e. uses the cached gJavaEnv/gJavaObj rather than its own env argument.",
     "jclass unused; uses cached gJavaObj", "none",
     ["Nested native frame uses a stale cached local ref (gJavaObj) instead of its own env/args."],
     ["EV-JNI-0015", "EV-JNI-0065"], "likely"),
    ("SnailMailActivity", "JNIResourceManagerInvalidate", [], "void",
     UI + " - onPause, after mGLView.onPause() and wl.release()",
     "cRResourceManager::Invalidate(&gResourceManager): gLoadingBar byte +0x35 = 1; every resource "
     "record (stride 140) gets +8 = 1. No GL and no JNI calls.",
     "jclass unused", "none",
     ["Writes state owned by the GL thread from the UI thread; relies on GLSurfaceView.onPause() "
      "having parked the GL thread (true for current AOSP; version-dependent on 2.x - hypothesis)."],
     ["EV-JNI-0049", "EV-JNI-0014"], "established"),
    ("ADGLSurfaceView", "JNIMouseEvent",
     ["jclass (unused)", "jint type: 0=ACTION_DOWN, 1=ACTION_MOVE, 2=ACTION_UP (Java mapping)",
      "jfloat x (MotionEvent.getX, view px)", "jfloat y (MotionEvent.getY, view px)"],
     "void", UI + " - ADGLSurfaceView.onTouchEvent",
     "Returns unless gGameValid. X=x*640/gG0DeviceScreenWidth, Y=y*480/gG0DeviceScreenHeight. "
     "type 0: Game->mouse(+0x228).ClickiPhone(true,X,Y); ClickOn(). type 1: if *(int*)(Game+0x718fc)==2 "
     "ClickiPhone(true,X,Y); then ClickOn(). any other type: ClickOff() (no position).",
     "jclass unused", "none",
     ["softfp ABI on ARM32 (x in r3, y on stack); on arm64 floats arrive in s0/s1 - ordinary C prototypes suffice.",
      "UI-thread writes to mouse state consumed by the GL thread (data race, unsynchronized in original).",
      "ACTION_CANCEL/POINTER_* are ignored by Java: a cancelled gesture never produces type 2."],
     ["EV-JNI-0024", "EV-JNI-0025", "EV-JNI-0070"], "established"),
    ("ADGLSurfaceView", "nativePause", [], "void", "n/a (never invoked from the DEX)",
     "Toggles an internal paused counter and accumulates paused wall time using gettimeofday "
     "milliseconds (_getTime).",
     "jclass unused", "none", [], ["EV-JNI-0048"], "established"),
    ("ADGLSurfaceView", "JNIKey",
     ["jobject thiz (ADGLSurfaceView, unused)",
      "jint Android keycode; Java only passes 7-16, 29-54, 62, 66, 67"],
     "void", UI + " - ADGLSurfaceView.onKeyDown (view must have focus)",
     "A-Z(29-54): cKeyPad::ConvertCode(Game+0xbf0,(char)(keycode+68)) i.e. 'a'..'z'; 0-9(7-16): "
     "ConvertCode((char)(keycode+41)) i.e. '0'..'9'; SPACE(62)->57, ENTER(66)->28, DEL(67)->14 "
     "(values equal DirectInput DIK_SPACE/DIK_RETURN/DIK_BACK - hypothesis on intent). KeySet(code) "
     "only when *(int*)(Game+0xbf0)==2, otherwise dropped.",
     "thiz unused", "none",
     ["Other keycodes would pass an uninitialised register to KeySet (unreachable from Java).",
      "UI-thread write to engine key state read by GL thread."],
     ["EV-JNI-0026", "EV-JNI-0027"], "established"),
    ("AccelerometerListener", "JNIAccelerometer",
     ["jobject thiz (listener, unused)", "jfloat -last_x", "jfloat -last_y", "jfloat +last_z "
      "(unit-normalised, low-pass alpha 0.3f)"],
     "void",
     "main thread - SensorManager.registerListener(listener, sensor, 0) without Handler delivers on the "
     "main Looper (framework contract)",
     "If Game != NULL: cAccelerometer::Input(Game+0xbd4, x, y, z).",
     "thiz unused", "Listener is never unregistered (stop() never called): keeps firing while paused/stopped "
     "and a new listener is added per onCreate.",
     ["Cross-thread write of accelerometer state (UI -> GL)."],
     ["EV-JNI-0028", "EV-JNI-0029"], "likely"),
    ("ADRenderer", "JNIAudioInit", ["jobject thiz (unused)"], "void",
     GL + " - onSurfaceCreated second+ time when AudioInitFlag is set",
     "bx lr (no-op).", "unused", "none", [], ["EV-JNI-0048", "EV-JNI-0044"], "established"),
    ("ADRenderer", "nativeDone", ["jobject thiz (unused)"], "void", "n/a (never invoked from the DEX)",
     "appDeinit() and importGLDeinit(), both bx lr (no-op).", "unused", "none", [],
     ["EV-JNI-0048"], "established"),
    ("ADRenderer", "nativeInit", ["jobject thiz = ADRenderer instance"], "void",
     GL + " - first onSurfaceCreated in the process (static SurfaceCreatedFirstTime)",
     "JAVA_RegisterFunctions(env, thiz): gJavaEnv=env, gJavaObj=thiz, gJavaClass=GetObjectClass(thiz), "
     "26 x GetMethodID into gJAVAFunction; then cRResourceManager::Init(&gResourceManager); "
     "G0StartBlackCount=2.",
     "CACHES env (raw JNIEnv*) and thiz/cls as RAW LOCAL REFERENCES in globals (no NewGlobalRef).",
     "gJavaObj/gJavaClass become invalid when nativeInit returns (JNI local-ref rules); they are reused "
     "by every later JAVA* callback. Worked on pre-ICS Dalvik (direct pointers); invalid under "
     "indirect-reference VMs.",
     ["Must become NewGlobalRef (or pass env/obj explicitly) in the port.",
      "gJavaEnv is only valid on the GL thread; any future callback from another thread must use "
      "JavaVM::GetEnv/AttachCurrentThread."],
     ["EV-JNI-0008", "EV-JNI-0009", "EV-JNI-0011", "EV-JNI-0044", "EV-JNI-0045"], "established"),
    ("ADRenderer", "nativeReInit", ["jobject thiz = ADRenderer instance"], "void",
     GL + " - every later onSurfaceCreated (new EGL context after pause, or new Activity in same process)",
     "JAVA_RegisterFunctions(env, thiz) (re-caches env/obj/method IDs); InitGL(); "
     "cRResourceManager::ReInit(&gResourceManager) (reloads textures through JAVAC_UnPng/UnJpg/UnZip, "
     "JAVACFileSize/FindFile/LoadFile); *(int*)(Game+0x718b4)=4; G0StartBlackCount=2.",
     "Same caching as nativeInit.", "Same as nativeInit.",
     ["Same as nativeInit."], ["EV-JNI-0011", "EV-JNI-0015", "EV-JNI-0045"], "established"),
    ("ADRenderer", "nativeRender", ["jobject thiz (unused)", "jint SystemPauseFlag: 1 when !SnailMailActivity.HasFocus"],
     "void", GL + " - every onDrawFrame (RENDERMODE_CONTINUOUSLY default)",
     "if (AppInit()) appRender(flag != 0). Main game tick; reaches all JAVA* callbacks and GL imports.",
     "thiz unused (callbacks use cached gJavaObj)", "none",
     ["HasFocus is a non-volatile static written on UI thread, read on GL thread."],
     ["EV-JNI-0015", "EV-JNI-0046"], "established"),
    ("ADRenderer", "nativeResize", ["jobject thiz (unused)", "jint w", "jint h"], "void",
     GL + " - onSurfaceChanged",
     "gG0DeviceScreenWidth = gG0ScreenWidth = (float)w; gG0DeviceScreenHeight = gG0ScreenHeight = (float)h.",
     "thiz unused", "none", [], ["EV-JNI-0047"], "established"),
    ("ADRenderer", "JNIOFOSubmitCB",
     ["jobject thiz (ADRenderer; unused)",
      "jint CBOFOStatePtr: native pointer (&gOFOData[user].leaderboardState[i]) on success, 0 on failure"],
     "void",
     "main thread - OpenFeint Client posts request completions to a Handler created on the main thread "
     "(Client ctor runs inside OpenFeint.initialize from Application.onCreate)",
     "if (ptr) *(uint8_t*)ptr = 2; gOFOSaveFlag = 1.",
     "thiz unused", "Pointer originates from GL-thread code and is dereferenced on the main thread.",
     ["Native pointer round-tripped through a Java int: truncates on arm64.",
      "Cross-thread byte write without synchronisation.",
      "OpenFeint servers are defunct; native only submits when OFIsUserLoggedIn() is true, so this path is dormant."],
     ["EV-JNI-0037", "EV-JNI-0038", "EV-JNI-0039", "EV-JNI-0040", "EV-JNI-0041"], "likely"),
    ("ADRenderer", "JNIOFOUnlockCB",
     ["jobject thiz (ADRenderer; unused)",
      "jint CBOFOStatePtr: native pointer (&gOFOData[user].achievementState[i]) on success/already-unlocked, 0 on unlock failure"],
     "void", "main thread (see JNIOFOSubmitCB)",
     "if (ptr) *(uint8_t*)ptr = 2; gOFOSaveFlag = 1.",
     "thiz unused",
     "No callback at all when Achievement.load() fails (ADRenderer$2 lacks onFailure) - the state byte stays 1.",
     ["Same pointer-in-int hazard as JNIOFOSubmitCB."],
     ["EV-JNI-0037", "EV-JNI-0038", "EV-JNI-0039", "EV-JNI-0040", "EV-JNI-0041"], "likely"),
    ("MyOpenFeintDelegate", "JNIOFOInit",
     ["jobject thiz (MyOpenFeintDelegate; unused)", "jstring CurrentUser.userID()"], "void",
     "main thread - OpenFeintDelegate.userLoggedIn is called from OpenFeint request completions (Handler on main thread)",
     "GetStringUTFChars(userID, NULL) (never released); OFONewUser(chars): strcpy(gOFUser, chars); "
     "strcpy(gConfig+240, gOFUser); gOFOLoadFlag = 1.",
     "jstring chars leaked (no ReleaseStringUTFChars)",
     "Dormant today (login can never succeed against defunct servers).",
     ["Unbounded strcpy of a server-supplied string into fixed buffers."],
     ["EV-JNI-0042", "EV-JNI-0041"], "likely"),
]

for (cls, meth, args, ret, thread, beh, jobj, life, haz, ev, conf) in j2n:
    m = NAT[(PKG + cls + ";", meth)]
    sym = sym_for(cls, meth)
    entries.append({
        "id": "J2N-%s.%s" % (cls, meth),
        "direction": "java_to_native",
        "java_class": PKG + cls + ";",
        "java_method": meth,
        "descriptor": m["descriptor"],
        "static": m["static"],
        "access": m["access"],
        "smali_declaration": "%s:%d" % (m["smali_declaration"]["file"], m["smali_declaration"]["line"]),
        "native_symbol": sym,
        "v7a": addr("v7a", sym),
        "v5": addr("v5", sym),
        "invoked_from": invoked(cls, meth),
        "args": ["JNIEnv* env"] + args,
        "returns": ret,
        "thread": thread,
        "thread_confidence": "established" if thread.startswith("n/a") else "likely",
        "native_side_behavior": beh,
        "jobject_handling": jobj,
        "lifetime_notes": life,
        "arm64_hazards": haz,
        "evidence": ev,
        "confidence": conf,
    })

# native -> Java callbacks
n2j = {
    "JAVALoadSample": ("_Z14JAVALoadSamplePc", "int JAVALoadSample(char* name)",
                       "NewStringUTF(name); CallIntMethod; DeleteLocalRef(str); return Java result.",
                       "Opens assets/<name>.ogg with AssetManager.openFd and SoundPool.load(fd, off, len, 1); returns sample id or -1. AssetFileDescriptor never closed.",
                       ["PfmAudioLoadSample"]),
    "JAVAPlaySample": ("_Z14JAVAPlaySampleif", "int JAVAPlaySample(int id, float vol)",
                       "CallIntMethod(id, (double)vol) - float promoted to double through C varargs, as CallIntMethodV expects.",
                       "SoundPool.play(id, vol, vol, priority 1, loop 0, rate 1.0f) -> stream id.",
                       ["PfmAudioPlaySample (vol *= PfmSampleVolume)"]),
    "JAVAStopSample": ("_Z18PfmAudioStopSamplei", "void PfmAudioStopSample(int stream)",
                       "CallVoidMethod(stream).", "SoundPool.stop(stream).",
                       ["RShellSoundStopSample", "RShellSoundStopLooped"]),
    "JAVASaveFile": ("_Z13JAVACSaveFilePcPvi", "int JAVACSaveFile(char* name, void* data, int len)",
                     "arr=NewByteArray(len); SetByteArrayRegion(arr,0,len,data); str=NewStringUTF(name); r=CallIntMethod(str,arr,len); DeleteLocalRef(str); DeleteLocalRef(arr); return r!=0.",
                     "Context.openFileOutput(name, MODE_PRIVATE).write(data,0,len); returns 1/0.",
                     ["PfmSaveFile"]),
    "JAVADeleteFile": ("_Z15JAVACDeleteFilePc", "void JAVACDeleteFile(char* name)",
                       "NewStringUTF; CallVoidMethod; DeleteLocalRef.", "Context.deleteFile(name).",
                       ["PfmDeleteFile"]),
    "JAVALoadFile": ("_Z13JAVACLoadFilePcPv", "int JAVACLoadFile(char* name, void* dst)",
                     "size=JAVACFileSize(name); if 0 return 0; arr=NewByteArray(size); str=NewStringUTF(name); CallVoidMethod(str,arr,size); GetByteArrayRegion(arr,0,size,dst); DeleteLocalRef x2; return size.",
                     "openFileInput(name).read(Data,0,length) - single read() call, short reads not retried.",
                     ["PfmLoadFile"]),
    "JAVAFindFile": ("_Z13JAVACFindFilePc", "int JAVACFindFile(char* name)",
                     "NewStringUTF; CallIntMethod; DeleteLocalRef; return r!=0.",
                     "1 if openFileInput(name) succeeds, else 0.", ["PfmFindFile", "PfmLoadFile"]),
    "JAVAFileSize": ("_Z13JAVACFileSizePc", "int JAVACFileSize(char* name)",
                     "NewStringUTF; CallIntMethod; DeleteLocalRef.",
                     "FileInputStream.available() of openFileInput(name), 0 on error.",
                     ["PfmLoadFile", "JAVACLoadFile"]),
    "JAVASetMusicVolume": ("_Z22PfmAudioSetMusicVolumef", "void PfmAudioSetMusicVolume(float v)",
                           "CallVoidMethod((double)v).", "MusicVolume=v; MusicPlayer.setVolume(v,v) if playing.",
                           ["RShellMusicVolume"]),
    "JAVAPlayMusic": ("_Z17PfmAudioPlayMusicPc", "void PfmAudioPlayMusic(char* name)",
                      "NewStringUTF; CallVoidMethod; DeleteLocalRef.",
                      "new MediaPlayer on assets/<name>.ogg (openFd), prepare(), setLooping(true), setVolume(MusicVolume), start(). Previous MediaPlayer is not released here.",
                      ["RShellMusicPlay"]),
    "JAVAStopMusic": ("_Z17PfmAudioStopMusicv", "void PfmAudioStopMusic()", "CallVoidMethod.",
                      "MusicPlayer.stop(); release(); MusicPlayer=null.", ["RShellMusicStop"]),
    "JAVAUnPauseMusic": ("_Z20PfmAudioUnPauseMusicv", "void PfmAudioUnPauseMusic()", "CallVoidMethod.",
                         "MusicPlayer.start() if non-null.", ["RShellMusicUnPause"]),
    "JAVAPauseMusic": ("_Z18PfmAudioPauseMusicv", "void PfmAudioPauseMusic()", "CallVoidMethod.",
                       "MusicPlayer.pause() if non-null.", ["RShellMusicPause"]),
    "JAVAMusicRestart": ("_Z16JAVAMusicRestartv", "void JAVAMusicRestart()", "CallVoidMethod.",
                         "MusicPlayer.start() if non-null.", ["cRResourceManager::AI"]),
    "JAVAUnZip": ("_Z11JAVAC_UnZipPviS_i", "void JAVAC_UnZip(void* dst, int dstLen, void* src, int srcLen)",
                  "a=NewByteArray(dstLen); b=NewByteArray(srcLen); SetByteArrayRegion(b,0,srcLen,src); CallVoidMethod(a,b); GetByteArrayRegion(a,0,dstLen,dst); DeleteLocalRef x2.",
                  "ZipInputStream over src; inflates ONLY the first entry into dst (bytewise copy); all exceptions swallowed.",
                  ["PfmLoadFileDat (type 1)"]),
    "JAVAUnJpg": ("_Z11JAVAC_UnJpgPviS_iii", "void JAVAC_UnJpg(void* dst, int dstLen, void* src, int srcLen, int w, int h)",
                  "a=NewByteArray(dstLen); b=NewByteArray(srcLen); SetByteArrayRegion(b,..src); CallVoidMethod(a,b); rowBytes=dstLen/h; for i<h: GetByteArrayRegion(a, i*rowBytes, rowBytes, dst+(h-1-i)*rowBytes) (vertical flip); DeleteLocalRef x2. w is unused.",
                  "BitmapFactory.decodeByteArray(ARGB_8888, inDither) then copyPixelsToBuffer(ByteBuffer.wrap(dst)): RGBA byte order, premultiplied alpha (platform behaviour, likely).",
                  ["PfmLoadFileDat (type 2)"]),
    "JAVAUnPng": ("_Z11JAVAC_UnPngPviS_iii", "void JAVAC_UnPng(void* dst, int dstLen, void* src, int srcLen, int w, int h)",
                  "Same as JAVAC_UnJpg (bottom-up row copy).",
                  "Same as JAVAUnJpg; for PNG with alpha the premultiplication matters.",
                  ["PfmLoadFileDat (type 3)"]),
    "JAVAOpenFeintOpen": ("_Z17JAVAOpenFeintOpenv", "void JAVAOpenFeintOpen()", "CallVoidMethod.",
                          "Calls native JNIOFOSave() (re-entrant) then Dashboard.open() (starts an Activity from the GL thread).",
                          ["OFOpen"]),
    "JAVAOpenFeintLastLoggedInUserID": ("_Z31JAVAOpenFeintLastLoggedInUserIDPci", "void JAVAOpenFeintLastLoggedInUserID(char* out, int len)",
                                        "arr=NewByteArray(len) (callers pass 64); CallVoidMethod(arr); GetByteArrayRegion(arr,0,len,out); DeleteLocalRef; if out[0] strcpy(gConfig+240,out) else strcpy(out,gConfig+240).",
                                        "Writes getCurrentUser().userID() bytes + NUL into the array with no bounds check (AIOOBE if >= len).",
                                        ["OFGetHighScore", "OFSetHighScore"]),
    "JAVAOpenFeintSubmit": ("_Z19JAVAOpenFeintSubmitPcii", "void JAVAOpenFeintSubmit(char* leaderboard, int score, int statePtr)",
                            "NewStringUTF; CallVoidMethod(str, score, statePtr); DeleteLocalRef.",
                            "new Score(score).submitTo(new Leaderboard(id), cb) - cb forwards statePtr (success) or 0 (failure) to JNIOFOSubmitCB on the main thread.",
                            ["OFAddArcade", "OFAddArcadePro", "OFAddChallenge", "OFAddTimeTrial", "OFOUpdate"]),
    "JAVAOpenFeintUnlock": ("_Z19JAVAOpenFeintUnlockPci", "void JAVAOpenFeintUnlock(char* achievement, int statePtr)",
                            "NewStringUTF; CallVoidMethod(str, statePtr); DeleteLocalRef.",
                            "Achievement.load(cb2); cb2.onSuccess: unlock(cb3) or JNIOFOUnlockCB(statePtr) if already unlocked; cb3 -> JNIOFOUnlockCB(statePtr|0). No callback on load failure.",
                            ["OFAddAchievement", "OFOUpdate"]),
    "JAVAOpenFeintIsUserLoggedIn": ("_Z27JAVAOpenFeintIsUserLoggedInv", "int JAVAOpenFeintIsUserLoggedIn()",
                                    "CallIntMethod; return r!=0.", "OpenFeint.isUserLoggedIn() ? 1 : 0.",
                                    ["OFIsUserLoggedIn"]),
    "JAVAOpenFeintIsOnline": ("_Z21JAVAOpenFeintIsOnlinev", "int JAVAOpenFeintIsOnline()",
                              "CallIntMethod; return r!=0.",
                              "OpenFeint.isNetworkConnected() ? 1 : 0 (ConnectivityManager, not server reachability).",
                              ["OFIsOnline"]),
    "JAVATime": ("_Z8JAVATimev", "uint64_t JAVATime()",
                 "lo=CallIntMethod(JAVATime); hi=CallIntMethod(JAVATimeHi); return (((uint64_t)hi<<32)|(uint32_t)lo)/1000 (__aeabi_uldivmod) -> microseconds from System.nanoTime().",
                 "JAVATime: JTime=System.nanoTime(); return (int)JTime.", ["GetTime"]),
    "JAVATimeHi": ("_Z8JAVATimev", "uint64_t JAVATime()",
                   "Second call inside JAVATime(), must follow JAVATime on the same thread.",
                   "return (int)(JTime >> 32) (reads the static written by JAVATime).", ["GetTime"]),
    "JAVAVibrate": ("_Z11JAVAVibratei", "void JAVAVibrate(int ms)", "CallVoidMethod(ms).",
                    "Vibrator.vibrate((long)ms).", ["PfmVibrate (300 ms)"]),
}

haz_common = ["Uses cached gJavaEnv (GL-thread JNIEnv*) and stale local ref gJavaObj; port must use a "
              "global ref / valid env.",
              "Pending Java exceptions are never checked (no ExceptionCheck/ExceptionClear anywhere)."]
extra_haz = {
    "JAVAOpenFeintSubmit": ["jint carries a native pointer (truncation on arm64)."],
    "JAVAOpenFeintUnlock": ["jint carries a native pointer (truncation on arm64)."],
    "JAVAUnJpg": ["Output byte order RGBA + premultiplied alpha + bottom-up rows must be reproduced by any native decoder replacement.",
                  "decodeByteArray returning null -> NullPointerException left pending."],
    "JAVAUnPng": ["Output byte order RGBA + premultiplied alpha + bottom-up rows must be reproduced by any native decoder replacement.",
                  "decodeByteArray returning null -> NullPointerException left pending."],
    "JAVAUnZip": ["Buffer sized by native (dstLen); Java truncates silently on overflow (exception swallowed)."],
    "JAVAOpenFeintLastLoggedInUserID": ["Fixed 64-byte buffer sized by native; Java does not bound-check."],
    "JAVATime": ["Two JNI calls + a Java static per timestamp; replace with clock_gettime(CLOCK_MONOTONIC) in the port."],
    "JAVATimeHi": ["Non-atomic 2-call protocol through a static long; replace with clock_gettime."],
    "JAVAOpenFeintOpen": ["Starts an Activity and re-enters native code from within a native frame."],
}
for e in B["v7a"]["gJAVAFunction"]["entries"]:
    if not e["name"]:
        continue
    name = e["name"]
    wsym, wproto, nat, jav, callers = n2j[name]
    ent5 = B["v5"]["gJAVAFunction"]["entries"][e["index"]]
    helper = "CallIntMethodV via _JNIEnv::CallIntMethod" if e["signature"].endswith(")I") else \
        "CallVoidMethodV via _JNIEnv::CallVoidMethod"
    entries.append({
        "id": "N2J-ADRenderer.%s" % name,
        "direction": "native_to_java",
        "java_class": PKG + "ADRenderer;",
        "java_method": name,
        "descriptor": e["signature"],
        "static": False,
        "java_smali": [m for m in e["managed_matches"]],
        "gJAVAFunction": {"index": e["index"], "table_offset": e["table_offset"],
                          "v7a_entry_addr": e["entry_addr"], "v5_entry_addr": ent5["entry_addr"]},
        "jni_call": helper,
        "native_wrapper": {"symbol": wsym, "prototype": wproto,
                           "v7a": waddr("v7a", wsym), "v5": waddr("v5", wsym)},
        "native_callers": callers,
        "env_source": "gJavaEnv (cached by JAVA_RegisterFunctions in nativeInit/nativeReInit)",
        "object_source": "gJavaObj (raw local ref to the ADRenderer instance from nativeInit/nativeReInit)",
        "native_side_behavior": nat,
        "java_side_behavior": jav,
        "thread": ("GL thread: every native caller chain is rooted in nativeRender/nativeReInit (or "
                   "JNIOFOSave, itself GL-thread) or in address-taken engine methods driven by the game loop; "
                   "no UI/main-thread entry point can reach it (their call closures contain no indirect calls)."),
        "thread_confidence": "likely",
        "lifetime_notes": "Local refs created per call (NewStringUTF/NewByteArray) are deleted before return.",
        "arm64_hazards": haz_common + extra_haz.get(name, []),
        "evidence": ["EV-JNI-0009", "EV-JNI-0010", "EV-JNI-0012", "EV-JNI-0014", "EV-JNI-0015"] + {
            "JAVATime": ["EV-JNI-0030", "EV-JNI-0031"], "JAVATimeHi": ["EV-JNI-0030", "EV-JNI-0031"],
            "JAVAUnPng": ["EV-JNI-0032", "EV-JNI-0033", "EV-JNI-0034", "EV-JNI-0064"],
            "JAVAUnJpg": ["EV-JNI-0032", "EV-JNI-0033", "EV-JNI-0034", "EV-JNI-0064"],
            "JAVAUnZip": ["EV-JNI-0035", "EV-JNI-0036"],
            "JAVAOpenFeintSubmit": ["EV-JNI-0037", "EV-JNI-0038"],
            "JAVAOpenFeintUnlock": ["EV-JNI-0037", "EV-JNI-0038"],
            "JAVAOpenFeintLastLoggedInUserID": ["EV-JNI-0043"],
            "JAVAPlaySample": ["EV-JNI-0063"], "JAVASaveFile": ["EV-JNI-0066"],
            "JAVAFileSize": ["EV-JNI-0066"], "JAVAPlayMusic": ["EV-JNI-0067"],
            "JAVALoadSample": ["EV-JNI-0053"],
        }.get(name, []),
        "confidence": "established",
    })

entries.append({
    "id": "NFA-FileDescriptor.descriptor",
    "direction": "native_field_access",
    "java_class": "Ljava/io/FileDescriptor;",
    "field": "descriptor", "field_signature": "I",
    "native_function": {"symbol": "Java_com_sandlotgames_snailmail_SnailMailActivity_JNIDatInit",
                        "v7a_sites": {"FindClass": "0x15270", "NewGlobalRef": "0x1528c", "GetFieldID": "0x152b4",
                                      "GetIntField": "0x152ec", "DeleteLocalRef": ["0x15428", "0x1543c"]},
                        "v5_sites": {"FindClass": "0x15b5c", "NewGlobalRef": "0x15b78", "GetFieldID": "0x15ba0",
                                     "GetIntField": "0x15bd8", "DeleteLocalRef": ["0x15d1c", "0x15d30"]}},
    "thread": UI + " (onCreate)",
    "behavior": "Reads the raw int fd out of the FileDescriptor passed to JNIDatInit and dup()s it.",
    "arm64_hazards": ["Private-field reflection; replace with NDK AAsset APIs.",
                      "Global ref from NewGlobalRef deleted with DeleteLocalRef (leak/misuse)."],
    "evidence": ["EV-JNI-0016", "EV-JNI-0017"],
    "confidence": "established",
})

hazards = [
    ("HZ-01", "Cached JNIEnv* and raw local references",
     "gJavaEnv/gJavaObj/gJavaClass are captured from nativeInit/nativeReInit without NewGlobalRef and reused by all 26 callbacks in later native frames.",
     "Hold ADRenderer (or its replacement) in a NewGlobalRef, cache jclass as global ref, and obtain JNIEnv per call via JavaVM::GetEnv (or pass it down from the current JNI entry).",
     ["EV-JNI-0008", "EV-JNI-0011", "EV-JNI-0012"], "established"),
    ("HZ-02", "Native pointers carried in Java int",
     "JAVAOpenFeintSubmit(..., int CBOFOStatePtr) / JAVAOpenFeintUnlock(..., int) receive &gOFOData[...] and JNIOFOSubmitCB(int)/JNIOFOUnlockCB(int) dereference it as uint8_t*.",
     "Use jlong (or an index into gOFOData) in the port; the Java shell descriptors must change accordingly, or OpenFeint must be stubbed so the callbacks never fire.",
     ["EV-JNI-0037", "EV-JNI-0038", "EV-JNI-0039"], "established"),
    ("HZ-03", "FileDescriptor -> int fd extraction",
     "JNIDatInit reflects FileDescriptor.descriptor (private) and dup()s it.",
     "Use AAssetManager_open + AAsset_openFileDescriptor64 (off64_t start/length) or ParcelFileDescriptor.getFd()/detachFd().",
     ["EV-JNI-0016"], "established"),
    ("HZ-04", "AssetFileDescriptor offsets truncated to int",
     "getStartOffset()/getLength() are long-to-int'd before JNIDatInit; native stores them in 32-bit globals and fseek()s with long.",
     "Pass int64 (jlong/off64_t) and use fseeko/pread with 64-bit offsets.",
     ["EV-JNI-0023", "EV-JNI-0059"], "established"),
    ("HZ-05", "Split 64-bit time",
     "System.nanoTime is returned as two jints (JAVATime low word, JAVATimeHi from a static long) and recombined natively into uint64 microseconds.",
     "Replace with clock_gettime(CLOCK_MONOTONIC) in native code; keep microsecond uint64 semantics (callers compare r0:r1).",
     ["EV-JNI-0030", "EV-JNI-0031"], "established"),
    ("HZ-06", "Byte[] buffers sized by native code",
     "JAVAUnZip/UnJpg/UnPng/LoadFile/LastLoggedInUserID write into arrays whose size is chosen natively; overflow is either swallowed (UnZip) or leaves a pending exception (UnPng/UnJpg/UserID) that native never checks.",
     "Decode natively (zlib/libpng/libjpeg or stb) into caller-sized buffers with explicit length checks; if JNI is kept, add ExceptionCheck.",
     ["EV-JNI-0033", "EV-JNI-0035", "EV-JNI-0043"], "established"),
    ("HZ-07", "Pixel format contract of JAVAC_UnPng/UnJpg",
     "Rows are copied bottom-up into a buffer preceded by an 18-byte TGA header (32 bpp, descriptor 8); pixel bytes come from ARGB_8888 copyPixelsToBuffer (RGBA order, premultiplied).",
     "A native decoder must emit RGBA, premultiply alpha (to match original blending) and flip rows; validate against device captures.",
     ["EV-JNI-0033", "EV-JNI-0034", "EV-JNI-0064"], "likely"),
    ("HZ-08", "32-bit pointers stored in archive directory",
     "JNIDatInit rewrites each record's name_offset (u32 at archive offset 4+24*i) into an absolute pointer.",
     "Keep the offset and resolve on lookup, or build a separate char* table.",
     ["EV-JNI-0018"], "established"),
    ("HZ-09", "Cross-thread shared state without synchronisation",
     "UI thread (touch, key, sensor, onPause invalidate) and main-thread OpenFeint callbacks write engine globals read by the GL thread; Java statics HasFocus/AudioInitFlag are non-volatile.",
     "Queue input events to the GL thread (GLSurfaceView.queueEvent or a lock-free ring) and use atomics for flags.",
     ["EV-JNI-0014", "EV-JNI-0062"], "established"),
    ("HZ-10", "JNI misuse that newer runtimes/CheckJNI reject",
     "DeleteLocalRef on a global ref (JNIDatInit), GetStringUTFChars without Release (JNIOFOInit), no exception checks, stale local refs.",
     "Fix in the port; run with -Xcheck:jni during validation.",
     ["EV-JNI-0017", "EV-JNI-0042", "EV-JNI-0013"], "established"),
    ("HZ-11", "softfp float passing",
     "ARM32 JNI natives receive float args in core registers/stack (JNIMouseEvent x in r3, y on stack; JNIAccelerometer z on stack); varargs Call*Method pass float as double.",
     "No action for C/C++ prototypes on arm64 (AAPCS64 passes floats in v-registers); only relevant to hand-written glue or emulation.",
     ["EV-JNI-0025", "EV-JNI-0063", "EV-JNI-0070"], "established"),
]
for hid, title, desc, port, ev, conf in hazards:
    entries.append({"id": hid, "direction": "hazard", "title": title, "description": desc,
                    "port_guidance": port, "evidence": ev, "confidence": conf})

json.dump(entries, open(ROOT + "docs/JNI_MAP.json", "w"), indent=1)
open(ROOT + "docs/JNI_MAP.json", "a").write("\n")
print(len(entries), "entries")

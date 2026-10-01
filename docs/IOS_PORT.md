# Native iOS port bootstrap

The iOS target reuses the same statically translated game core used by the Android ARM64 port. No ARM32 code, interpreter, JIT, emulator, Android runtime, or JNI library is shipped in the iOS app. The original guest JNI calls are serviced by a small native Objective-C++ compatibility layer implementing the callback names and semantics the game expects.

## Architecture

```
SnailMailIOS (UIKit / CADisplayLink / CoreMotion)
        |
        +-- SMIOSHost --------------------+--> AOT translated game core
        |                                 |       (normal compiled code)
        +-- SMIOSJava (pseudo-Java ops) --+--> files, image decode, audio, time, haptics
        |
        +-- smgl (GLES 1.1 semantics on GLES 2)
                    |
                    +--> EAGLContext / CAEAGLLayer
```

The GLES2 host is deliberately a bootstrap backend. OpenGL ES is deprecated by Apple, so the long-term iOS renderer should consume the existing `sm_gl_backend` boundary through Metal. Keeping this first target on `smgl` minimizes changes while boot/input/gameplay parity is established.

## Build from your own APK

Requirements on macOS:

- Xcode with the iOS SDK
- CMake
- Python 3 with `capstone==5.0.7` and `pyelftools==0.33`
- `ffmpeg` is recommended; the helper converts the original Ogg/Vorbis audio to M4A/AAC for AVFoundation without committing game assets

```sh
python3 -m pip install capstone==5.0.7 pyelftools==0.33
brew install cmake ffmpeg
python3 tools/ios_build/build_ios.py /path/to/com.sandlotgames.snailmail-1.00.apk --configure-only
open work/ios_build/xcode/snailmail_reconstructed.xcodeproj
```

Select the `SnailMailIOS` target, choose your signing team, connect an iPhone/iPad, and run. For command-line building after signing is configured:

```sh
python3 tools/ios_build/build_ios.py /path/to/snailmail.apk --build --team YOUR_TEAM_ID
```

The known reference APK SHA-256 is `0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7`. The helper refuses a different binary by default because the existing AOT validation applies to that exact code image.

## Implemented platform boundaries

- Landscape full-screen UIKit shell.
- OpenGL ES 2 drawable plus the validated GLES1-on-GLES2 `smgl` renderer.
- 60 Hz `CADisplayLink` presentation. This is intentional because the original simulation executes at least one 60 Hz step for every render and otherwise speeds up when naively driven at 120 Hz.
- Single-pointer touch mapping through `sm_port_map_touch`.
- CoreMotion accelerometer mapping with the same normalization and time-based smoothing constants as the Android ARM64 shell.
- App-private save/config files in Application Support.
- `System.nanoTime()` compatibility using monotonic Mach time.
- PNG/JPEG decode through ImageIO/CoreGraphics into premultiplied RGBA.
- ZIP decode through zlib.
- Sample/music bridge through AVAudioPlayer. The preparation script converts the original Ogg/Vorbis assets to AAC/M4A when `ffmpeg` is available.
- Vibration through AudioToolbox.
- OpenFeint remains intentionally offline because the service is defunct.

## Validation still required on Apple hardware

This branch establishes the native target and platform boundary, but it is not claimed device-validated until it is built on macOS/Xcode and exercised on a physical iPhone/iPad. The first device pass should check: the 4 GiB `PROT_NONE` guest reservation, boot through `gAppState 0..10`, GL framebuffer completeness, menu touch alignment, accelerometer signs in both landscape orientations, ImageIO pixel parity, audio latency/looping, save reload, background/resume, and a 6,000-frame GL trace against the existing reference harness.

After parity, the next renderer step is a Metal implementation behind `sm_gl_backend`; the AOT game core and iOS host do not need to change for that migration.

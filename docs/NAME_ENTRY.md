# Game Over / name-entry subsystem

The name-entry scene always uses the source artwork's **480×320** reference
canvas, including when Screen Stretched is selected for other screens.
Gameplay control modes are independent of this UI adaptation.

`aot/port/nameentry_layout.c` owns the reference geometry, fit, hit testing and
directional navigation. `port_nameentry.c` owns the original keypad/high-score
lifetime and routes inputs into the original editor. `port_display.c` uses
the same transform for queued glyphs, keyboard bitmap and selection glow.
The decorative menu background reuses the main menu's widescreen panel
expansion, preserving its edge artwork. No source artwork was edited.

## Recovered original assumptions

The owner's APK contains `KEYPAD/KEYPAD.PNG` (512×256 texture, visible width
480) and `KEYPAD/KEYPADMASK.TXT`. The mask contains 40 records: the `$` name
field, 36 alphanumeric keys, Space, `@` (delete), and `#` (Return). Its
coordinates are source-image pixels. The keyboard is bottom-anchored in the
480×320 composition: its source origin is (0,64).

Original `cKeyPad::Open` (`0x3be24`) changes mask X into `x/480*640` and Y
into `96+y/256*384`, truncating to integers. `Render` (`0x3b9b0`) queues the
bitmap at logical (0,96), size 640×384, with U extent 480/512. `AI`
(`0x3bbe8`) samples the game mouse floats at Game+0x234/+0x238, truncates
them, calls `KeyTest`, then converts the returned **asset character**, not
its index, through `ConvertCode` (`0x3b2f0`) to `KeySet` (`0x20c54`).
The original border editor implements capitalization, name length, deletion
and caret behavior; the high-score subsystem owns storage and Submit/Cancel.

`cRHighScore::Init` (`0x56010`) distinguishes a new score from score viewing
using its second parameter: -1 means viewing; another value selects a name
row. Its `UnInit` (`0x55ed4`) ends the adapted scene. Return closes the pad
through its original state 3 closing animation; Submit then commits the name
and returns through the original high-score exit path.

## One transform

For surface W×H and surface-local safe insets L,T,R,B:

```
s = min((W-L-R)/480, (H-T-B)/320)
origin = (L + ((W-L-R)-480*s)/2, T + ((H-T-B)-320*s)/2)
pixel = origin + reference * s
reference = (pixel-origin)/s
```

On widescreen the safe rectangle is first intersected with the widened
frame's authored interior: left 40, top 64, right margin 20 and bottom margin
20 art pixels, all scaled by H/320. `sm_name_scene_fit` owns that intersection;
rendering, touch, selection and debug snapshots all use its resulting canvas.
This reserves the original logo/header and bottom frame without offsetting
individual keyboard elements. The complete 480×320 name composition fits
uniformly inside that rectangle.

The original 640×480 coordinates are a compatibility boundary only:
normalize X by 640 and Y by 480, then express that normalized point on the
480×320 reference. The final source-art-to-display scale is **one scalar**.
The keyboard, field, text and glow share its origin and scale.
Full-width keyboard quads never use the old full-screen stretching exception.
On widescreen, the menu background fills the display with the same split
panel expansion as the main menu: intact left/right ornaments, an expanded
purple middle, and the original logo aligned right. The keyboard's safe
canvas sits centered over that background; its side space is filled by the
panel instead of black margins. Background decoration is not interactive.

The mask is loaded and parsed from the APK at runtime; no copied key positions
or row-to-character tables define the new layout. The `$` field cannot be
activated as a key. Hit rectangles are half-open, gaps do not select a key,
and input preserves the sampled floats before the original AI's truncation.

Android forwards surface-local cutout/system-bar insets on the GL queue.
Window insets are adjusted for any margin Android already applies to the
surface to prevent applying an inset twice. API 28 cutout access uses public
API reflection because the existing build supports older SDK headers; see
[Android WindowInsets](https://developer.android.com/reference/android/view/WindowInsets).

Touch tracks one pointer and handles cancel/pointer-up while name entry is
active. Touch and handled hardware input share the GL queue. Arrow keys move
between visible row centres, including staggered rows and wide Space/Return
keys; edges stay put. DPAD centre/controller A activates selection. Physical
letters, digits, Space and Delete still enter the original key-code/editor
path. Hardware Enter closes the pad like its Return key. Unsupported Android
key codes are ignored rather than entering the original JNI path's undefined
register value. The JNI hook also guards calls before Game exists.

## Issues and verification

| What happened | Why / origin | Change | Verification |
|---|---|---|---|
| Visible keys differed from touch selection; bitmap and cursor were offset. | `G0RenderFont` full-width exception stretched the keyboard while `sm_port_map_touch` inverted a centred adaptive canvas. | One source canvas for the whole scene; inverse mapping and source mask lookup. | Every visible character and delete tested through real JNI mouse events at all six display cases. Live rectangle snapshots and captures checked. |
| Keyboard/background art changed proportions on widescreen. | Legacy per-axis 640×480 stretching and the full-screen bitmap exception. | Normalize the legacy boundary into source-art coordinates, then uniformly fit 480×320 to safe area; keep full-width quads inside that composition. | Captures at 16:9, 18:9, 19.5:9, 20:9 and wider; source-pixel key bounds share the captured transform. |
| Hit edges lost precision on fractional scales. | Integer mask conversion in Open and float-to-int conversion in keypad AI. | Parse source mask without integer canvas conversion; use original sampled mouse floats before AI truncation. | 720 reference/pixel round trips, boundary/gap checks and actual character entry at all tested scales. |
| Arrow input did not navigate; unsupported codes could become arbitrary keys. | Android only logged DPAD; original JNIKey used an uninitialized register for unsupported codes and dereferenced Game before checking it. | Forward handled input on GL queue; derive directional selection from visible source rectangles; guard unknown codes and uninitialized Game. | All four directions, controller activation, every hardware letter/digit and hardware Delete tested in the running game. |
| Gameplay prompt and disabled Submit/Cancel showed through the keyboard. | The shared BorderManager kept a surviving gameplay border active; border AI queues its label separately from Draw. The score actions were disabled but still drawn. | Collect actual border identities allocated by high-score Init; update/draw only that scene's borders during name entry. Suppress Submit/Cancel AI and Draw while the pad is open. | Open-pad/closed-pad captures; Submit still commits and exits after Return. |
| Blank side margins; widening the score backdrop directly produced diagonal holes and cropped ornaments. | HighScore animates backdrop UVs inward by 0.09; those zoomed UVs cannot define the main menu's three-piece boundaries. | Sample the authored 480×320 background using its column-major mesh coordinates in this scene, then apply the existing menu split. Preserve both ornaments and the open green-tube gap. Fit the interactive composition inside the frame interior to avoid the logo and border. | Uncropped captures and complete key/delete/confirm lifecycle at six display sizes, including S24+ dimensions and asymmetric safe insets. |

## Reproducible validation

```
cmake --build build-host-controls -j 6
python3 tests/nameentry/run.py
work/engineering/venv-linux/bin/python tests/nameentry/reference_keys.py
python tests/nameentry/check_captures.py
```

The second command compiles and exercises the actual C layout implementation,
then boots the AOT game, follows the Play/Postal Adventure/Deliver/start
sequence, loses the final life, enters every visible character individually,
deletes each, tests hardware inputs, enters `Snailmail9`, deletes/re-enters its
last digit, closes the keypad and presses Submit. The original name editor's
first-character capitalization is asserted. The test profile marks Tutorial
completed, sets zero extra lives and a qualifying score, and invokes the
original `Kill` routine. It **does not inject a Game Over/name-entry screen**.
These fixture commands exist only in the host executable.

Display cases: 1920×1080, 2160×1080, 2340×1080, 2400×1080, 3200×1080
and 3120×1440 (Galaxy S24+ landscape dimensions).
The 3200×1080 case uses asymmetric left/right safe insets (96/32 pixels). The
2400×1080 case closes with physical Enter; other cases use touch Return.
Pure transform tests also include 4:3, portrait and four-edge safe insets.
Reports, scripts, source screenshots, live rectangle snapshots and logs are
under `work/engineering/nameentry/validation/`. Original ARM32 instructions
in Unicorn separately verify all 40 mask records and scancodes plus the `!`
shift action (supported by original code, absent from the visible asset),
with zero mismatches. Unicorn is analysis-only and never shipped.
The capture check uses Pillow (available on the Windows host). It rejects
wide black sidebars or diagonal unfilled bands and verifies the selected
9-key's glow inside its live transformed hitbox at every display size.

The Android shared library is compiled with the local NDK
27.2.12479018 for arm64-v8a/API 23. The APK passes signature verification
and 16 KiB ELF/APK alignment checks.
The signed APK was installed in the Android 30 emulator, launched at
3120×1440, and reached Options through Android touch events. This is a
startup/input smoke check, not the complete Android name-entry acceptance test.

**Validation limit:** this is the native AOT game running on a host with
GLES1-on-GLES2/Mesa software rendering. No physical Android phone was tested. The
Android cutout callback, multitouch/cancellation and hardware-event dispatch
are compiled but the complete Android sequence is not device-validated.
Final device acceptance is still required.

## Development overlay

Launch the development APK with `--ez nameentryDebug true`, for example:

```
adb shell am start -n com.sandlotgames.snailmail.port.preview/com.sandlotgames.snailmail.SnailMailActivity --ez nameentryDebug true
```

The opt-in, non-interactive overlay draws cyan key/touch rectangles, a yellow
selected key, the gray non-interactive name field, logical key indices, and
the selected key's source/reference and final pixel coordinates. It reads a
locked snapshot produced by the game thread; the UI thread never reads game
memory. Normal launches create no overlay or polling timer.

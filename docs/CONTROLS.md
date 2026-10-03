# Android controls: original path, changes, and evidence

The reference is the user-supplied Android 1.00 APK, SHA-256
`0d10908d50f2a8361d9bbd3c6c9bff025434fdfb49c0f2b97254a793fb0b29e7`.
Its ARMv7 library has SHA-256
`e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466`.
The APK is a test input, not a source of development instructions. Addresses
below are virtual offsets in that library. The original APK and extracted
code remain in gitignored `original/` and `work/`.

## Input path recovered from the APK

| Stage | Original behavior and source |
|---|---|
| Sample | `AccelerometerListener.start()` selects the first `TYPE_ACCELEROMETER` and registers `SENSOR_DELAY_FASTEST` on the main looper; it never unregisters. `ADGLSurfaceView.onTouchEvent()` receives UI-thread touch callbacks. Original DEX methods were checked with `dexdump`. |
| Normalize | For every sensor callback of at least three values, the DEX evaluates `x*x+y*y+z*z` in **float**, square-roots in double, substitutes 1 for a zero norm, divides each component in double and casts to float. No Java deadzone or sensitivity factor exists. |
| Original filter | `last += (normalized-last)*0.3f` for each axis, initially zero, once per callback. The signs sent to JNI are `(-x,-y,+z)`. Its effective response varies with sensor callback frequency: the 90% response is about 108 ms at 60 Hz but 32 ms at 200 Hz. |
| Native entry | JNI at `0x142bc` calls `cAccelerometer::Input` (`0x4b8d8`) with `Game+0xbd4`; the routine stores the three floats unmodified. |
| Steering transform | Once per game update, `cAccelerometer::AI` (`0x4b400`) consumes those stored values. It applies the original tilt/touch configuration, orientation/angle math, state checks and turn changes. At `0x4b754` it can call `cRMouse::ClickScreen` (`0x20fe0`), and other branches update game/camera turn fields. No part of this native transform was replaced for Smooth mode. |
| Movement consumer | `cRMouse::AI` (`0x210a4`) runs in the game's update loop; the game's actor/camera updates and renders use the mouse and turn state. A render normally advances one game tick, so presentation cadence affects original movement speed. |
| Touch | Original `getAction()` recognizes only `DOWN`, `MOVE`, `UP` and passes `(0,1,2)` with surface-pixel X/Y to JNI at `0x14318`. JNI converts them to 640×480 logical coordinates using device width/height, then calls `cRMouse` methods. The port's existing `sm_port_map_touch` inversely maps adaptive screen fit before this original scaling. |

The original Java code performs no accelerometer deadzone, acceleration or
sensitivity scaling beyond normalization and the fixed low-pass. The native
game has its own orientation and state-dependent turn logic; this change does
not duplicate or bypass it. Touch and tilt both ultimately affect original
`cRMouse`/game state, so filtering is applied only before the tilt JNI entry.

## Issues and fixes

| What was happening → why → origin | Change | Verification |
|---|---|---|
| The port had already replaced the original 0.3-per-callback filter for all players → Legacy no longer matched the APK → `AccelerometerListener`. | `ControlFilter` carries the original float arithmetic in Legacy; Smooth is a separate branch. Legacy remains the default. | Independent DEX transcription in `ControlFilterTest` matched every float's raw bits for 200 Legacy samples, including a Smooth-to-Legacy switch. Native steering code is unchanged. |
| Tilt response differed by device sensor frequency → 0.3 was applied per callback, with `SENSOR_DELAY_FASTEST` providing no fixed rate → original listener. | Smooth applies an exponential low-pass using **sensor event timestamps**. The Smoothness slider maps 0 to bypass and 1–100 to a short time constant; a large target change shortens that constant for turns and recentering. No `dt` cap distorts slower sample rates. | Java replay at 60 and 200 samples/s showed less than 0.04 normalized-axis difference at 50 ms, high-Smoothness recenter residual below 0.025 after 120 ms, and high Smoothness attenuated alternating jitter by over 30% versus low Smoothness. These are numerical bounds, not a device feel claim. |
| A paused/resumed or invalid sensor stream could produce a stale ramp or non-finite Smooth steering → the prior port kept filter state across gaps and passed a malformed vector through → listener. | Smooth seeds at the current physical orientation on entry or a gap over 500 ms, rejects non-finite/near-zero vectors, and unregisters on pause so queued callbacks cannot accumulate. Legacy keeps the original calculation and lifecycle behavior. | `ControlFilterTest` covers gap reset, invalid sample rejection, zero Smoothness and recentering. |
| Sensor writes and the game update ran on different threads without synchronization → `JNIAccelerometer` writes state that the GL game loop reads → Android listener/native bridge. | Smooth marshals JNI calls through `GLSurfaceView.queueEvent`; a mode epoch discards stale queued samples after switching or pause. Legacy retains direct callbacks for original behavior. | Java source compilation and host game run passed; cross-thread behavior still requires an instrumented device run. |
| A canceled or multi-pointer gesture could leave touch pressed → original `getAction()` ignored `ACTION_CANCEL` and pointer-up → original `ADGLSurfaceView`. | Smooth tracks the primary pointer by ID, releases it on cancellation or its own pointer-up, and serializes touch events on the GL thread. Legacy retains the original `getAction()` switch. | Source-level action trace and Java compilation; Android touch injection is pending a device/emulator. |
| On a 75/90 Hz panel the old 14.7 ms minimum interval could select only every second vsync, slowing the game's render-tied tick to 37.5/45 Hz → `FramePacer`. | A deadline accumulator selects the appropriate fraction of vsyncs for an average of 60 updates/s. | `FrameScheduleTest` generated 600±1 ticks over ten virtual seconds at 60, 75, 90, 120, 144 and 200 Hz, plus uncapped mode. Real surface scheduling remains to be checked. |

## Menu and persistence

Options now has a **Controls** entry beside the existing Back and Display
buttons. Its page uses the game's own `cRBorder` button and slider, with
**Legacy Controls**, **Smooth Controls** and **Smoothness** labels. The slider
value is 0–100%, starts at 50%, and is retained when Legacy is selected.
`PortSettings` stores both values in the existing `port` SharedPreferences and
pushes them to the native menu at startup. Mode changes become visible to the
sensor listener immediately; pending Smooth samples are invalidated. The
original options' Tilt/Touch toggle and volume controls remain intact.

The original options-page slider flags and value fields (`+0x170`, `+0x174`)
were recovered from `cROptions::Init` at `0x59ee4`. Offscreen rendering of
the real game showed the Controls page, a Legacy-to-Smooth toggle and a slider
change from 50% to 70%. The page also opened through adapted touch mapping at
2400×1080. A 6,700-frame host run reached active tutorial movement with
scripted touch and accelerometer JNI inputs. Its 1,641,129-call GL trace was
byte-for-byte identical to the original ARM32 instructions run in the
analysis-only Unicorn reference. This establishes native gameplay parity for
that trace, separate from the Java-filter replay test; it does not establish
full Android/device equivalence.

The ARM64 preview APK was built locally with the supplied assets and passed
signature verification, arm64-only packaging and 16 KiB ELF/APK alignment
checks. The repository host suite passed all 22 tests, including original-APK
JNI shell parity, archive-in-APK and the Java control replay. The latter
covers Legacy direct dispatch, Smooth GL-thread queuing, stale sample
rejection, sensor lifecycle, numeric response and frame scheduling.

## Limits of current verification

### v0.1.6 landscape tilt corrections

* Stalled rendering could accumulate a Runnable for every sensor callback →
  `AccelerometerListener` queued all Smooth samples → replaced with one locked
  mailbox consumed by `ADRenderer` immediately before the native game update.
  Every sensor event still advances the timestamp-based filter. A 1,000-event
  stalled-frame replay delivers exactly one latest sample and queues no work.
* Duplicate or backwards sensor timestamps snapped the filter onto noisy raw
  input → the nonpositive-time branch treated these as a reset → Smooth now
  rejects these events without changing its clock. Pause/mode epochs reset
  Smooth history and discard pending input. Legacy arithmetic remains unchanged.
* Zero-length input was accepted as an orientation after the Legacy zero-norm
  fallback → Smooth shared normalization → its separate double-precision norm
  now rejects zero and non-finite gravity. Regression tests cover both cases.

The production Java replay rotates a gravity vector left/right through both
landscape orientations at 60, 100, 200 and 400 Hz. At maximum Smoothness, a
60-degrees/second ramp stays within 4 degrees of its target; after reversal it
turns monotonically within 60 ms and remains within the commanded range. A
center hold has less than 0.01-degree residual after 500 ms. Fixed-frequency
12/7-Hz noise loses more than 50% of its RMS amplitude. The independent original
DEX calculation still matches 200 Legacy samples bit for bit, including mode
switches. These are deterministic replay bounds, not physical phone measurements.

The earlier per-sample queue description below is superseded by the mailbox.

An Android 30 x86_64 emulator with ARM64 translation ran the NDK-built preview
APK. The main, Options and Controls menus rendered and accepted injected taps;
Smooth Controls at 70% persisted through a force-stop and restart. The tutorial
rendered while the accelerometer listener subscribed at a 10 ms sampling
period, and injected virtual tilt values were accepted. This is a functional
emulator smoke test, not a quantitative gameplay steering comparison.

There is no physical ARM64 device test or measured touch-to-photon latency,
sensor-noise trace, cancel/multi-touch run, GPU performance survey or original-
APK on-device comparison. Legacy deliberately retains original touch
limitations and its UI/GL thread interaction to preserve its exact input path.
Device acceptance should record sensor/touch traces in both modes, exercise
pause/resume and cancel/multi-touch, and compare steering and frame cadence
against the original APK.

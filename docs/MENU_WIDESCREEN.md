# Widescreen main-menu composition

`Backgrounds/MenuScreenHoriz.png` is a 512×512 texture whose landscape mesh
samples a 480×320 region. The source artwork combines the purple panel,
left stem and foot, rounded right edge, top tubes and Snail Mail logo. The
buttons and settings widgets are queued separately. The lower-right green
leaf is a separate queued element.

The original adaptive display path drew the whole menu texture on a centred
canvas with a dim stretched copy behind it. That left a narrow, complete menu
frame floating on a wide display. Uniformly widening that copy also distorted
its corners and logo. The fix is in `aot/port/port_display.c` at the original
`G0RenderBackdrop` hook. For `MenuScreenHoriz` on adaptive displays at 16:9
or wider, it uses the source texture and mesh in three passes:

1. The left artwork up to the diagonal beside the ornament stays at the
   original height-derived scale. The split is approximately source x=204 at
   the top and x=260 at the bottom, following the user-marked separation.
2. A narrow strip of the panel and background at that split fills the extra
   horizontal width. In the header it samples only clean dark pixels. This
   prevents the logo's green cap from smearing across the new space.
3. The complete right artwork moves outward unchanged in scale. The logo,
   green surround and rounded corner retain their proportions. The original
   open gap between the left green tube and logo surround remains open; no
   green tube segment is stretched or added.

The menu leaf follows the right screen margin while the interactive controls
stay centred in the wider panel. The same anchor is used when mapping touch
coordinates. Legacy display fit and non-menu backdrops retain their existing
render paths.

## Verification and limits

The rebuilt ARM64 library was packaged into an APK and passed APK signature
verification. The game was run in an Android 30 emulator at 16:9, 18:9 and
19.5:9 during iteration. Main, Options and Controls screens rendered, and
Options and Controls accepted touch input. A source-pixel check of the final
header fill found no green-dominant pixels in the sampled source area. The
final stain-removal change compiled and was packaged but was **not** rendered
again: the emulator was stopped at the user's request. The 20:9 live capture
and final visual acceptance therefore remain unverified. A physical ARM64
device and other baked backgrounds (such as the Help screen) have not been
visually validated by this change.

## Continuous background and native galaxy map (v0.1.5)

The old stretched centre strip also stretched the painted swirl beneath the
frame. Adaptive menus now draw one continuous, uniformly covered background
first. The frame is a separate transparent layer; its original RGB pixels are
retained, using the generated cutout only as an alpha matte. The original open
green-tube gap remains open and the intact logo remains right aligned. The
purple/red background with the widened swirl was selected by the user after
reviewing generated alternatives. It is a replacement bitmap, not a claim of
pixel-identical restoration of the original background.

Startup logos use generated widescreen artwork with uniform cover scaling.
Loading uses the same continuous menu background with original logo/bar art on
one uniformly scaled 480x320 canvas. Texture upload is deferred to the GL
thread; context recreation invalidates the old texture names and reuploads
retained bitmap data. Asset absence retains the original render fallback.

The galaxy selector uses the exact user-selected widescreen bitmap as one
full-screen texture. Uniform cover scaling crops only excess outer image area
for a different device aspect ratio. It does not replace the centre with the
original low-resolution map and does not draw a dimmed stretched duplicate.
Original game selection/navigation logic remains separate from the artwork.

Galaxy UI is laid out separately from its background. The title is left
anchored, the logo right anchored, Back left anchored, and the measured complete
navigation row centred. The measured detail widget is centred as a unit, its
text and Deliver button move with it, and the horizontal connector extends to
its new edge. Each displaced widget's measured origin is also registered for
the inverse touch transformation. Other menus and gameplay retain their
existing placement rules. After custom texture draws the original texture
binding cache is invalidated; otherwise a cached bind could display the wrong
texture on the next widget.

Verification for this revision: actual host game renders at 3120x1440 (Galaxy
S24+ dimensions) show the main/menu variants, startup screens, galaxy overview
and selected-route details. Additional 1280x720 (16:9) renders verified the new
Back button touch position returns to the original expected menu (screen 0).
The existing control regression suite passed, including 200 bit-exact Legacy
samples and rate/filter/lifecycle checks. Java and native fresh-install defaults
remain Legacy; saved player choices are retained. The signed v0.1.5 APK passes
signature and 16 KiB alignment validation and installs over v0.1.4 in the local
Android emulator. Physical Galaxy S24+ installation/rendering is unverified.
The preview captures are host game renders, not screenshots from that phone.

Final background choice: the user-supplied galaxy-map PNG is copied byte for
byte into `assets/port/galaxy-map-wide.png`, including in the final APK. Final
3120x1440 and 1280x720 game renders use that exact image; Back touch was checked
again at 1280x720 after the replacement and returned to screen 0. The update
installed and launched in the local Android emulator without an AndroidRuntime
error in the checked log. Version 0.1.5 (code 9) was uploaded to the existing
Drive download. It keeps the v0.1.4 release application ID and signing key.

## v0.1.6 galaxy panel layout

Each of the ten original descriptions is centred horizontally and vertically
as one measured widget. Text, frame, Deliver button and inverse touch mapping
share the same translation. Independent Galaxy/Line and Galaxy/Level map
primitives are excluded from widget grouping, while BorderSpaceMap is retained.
Previously these overlapping map primitives could enlarge the measured widget
and displace its centre. Connector endpoints now follow the moved panel;
route nodes remain attached to the background map.

`tests/ui/run.py` exercises all ten descriptions and the original Back sequence
at 16:9, 18:9, 19.5:9, 20:9, 8:3, and 3120x1440. It checks transformed widget
bounds, uniform scaling, panel centres and the expected return screen. Its
host-only temporary profile exposes each route without modifying Android saves.
Screenshots are full-frame game renders, not physical-phone captures.

# Release installation

The v0.1.3 preview APK (package `com.sandlotgames.snailmail.port.preview`,
version code 7) was rejected with the generic "App was not installed" on
the owner's Galaxy S24+, where an older port was already installed.

The uploaded Drive bytes match the local SHA-256 exactly. Local APK ZIP,
signature (API 23–36), 16 KiB ELF/APK alignment, and Android 30 emulator
update-installation checks pass. The phone's actual package-manager error
is unavailable. A signing-key/version conflict is a hypothesis, not a
confirmed diagnosis. No malformed APK defect has been demonstrated.

v0.1.4 uses the stable release package
`com.sandlotgames.snailmail.port.release` and launcher label **Snail Mail ARM64**.
This permits installation alongside the existing preview/original game and
avoids their signing-key and version-code update collisions. It does not
delete either existing installation or migrate its app-private saves.
The release starts with separate saves/settings. Future releases must keep
this package ID and signing key and increase the version code.
The release manifest does not enable Android's debuggable flag.

The local release builder is `work/engineering/build-release.ps1`, with
explicit Version, VersionCode and AppId parameters. The signing key is the
existing private local keystore; it is never committed or uploaded.

This compatibility build does not bypass phone security restrictions. If
it is still rejected, collect the exact Android package-manager error from
the phone via USB debugging before changing signing, SDK, or manifest rules.
Do not uninstall the older port as a diagnostic step: that may erase saves.

Installation on the Galaxy S24+ still requires confirmation from the owner.

## Reproducible v0.1.6 release

The checked-in Windows release builder is
`tools/android_build/build-release.ps1 -Version 0.1.6 -VersionCode 10`.
It requires the local Android SDK/NDK and JDK, original owner-supplied extracted
assets and regenerated AOT output. `prepare_background_art.py` extracts the two
original menu/loading PNGs locally; these original images remain gitignored.
The supplemental widescreen art and masks are included in the repository.
The private local signing key must be retained for updates and never committed.
Version 0.1.6 retains the release application ID and key, has version code 10,
and passes signature, ARM64 ELF and 16 KiB package alignment checks.

param(
    [string]$Version = '0.1.6',
    [int]$VersionCode = 10,
    [string]$AppId = 'com.sandlotgames.snailmail.port.release'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Set-Location -LiteralPath $root
$version = $Version
$code = $VersionCode
if ($version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or $code -lt 1 -or $AppId -notmatch '^[a-zA-Z_][a-zA-Z_0-9]*(\.[a-zA-Z_][a-zA-Z_0-9]*)+$') { throw 'Invalid release identity' }
$out = Join-Path $root ('work/releases/v' + $version)
New-Item -ItemType Directory -Force -Path $out,(Join-Path $out 'java'),(Join-Path $out 'classes'),(Join-Path $out 'dex') | Out-Null
$jdk = 'C:/Program Files/Eclipse Adoptium/jdk-21.0.10.7-hotspot'
$env:JAVA_HOME = $jdk
$sdk = 'C:/AndroidSDK'
$bt = Join-Path $sdk 'build-tools/36.1.0'
$jar = Join-Path $sdk 'platforms/android-33/android.jar'
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Build command failed: $LASTEXITCODE" } }
python tools/android_build/prepare_background_art.py
Check-Exit
& (Join-Path $PSScriptRoot 'ndk-build.ps1')
Copy-Item -LiteralPath (Get-ChildItem android/app/src/main/java/com/sandlotgames/snailmail/*.java).FullName -Destination (Join-Path $out 'java')
$view = Join-Path $out 'java/ADGLSurfaceView.java'
$text = [IO.File]::ReadAllText($view)
$anchor = '        setRenderer(this.mRenderer);'
if (-not $text.Contains($anchor)) { throw 'Missing GLES context anchor' }
$text = $text.Replace($anchor, "        setEGLContextClientVersion(2);`n" + $anchor)
[IO.File]::WriteAllText($view, $text)
$sources = (Get-ChildItem (Join-Path $out 'java/*.java')).FullName
& "$jdk/bin/javac.exe" -nowarn -source 8 -target 8 -bootclasspath $jar -Xlint:-options -d (Join-Path $out 'classes') @sources
Check-Exit
& "$jdk/bin/jar.exe" cf (Join-Path $out 'classes.jar') -C (Join-Path $out 'classes') .
Check-Exit
& "$bt/d8.bat" --min-api 23 --lib $jar --output (Join-Path $out 'dex') (Join-Path $out 'classes.jar')
Check-Exit
$manifest = [IO.File]::ReadAllText((Join-Path $root 'android/app/src/main/AndroidManifest.xml'))
$manifest = $manifest.Replace('<manifest xmlns:android="http://schemas.android.com/apk/res/android"', ('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.sandlotgames.snailmail" android:versionCode="' + $code + '" android:versionName="' + $version + '"'))
$manifest = $manifest.Replace('android:glEsVersion="0x00010001"','android:glEsVersion="0x00020000"')
$manifest = $manifest.Replace('android:label="@string/app_name"','android:label="Snail Mail ARM64"')
[IO.File]::WriteAllText((Join-Path $out 'AndroidManifest.xml'), $manifest)
& "$bt/aapt.exe" package -f --min-sdk-version 23 --target-sdk-version 35 --rename-manifest-package $AppId -M (Join-Path $out 'AndroidManifest.xml') -S work/android_build/app/res -S android/app/src/main/res -A work/apk_unzip/assets -A android/app/src/main/assets -I $jar -0 arsc -0 mp3 -0 ogg -F (Join-Path $out 'unsigned.apk')
Check-Exit
@'
import zipfile
from pathlib import Path
import sys
p=Path(sys.argv[1])
with zipfile.ZipFile(p/'unsigned.apk','a') as z:
    z.write(p/'dex/classes.dex','classes.dex',compress_type=zipfile.ZIP_DEFLATED)
    z.write('work/android_build/ndk-out/libsnailmail.so','lib/arm64-v8a/libsnailmail.so',compress_type=zipfile.ZIP_STORED)
'@ | python - $out
Check-Exit
& "$bt/zipalign.exe" -P 16 -f 4 (Join-Path $out 'unsigned.apk') (Join-Path $out 'aligned.apk')
Check-Exit
$apk = Join-Path $out ('SnailMail-ARM64-v' + $version + '.apk')
& "$bt/apksigner.bat" sign --ks work/android_build/keys/debug.keystore --ks-pass pass:android --out $apk (Join-Path $out 'aligned.apk')
Check-Exit
& "$bt/apksigner.bat" verify --verbose $apk
Check-Exit
& "$bt/zipalign.exe" -c -P 16 4 $apk
Check-Exit
python tools/validation/platform/check_elf_alignment.py $apk
Check-Exit
& "$bt/aapt.exe" dump badging $apk | Select-Object -First 2
Check-Exit
Get-FileHash -LiteralPath $apk -Algorithm SHA256 | Format-List

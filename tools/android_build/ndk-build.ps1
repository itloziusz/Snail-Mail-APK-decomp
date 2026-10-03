$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$clang = 'C:\AndroidSDK\ndk\27.2.12479018\toolchains\llvm\prebuilt\windows-x86_64\bin\clang.exe'
$out = Join-Path $root 'work/android_build/ndk-out'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$sources = @(
    'aot/runtime/aot_core.c', 'aot/runtime/aot_libc.c',
    'aot/runtime/aot_gl.c', 'aot/runtime/aot_jni.c',
    'aot/runtime/aot_android.c', 'aot/port/port_display.c',
    'aot/port/port_menu.c', 'aot/port/port_android.c',
    'aot/port/port_nameentry.c', 'aot/port/nameentry_layout.c',
    'aot/generated/aot_table.c',
    'reconstructed/rendering/src/smgl.c',
    'reconstructed/rendering/src/smgl_math.c'
)
$sources += Get-ChildItem (Join-Path $root 'aot/generated/aot_funcs_*.c') | ForEach-Object { $_.FullName }
$common = @('--target=aarch64-linux-android23', '-fPIC', '-O2',
    '-ffp-contract=off', '-funsigned-char', '-fwrapv', '-fno-strict-aliasing',
    '-DSM_GL_EMULATE_GLES1',
    ('-I' + (Join-Path $root 'aot/runtime')),
    ('-I' + (Join-Path $root 'aot/generated')),
    ('-I' + (Join-Path $root 'aot/port')),
    ('-I' + (Join-Path $root 'reconstructed/rendering/include')))
$objects = @()
for ($i = 0; $i -lt $sources.Count; $i++) {
    $src = if ([System.IO.Path]::IsPathRooted($sources[$i])) { $sources[$i] } else { Join-Path $root $sources[$i] }
    $obj = Join-Path $out ([System.IO.Path]::GetFileNameWithoutExtension($src) + '.o')
    $extra = @()
    if ($src -like '*aot_funcs_*') { $extra = @('-w', '-g0') }
    if ($src -like '*port_display.c') { $extra += '-DSM_PORT_DEBUG_BACKDROP' }
    if ((Test-Path $obj) -and (Get-Item $obj).LastWriteTime -ge (Get-Item $src).LastWriteTime) {
        $objects += $obj
        continue
    }
    Write-Host ('[{0}/{1}] {2}' -f ($i+1), $sources.Count, $src)
    & $clang @common @extra -c $src -o $obj
    if ($LASTEXITCODE -ne 0) { throw "NDK compilation failed: $src" }
    $objects += $obj
}
$lib = Join-Path $out 'libsnailmail.so'
& $clang --target=aarch64-linux-android23 -shared '-Wl,-soname,libsnailmail.so' '-Wl,-z,max-page-size=16384' '-Wl,-z,noexecstack' '-Wl,-z,relro,-z,now' '-Wl,--no-undefined' -o $lib @objects -lGLESv2 -llog -landroid -lm
if ($LASTEXITCODE -ne 0) { throw 'NDK link failed' }
Write-Host "NDK library: $lib"

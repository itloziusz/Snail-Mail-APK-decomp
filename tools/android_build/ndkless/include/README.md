Minimal libc declarations for building arm64 Android code WITHOUT the NDK
(dl.google.com is unreachable from the analysis machine). They declare only
standard functions with LP64 bionic signatures; stddef.h/stdint.h/stdbool.h/
stdarg.h/limits.h/float.h come from clang's own resource directory. A function
not declared here is a compile error, never a silent mismatch. Replace this
whole directory with the NDK sysroot as soon as the pinned NDK is available.

GLES headers: sm_gles1_decls.h (the 45 GLES 1.x imports, aot variant) and
GLES2/gl2.h (only the GLES 2.0 subset used by reconstructed/rendering/src/smgl.c,
aot-gles2 variant; values and prototypes copied verbatim from Khronos gl2.h).

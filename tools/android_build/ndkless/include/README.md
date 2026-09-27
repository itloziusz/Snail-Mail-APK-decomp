Minimal libc declarations for building arm64 Android code WITHOUT the NDK
(dl.google.com is unreachable from the analysis machine). They declare only
standard functions with LP64 bionic signatures; stddef.h/stdint.h/stdbool.h/
stdarg.h/limits.h/float.h come from clang's own resource directory. A function
not declared here is a compile error, never a silent mismatch. Replace this
whole directory with the NDK sysroot as soon as the pinned NDK is available.

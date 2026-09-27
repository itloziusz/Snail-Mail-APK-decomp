/*
 * Emulation of the Java side the native game talks to (ADRenderer callbacks,
 * java.io.FileDescriptor), for running the translated game on a Linux host.
 * Semantics follow the original shell (docs/APK_AUDIT.md §9-11, smali-verified).
 */
#ifndef SM_HOST_JAVA_EMUL_H
#define SM_HOST_JAVA_EMUL_H

#include <stdint.h>

#include "aot_host.h"

typedef struct sm_java_emul_cfg {
    const char *assets_dir;   /* extracted APK assets/ (for .ogg existence checks) */
    const char *files_dir;    /* app-private files (saves) */
    int virtual_clock;        /* 1: JAVATime advances only via sm_java_clock_advance */
    int verbose;
} sm_java_emul_cfg;

const aot_java_ops *sm_java_emul_ops(const sm_java_emul_cfg *cfg);
void *sm_java_emul_renderer(void);          /* the ADRenderer instance ("thiz") */
void *sm_java_emul_class(const char *name); /* a class object ("clazz" for static natives) */
void *sm_java_emul_file_descriptor(int fd);
void sm_java_clock_advance(uint64_t ns);
uint64_t sm_java_clock_now(void);

typedef struct sm_audio_stats {
    int samples_loaded, samples_missing, sample_plays, music_starts, vibrations;
    char last_music[64];
} sm_audio_stats;
const sm_audio_stats *sm_java_emul_audio_stats(void);

#endif

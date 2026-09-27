#ifndef SM_PLATFORM_DAT_H
#define SM_PLATFORM_DAT_H
/*
 * Native side of JNIDatInit (v7a:0x15244-0x15450, sha256 e43bc913...a466):
 * the game's single open handle on the asset archive assets/asm.mp3.
 *
 * Original state (docs/ASSET_FORMATS.md section 3, docs/BOOT_CHAIN.md section 1):
 *   gJavaAssetFid    dup() of the AssetFileDescriptor's fd
 *   gJavaAssetStart  archive start offset inside the APK
 *   gJavaAssetLength archive length
 *   gDatFP           fdopen(gJavaAssetFid, "rb"); stays open for the process
 *                    lifetime (JNIDatUnInit v7a:0x13c70 does nothing)
 *   gDat / gDatHash  directory block [0, dir_size) and its cRHash index
 *
 * Port representation: the fd is read with pread() at start + offset, so the
 * reader keeps no shared file position (the original's fseek+fread pairs on
 * gDatFP are replaced by positioned reads with the same byte ranges). The
 * directory is parsed by sm_assets without the original's in-place 32-bit
 * pointer fix-up.
 */
#include <stddef.h>
#include <stdint.h>

#include "sm_assets/asm_archive.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum sm_dat_status {
    SM_DAT_OK = 0,
    SM_DAT_ERR_ARG = 1,          /* bad fd, negative start/length, NULL */
    SM_DAT_ERR_IO = 2,           /* read failed or returned short */
    SM_DAT_ERR_DIRECTORY = 3,    /* sm_assets rejected the directory (see asm_status) */
    SM_DAT_ERR_DATA_RANGE = 4,   /* a record's data span lies outside the archive */
    SM_DAT_ERR_NO_MEMORY = 5,
    SM_DAT_ERR_NOT_OPEN = 6
} sm_dat_status;

typedef struct sm_dat {
    int fd;                  /* owned; -1 when closed */
    uint64_t start;          /* gJavaAssetStart */
    uint64_t length;         /* gJavaAssetLength */
    sm_asm_directory dir;    /* gDat + gDatHash */
    sm_asm_status asm_status;/* detail when SM_DAT_ERR_DIRECTORY */
    uint32_t bad_index;      /* record index for directory/data-range errors */
} sm_dat;

const char *sm_dat_status_str(sm_dat_status st);

/* Initialise to the closed state. */
void sm_dat_init(sm_dat *dat);

/*
 * Take ownership of `owned_fd` (already a private dup, as gJavaAssetFid was)
 * and load the directory of the archive stored at [start, start+length).
 * On failure the fd is closed and `dat` is left closed.
 */
sm_dat_status sm_dat_open(sm_dat *dat, int owned_fd, int64_t start, int64_t length);

/* Positioned read of archive bytes [offset, offset+len) (archive-relative). */
sm_dat_status sm_dat_read(const sm_dat *dat, uint64_t offset, void *buf, size_t len);

/* Name lookup with the original cRHash::Search semantics. */
const sm_asm_entry *sm_dat_find(const sm_dat *dat, const char *name);

void sm_dat_close(sm_dat *dat);

#ifdef __cplusplus
}
#endif

#endif /* SM_PLATFORM_DAT_H */

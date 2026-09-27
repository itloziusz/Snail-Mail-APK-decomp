/*
 * Native side of JNIDatInit, reconstructed from v7a:0x15244-0x15450
 * (sha256 e43bc913...a466). See include/sm_platform/dat.h for the mapping of
 * original globals to fields and docs/ASSET_FORMATS.md section 3 for evidence.
 */
#define _POSIX_C_SOURCE 200809L /* pread, mkstemp under strict C11 */
#define _FILE_OFFSET_BITS 64
#include "sm_platform/dat.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

const char *sm_dat_status_str(sm_dat_status st)
{
    switch (st) {
    case SM_DAT_OK: return "ok";
    case SM_DAT_ERR_ARG: return "invalid argument";
    case SM_DAT_ERR_IO: return "read failed";
    case SM_DAT_ERR_DIRECTORY: return "directory rejected";
    case SM_DAT_ERR_DATA_RANGE: return "record data outside archive";
    case SM_DAT_ERR_NO_MEMORY: return "out of memory";
    case SM_DAT_ERR_NOT_OPEN: return "archive not open";
    }
    return "unknown";
}

void sm_dat_init(sm_dat *dat)
{
    if (dat == NULL) {
        return;
    }
    memset(dat, 0, sizeof *dat);
    dat->fd = -1;
}

static sm_dat_status read_exact(int fd, uint64_t pos, void *buf, size_t len)
{
    uint8_t *p = (uint8_t *)buf;
    while (len > 0) {
        ssize_t n;
        if (pos > (uint64_t)INT64_MAX) {
            return SM_DAT_ERR_ARG;
        }
        n = pread(fd, p, len, (off_t)pos);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n <= 0) {
            return SM_DAT_ERR_IO;
        }
        p += n;
        pos += (uint64_t)n;
        len -= (size_t)n;
    }
    return SM_DAT_OK;
}

sm_dat_status sm_dat_read(const sm_dat *dat, uint64_t offset, void *buf, size_t len)
{
    if (dat == NULL || buf == NULL) {
        return SM_DAT_ERR_ARG;
    }
    if (dat->fd < 0) {
        return SM_DAT_ERR_NOT_OPEN;
    }
    if (offset > dat->length || len > dat->length - offset) {
        return SM_DAT_ERR_DATA_RANGE;
    }
    return read_exact(dat->fd, dat->start + offset, buf, len);
}

static void fail_close(sm_dat *dat, int fd)
{
    sm_asm_directory_free(&dat->dir);
    close(fd);
    dat->fd = -1;
}

/*
 * Original sequence (v7a):
 *   0x15300-0x15320  store gJavaAssetStart/Length/Fid
 *   0x15324          gDatFP = fdopen(fid, "rb")
 *   0x1534c-0x15368  fseek(fp, start, SEEK_SET); fread(probe, 1, 244, fp)
 *   0x1536c-0x15380  dir_size = probe.u32[2]; gDat = RShellMemoryMalloc(dir_size)
 *   0x15394-0x153ac  fseek(fp, start, SEEK_SET); fread(gDat, 1, dir_size, fp)
 *   0x153b0-0x15414  cRHash::Init(count, DatHashGetString); Add(name_i, i) for i < count
 * The 244-byte probe only serves to learn dir_size; reading its first 12 bytes
 * yields the same value (SM_ASM_DIR_SIZE_OFFSET). The original checks none of
 * the fread results; the port validates every size against the archive
 * length before trusting it.
 */
sm_dat_status sm_dat_open(sm_dat *dat, int owned_fd, int64_t start, int64_t length)
{
    uint8_t head[SM_ASM_MIN_HEADER];
    uint8_t *block;
    uint32_t dir_size;
    sm_dat_status st;
    sm_asm_status ast;

    if (dat == NULL || owned_fd < 0 || start < 0 || length < 0) {
        if (owned_fd >= 0) {
            close(owned_fd);
        }
        return SM_DAT_ERR_ARG;
    }
    sm_dat_init(dat);
    dat->fd = owned_fd;
    dat->start = (uint64_t)start;
    dat->length = (uint64_t)length;

    if (dat->length < SM_ASM_MIN_HEADER) {
        dat->asm_status = SM_ASM_ERR_TRUNCATED_HEADER;
        fail_close(dat, owned_fd);
        return SM_DAT_ERR_DIRECTORY;
    }
    st = read_exact(owned_fd, dat->start, head, sizeof head);
    if (st != SM_DAT_OK) {
        fail_close(dat, owned_fd);
        return st;
    }
    dir_size = (uint32_t)head[SM_ASM_DIR_SIZE_OFFSET] |
               ((uint32_t)head[SM_ASM_DIR_SIZE_OFFSET + 1] << 8) |
               ((uint32_t)head[SM_ASM_DIR_SIZE_OFFSET + 2] << 16) |
               ((uint32_t)head[SM_ASM_DIR_SIZE_OFFSET + 3] << 24);
    if (dir_size == 0 || (uint64_t)dir_size > dat->length) { /* lower bound: sm_assets */
        dat->asm_status = SM_ASM_ERR_DIR_SIZE_RANGE;
        fail_close(dat, owned_fd);
        return SM_DAT_ERR_DIRECTORY;
    }
    block = (uint8_t *)malloc(dir_size);
    if (block == NULL) {
        fail_close(dat, owned_fd);
        return SM_DAT_ERR_NO_MEMORY;
    }
    st = read_exact(owned_fd, dat->start, block, dir_size);
    if (st != SM_DAT_OK) {
        free(block);
        fail_close(dat, owned_fd);
        return st;
    }
    ast = sm_asm_directory_parse(block, dir_size, &dat->dir, &dat->bad_index);
    free(block); /* sm_assets keeps its own copy of the directory bytes */
    if (ast == SM_ASM_OK) {
        ast = sm_asm_directory_build_index(&dat->dir);
    }
    if (ast != SM_ASM_OK) {
        dat->asm_status = ast;
        fail_close(dat, owned_fd);
        return ast == SM_ASM_ERR_NO_MEMORY ? SM_DAT_ERR_NO_MEMORY : SM_DAT_ERR_DIRECTORY;
    }
    ast = sm_asm_directory_validate_data(&dat->dir, dat->length, &dat->bad_index);
    if (ast != SM_ASM_OK) {
        dat->asm_status = ast;
        fail_close(dat, owned_fd);
        return SM_DAT_ERR_DATA_RANGE;
    }
    return SM_DAT_OK;
}

const sm_asm_entry *sm_dat_find(const sm_dat *dat, const char *name)
{
    if (dat == NULL || dat->fd < 0) {
        return NULL;
    }
    return sm_asm_directory_find(&dat->dir, name);
}

void sm_dat_close(sm_dat *dat)
{
    if (dat == NULL) {
        return;
    }
    sm_asm_directory_free(&dat->dir);
    if (dat->fd >= 0) {
        close(dat->fd);
    }
    sm_dat_init(dat);
}

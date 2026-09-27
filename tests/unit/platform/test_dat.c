/*
 * Unit tests for sm_platform/dat (native side of JNIDatInit, v7a:0x15244).
 *
 *   synthetic  the committed synthetic fixture, embedded at a non-zero start
 *              offset inside a larger file (as asm.mp3 sits inside the APK)
 *   real       assets/asm.mp3 read from INSIDE the original APK at its zip data
 *              offset (exactly what AssetManager.openFd hands to JNIDatInit on a
 *              device), cross-checked against the standalone extracted copy.
 *              Exit 77 (SKIP) when the gitignored inputs are absent.
 */
#define _POSIX_C_SOURCE 200809L /* pread, mkstemp under strict C11 */
#define _FILE_OFFSET_BITS 64
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "sm_platform/dat.h"
#include "sm_test.h"

static uint8_t *read_file(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    uint8_t *buf;
    long n;
    if (f == NULL) {
        return NULL;
    }
    if (fseek(f, 0, SEEK_END) != 0 || (n = ftell(f)) < 0 || fseek(f, 0, SEEK_SET) != 0) {
        fclose(f);
        return NULL;
    }
    buf = (uint8_t *)malloc((size_t)n + 1);
    if (buf != NULL && fread(buf, 1, (size_t)n, f) != (size_t)n) {
        free(buf);
        buf = NULL;
    }
    fclose(f);
    *len = (size_t)n;
    return buf;
}

static uint32_t le32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }
static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }

/* Write `prefix` junk bytes then `data` into a temp file; return an fd at 0. */
static int make_embedded(const uint8_t *data, size_t len, size_t prefix, size_t suffix)
{
    char path[] = "/tmp/sm_dat_testXXXXXX";
    int fd = mkstemp(path);
    size_t i;
    if (fd < 0) {
        return -1;
    }
    unlink(path);
    for (i = 0; i < prefix; ++i) {
        uint8_t b = (uint8_t)(0xA5 ^ i);
        if (write(fd, &b, 1) != 1) return -1;
    }
    if (write(fd, data, len) != (ssize_t)len) return -1;
    for (i = 0; i < suffix; ++i) {
        uint8_t b = 0xEE;
        if (write(fd, &b, 1) != 1) return -1;
    }
    return fd;
}

static int test_synthetic(void)
{
    size_t len = 0;
    uint8_t *arch = read_file(SM_FIXTURE_DIR "/synthetic_small.asm", &len);
    const size_t prefix = 0x1235; /* same odd start offset as the differential test */
    sm_asm_directory ref;
    uint32_t bad = 0;
    sm_dat dat;
    uint32_t i;
    int fd;

    CHECK(arch != NULL);
    if (arch == NULL) return sm_test_finish("platform.dat_synthetic");
    CHECK_EQ_INT(sm_asm_directory_parse(arch, le32(arch + 8), &ref, &bad), SM_ASM_OK);
    CHECK_EQ_INT(sm_asm_directory_build_index(&ref), SM_ASM_OK);

    fd = make_embedded(arch, len, prefix, 64);
    CHECK(fd >= 0);
    CHECK_EQ_INT(sm_dat_open(&dat, fd, (int64_t)prefix, (int64_t)len), SM_DAT_OK);
    CHECK_EQ_INT(dat.dir.count, ref.count);
    for (i = 0; i < ref.count; ++i) {
        const sm_asm_entry *e = &ref.entries[i];
        const sm_asm_entry *got = sm_dat_find(&dat, e->name);
        /* Lookup must agree with the reference-validated sm_assets index,
         * including "lowest index wins" for case-insensitive duplicates. */
        CHECK(got != NULL);
        if (got != NULL) {
            CHECK_EQ_INT(got - dat.dir.entries, sm_asm_directory_find_index(&ref, e->name));
        }
        CHECK_EQ_INT(dat.dir.entries[i].raw.data_offset, e->raw.data_offset);
    }
    /* DATA/README.TXT (#0) shadows data/readme.txt (#4). */
    CHECK_EQ_INT(sm_dat_find(&dat, "data/readme.txt") - dat.dir.entries, 0);
    CHECK(sm_dat_find(&dat, "NOT/THERE") == NULL);

    /* Positioned reads return archive-relative bytes, never the prefix. */
    {
        uint8_t buf[16];
        const sm_asm_entry *e = &dat.dir.entries[0];
        CHECK_EQ_INT(sm_dat_read(&dat, e->raw.data_offset, buf, 16), SM_DAT_OK);
        CHECK(memcmp(buf, arch + e->raw.data_offset, 16) == 0);
        CHECK_EQ_INT(sm_dat_read(&dat, 0, buf, 4), SM_DAT_OK);
        CHECK(memcmp(buf, arch, 4) == 0);
        CHECK_EQ_INT(sm_dat_read(&dat, len - 2, buf, 4), SM_DAT_ERR_DATA_RANGE);
        CHECK_EQ_INT(sm_dat_read(&dat, (uint64_t)len + 1, buf, 0), SM_DAT_ERR_DATA_RANGE);
    }
    sm_dat_close(&dat);
    CHECK_EQ_INT(dat.fd, -1);
    CHECK(sm_dat_find(&dat, "DATA/README.TXT") == NULL);
    {
        uint8_t b;
        CHECK_EQ_INT(sm_dat_read(&dat, 0, &b, 1), SM_DAT_ERR_NOT_OPEN);
    }

    /* Malformed / hostile inputs: each must fail cleanly and close the fd. */
    fd = make_embedded(arch, len, prefix, 0);
    CHECK_EQ_INT(sm_dat_open(&dat, fd, (int64_t)prefix, 8), SM_DAT_ERR_DIRECTORY);
    CHECK_EQ_INT(dat.asm_status, SM_ASM_ERR_TRUNCATED_HEADER);
    CHECK(fcntl(fd, F_GETFD) == -1); /* closed */

    fd = make_embedded(arch, len, prefix, 0);
    CHECK_EQ_INT(sm_dat_open(&dat, fd, (int64_t)prefix, (int64_t)le32(arch + 8) - 1), SM_DAT_ERR_DIRECTORY);
    CHECK_EQ_INT(dat.asm_status, SM_ASM_ERR_DIR_SIZE_RANGE);

    /* Range checks follow what the game actually READS (sm_asm_entry_read_span):
     * the last fixture record is raw with decoded_size 2 < stored_size 5, and
     * raw records read decoded_size bytes (EV-ASSET-0017). Dropping bytes past
     * that span is harmless; cutting into it must be rejected. */
    {
        uint32_t span_off = 0, span_len = 0;
        const sm_asm_entry *last = &ref.entries[ref.count - 1];
        CHECK_EQ_INT(sm_asm_entry_read_span(last, &span_off, &span_len), SM_ASM_OK);
        CHECK_EQ_INT((uint64_t)span_off + span_len, len - 3);
        fd = make_embedded(arch, len, prefix, 0);
        CHECK_EQ_INT(sm_dat_open(&dat, fd, (int64_t)prefix, (int64_t)span_off + span_len), SM_DAT_OK);
        sm_dat_close(&dat);
        fd = make_embedded(arch, len, prefix, 0);
        CHECK_EQ_INT(sm_dat_open(&dat, fd, (int64_t)prefix, (int64_t)span_off + span_len - 1),
                     SM_DAT_ERR_DATA_RANGE);
        CHECK_EQ_INT(dat.bad_index, ref.count - 1);
    }

    fd = make_embedded(arch, len, prefix, 0);
    CHECK_EQ_INT(sm_dat_open(&dat, fd, (int64_t)(prefix + len + 100), (int64_t)len), SM_DAT_ERR_IO);

    fd = make_embedded(arch, len, prefix, 0);
    CHECK_EQ_INT(sm_dat_open(&dat, fd, -1, (int64_t)len), SM_DAT_ERR_ARG);
    CHECK(fcntl(fd, F_GETFD) == -1);
    CHECK_EQ_INT(sm_dat_open(&dat, -1, 0, 0), SM_DAT_ERR_ARG);

    sm_asm_directory_free(&ref);
    free(arch);
    return sm_test_finish("platform.dat_synthetic");
}

/* Locate a STORED zip entry's data via the central directory. */
static int zip_find_stored(const uint8_t *z, size_t len, const char *name, uint64_t *off, uint64_t *size)
{
    size_t i, nlen = strlen(name);
    uint32_t cd_off, cd_n, p;
    if (len < 22) return 0;
    for (i = len - 22;; --i) {
        if (le32(z + i) == 0x06054b50u) break;
        if (i == 0 || len - i > 65557) return 0;
    }
    cd_n = le16(z + i + 10);
    cd_off = le32(z + i + 16);
    p = cd_off;
    while (cd_n-- > 0 && (size_t)p + 46 <= len && le32(z + p) == 0x02014b50u) {
        uint16_t method = le16(z + p + 10), fl = le16(z + p + 28), xl = le16(z + p + 30), cl = le16(z + p + 32);
        uint32_t csize = le32(z + p + 20), lho = le32(z + p + 42);
        if (fl == nlen && (size_t)p + 46 + fl <= len && memcmp(z + p + 46, name, nlen) == 0) {
            if (method != 0 || (size_t)lho + 30 > len) return 0;
            *off = (uint64_t)lho + 30 + le16(z + lho + 26) + le16(z + lho + 28);
            *size = csize;
            return 1;
        }
        p += 46u + fl + xl + cl;
    }
    return 0;
}

static int test_real(void)
{
    const char *apk_path = SM_REPO_ROOT "/original/com.sandlotgames.snailmail-1.00.apk";
    const char *asm_path = SM_REPO_ROOT "/work/apk_unzip/assets/asm.mp3";
    size_t apk_len = 0, asm_len = 0;
    uint8_t *apk = read_file(apk_path, &apk_len);
    uint8_t *asmb = read_file(asm_path, &asm_len);
    uint64_t off = 0, size = 0;
    sm_dat in_apk, standalone;
    uint32_t i, self = 0, shadowed = 0;

    if (apk == NULL || asmb == NULL) {
        printf("SKIP: need %s and %s (tools/inventory/setup_workspace.sh)\n", apk_path, asm_path);
        free(apk);
        free(asmb);
        return 77;
    }
    CHECK(zip_find_stored(apk, apk_len, "assets/asm.mp3", &off, &size));
    CHECK_EQ_INT(size, asm_len);
    CHECK(off + size <= apk_len && memcmp(apk + off, asmb, asm_len) == 0);

    /* On device: openFd("asm.mp3") -> (fd of the APK, start = off, length = size). */
    CHECK_EQ_INT(sm_dat_open(&in_apk, open(apk_path, O_RDONLY), (int64_t)off, (int64_t)size), SM_DAT_OK);
    CHECK_EQ_INT(sm_dat_open(&standalone, open(asm_path, O_RDONLY), 0, (int64_t)asm_len), SM_DAT_OK);
    CHECK_EQ_INT(in_apk.dir.count, standalone.dir.count);
    CHECK_EQ_INT(in_apk.dir.dir_size, standalone.dir.dir_size);
    /* Observed values for archive sha256 59740ec3... (not tuning targets). */
    CHECK_EQ_INT(in_apk.dir.count, 734);
    CHECK_EQ_INT(in_apk.dir.dir_size, 34701);

    for (i = 0; i < in_apk.dir.count; ++i) {
        const sm_asm_entry *e = &in_apk.dir.entries[i];
        const sm_asm_entry *hit = sm_dat_find(&in_apk, e->name);
        uint8_t a[64], b[64];
        uint32_t n = e->raw.stored_size < 64 ? e->raw.stored_size : 64;
        CHECK(hit != NULL);
        if (hit == e) {
            ++self;
        } else if (hit != NULL && hit < e) {
            ++shadowed; /* an earlier record with the same case-folded name wins */
        }
        CHECK_EQ_INT(sm_dat_read(&in_apk, e->raw.data_offset, a, n), SM_DAT_OK);
        CHECK_EQ_INT(sm_dat_read(&standalone, e->raw.data_offset, b, n), SM_DAT_OK);
        CHECK(memcmp(a, b, n) == 0);
        CHECK(memcmp(a, asmb + e->raw.data_offset, n) == 0);
    }
    CHECK_EQ_INT(self + shadowed, in_apk.dir.count);
    printf("real archive in APK at offset %llu: %u records, %u resolve to themselves, %u shadowed\n",
           (unsigned long long)off, in_apk.dir.count, self, shadowed);

    sm_dat_close(&in_apk);
    sm_dat_close(&standalone);
    free(apk);
    free(asmb);
    return sm_test_finish("platform.dat_real_apk");
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "real") == 0) {
        return test_real();
    }
    return test_synthetic();
}

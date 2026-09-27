/*
 * Unit tests for the asm.mp3 directory reconstruction
 * (reconstructed/assets/src/asm_archive.c) on synthetic archives built here
 * from the recovered format (docs/ASSET_FORMATS.md section 2), plus the
 * committed synthetic fixture when its path is given as argv[1].
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sm_assets/asm_archive.h"
#include "sm_test.h"

/* ------------------------------------------------------------------ */
/* Synthetic archive builder (writer side of the recovered format).    */
/* ------------------------------------------------------------------ */

typedef struct syn_rec {
    const char *name;
    uint32_t codec;
    uint32_t decoded_size;
    uint32_t dims;
    const char *payload; /* stored bytes */
    uint32_t payload_len;
} syn_rec;

static void wr32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* Layout: count | records | names | data (record order). Returns malloc'd buffer. */
static uint8_t *build_archive(const syn_rec *recs, uint32_t n, size_t *out_len)
{
    size_t names_len = 0, data_len = 0, table_end, dir_size, total, pos, dpos;
    uint32_t i;
    uint8_t *buf;
    for (i = 0; i < n; ++i) {
        names_len += strlen(recs[i].name) + 1;
        data_len += recs[i].payload_len;
    }
    table_end = 4 + (size_t)24 * n;
    dir_size = table_end + names_len;
    total = dir_size + data_len;
    buf = (uint8_t *)calloc(1, total < 12 ? 12 : total);
    if (buf == NULL) {
        return NULL;
    }
    wr32(buf, n);
    pos = table_end;
    dpos = dir_size;
    for (i = 0; i < n; ++i) {
        uint8_t *r = buf + 4 + (size_t)24 * i;
        size_t l = strlen(recs[i].name) + 1;
        wr32(r + 0, (uint32_t)pos);
        wr32(r + 4, (uint32_t)dpos);
        wr32(r + 8, recs[i].decoded_size);
        wr32(r + 12, recs[i].payload_len);
        wr32(r + 16, recs[i].codec);
        wr32(r + 20, recs[i].dims);
        memcpy(buf + pos, recs[i].name, l);
        memcpy(buf + dpos, recs[i].payload, recs[i].payload_len);
        pos += l;
        dpos += recs[i].payload_len;
    }
    *out_len = total < 12 ? 12 : total;
    return buf;
}

static const syn_rec k_recs[] = {
    {"DATA/A.TXT", SM_ASM_CODEC_RAW, 5, 0, "hello", 5},
    {"SPRITES/B.PNG", SM_ASM_CODEC_PNG, 4 * 4 * 2 + 0x13, (2u << 16) | 4u, "\x89PNGfake", 8},
    {"data/a.txt", SM_ASM_CODEC_RAW, 3, 0, "dup", 3}, /* case-insensitive duplicate of 0 */
    {"X/C.X", SM_ASM_CODEC_ZIP, 100, 0, "PK\x03\x04zz", 6},
    {"Q/UNKNOWN.BIN", 9, 4, 0, "abcd", 4},           /* codec outside 0..3 */
};
#define K_N ((uint32_t)(sizeof(k_recs) / sizeof(k_recs[0])))

static void test_valid(void)
{
    size_t len = 0;
    uint8_t *a = build_archive(k_recs, K_N, &len);
    sm_asm_directory d;
    uint32_t bad = 0, off = 0, n = 0;
    uint64_t ext = 0;
    const sm_asm_entry *e;
    CHECK(a != NULL);
    if (a == NULL) {
        return;
    }
    CHECK_EQ_INT(sm_asm_directory_parse(a, len, &d, &bad), SM_ASM_OK);
    CHECK_EQ_INT(bad, UINT32_MAX);
    CHECK_EQ_INT(d.count, K_N);
    CHECK_EQ_INT(d.table_end, 4 + 24 * K_N);
    CHECK_EQ_INT(d.dir_size, rd32(a + 8));
    CHECK(strcmp(d.entries[1].name, "SPRITES/B.PNG") == 0);
    CHECK_EQ_INT(d.entries[1].name_len, 13);
    CHECK_EQ_INT(sm_asm_entry_width(&d.entries[1]), 4);
    CHECK_EQ_INT(sm_asm_entry_height(&d.entries[1]), 2);
    /* no in-place fix-up: on-disk bytes unchanged */
    CHECK(memcmp(d.dir_bytes, a, d.dir_size) == 0);
    CHECK_EQ_INT(d.entries[0].raw.name_offset, rd32(a + 4));

    /* lookup before index is built */
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "DATA/A.TXT"), -1);
    CHECK_EQ_INT(sm_asm_directory_build_index(&d), SM_ASM_OK);
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "DATA/A.TXT"), 0);
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "data/a.txt"), 0); /* first wins */
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "Data/A.Txt"), 0);
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "sprites/b.png"), 1);
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "SPRITES\\B.PNG"), -1); /* no normalisation */
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "./SPRITES/B.PNG"), -1);
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "SPRITES/B"), -1); /* no extension handling */
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, ""), -1);
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "x/c.x"), 3);
    e = sm_asm_directory_find(&d, "q/unknown.bin");
    CHECK(e == &d.entries[4]);
    CHECK(sm_asm_directory_find(&d, "nope") == NULL);

    /* read spans follow PfmLoadFileDat: raw reads decoded_size, others stored_size */
    CHECK_EQ_INT(sm_asm_entry_read_span(&d.entries[0], &off, &n), SM_ASM_OK);
    CHECK_EQ_INT(off, d.dir_size);
    CHECK_EQ_INT(n, 5);
    CHECK_EQ_INT(sm_asm_entry_read_span(&d.entries[1], &off, &n), SM_ASM_OK);
    CHECK_EQ_INT(n, 8);
    CHECK_EQ_INT(sm_asm_entry_read_span(&d.entries[4], &off, &n), SM_ASM_ERR_UNSUPPORTED_CODEC);
    CHECK_EQ_INT(sm_asm_directory_validate_data(&d, len, &bad), SM_ASM_OK);
    CHECK_EQ_INT(sm_asm_directory_validate_data(&d, len - 1, &bad), SM_ASM_ERR_DATA_RANGE);
    CHECK_EQ_INT(bad, 4);

    CHECK_EQ_INT(sm_asm_entry_output_extent(&d.entries[1], &ext), SM_ASM_OK);
    CHECK_EQ_INT(ext, 18 + ((4 * 4 * 2 + 0x13) / 2) * 2);
    CHECK_EQ_INT(sm_asm_entry_output_extent(&d.entries[3], &ext), SM_ASM_OK);
    CHECK_EQ_INT(ext, 100);

    sm_asm_directory_free(&d);
    CHECK(d.entries == NULL && d.dir_bytes == NULL);
    /* parsing only the directory prefix is enough */
    CHECK_EQ_INT(sm_asm_directory_parse(a, rd32(a + 8), &d, NULL), SM_ASM_OK);
    sm_asm_directory_free(&d);
    free(a);
}

static void test_zero_count(void)
{
    uint8_t a[12] = {0};
    sm_asm_directory d;
    wr32(a + 8, 12); /* dir_size is read from offset 8 even when count == 0 */
    CHECK_EQ_INT(sm_asm_directory_parse(a, sizeof a, &d, NULL), SM_ASM_OK);
    CHECK_EQ_INT(d.count, 0);
    CHECK_EQ_INT(sm_asm_directory_build_index(&d), SM_ASM_OK);
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "A"), -1);
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, ""), -1);
    sm_asm_directory_free(&d);
}

static void test_malformed(void)
{
    size_t len = 0;
    uint8_t *a = build_archive(k_recs, K_N, &len);
    uint8_t *m;
    sm_asm_directory d;
    uint32_t bad = 0;
    uint32_t dir_size;
    if (a == NULL) {
        CHECK(0);
        return;
    }
    dir_size = rd32(a + 8);
    m = (uint8_t *)malloc(len);
    if (m == NULL) {
        CHECK(0);
        free(a);
        return;
    }

    CHECK_EQ_INT(sm_asm_directory_parse(NULL, len, &d, NULL), SM_ASM_ERR_ARG);
    CHECK_EQ_INT(sm_asm_directory_parse(a, len, NULL, NULL), SM_ASM_ERR_ARG);
    /* truncated header */
    CHECK_EQ_INT(sm_asm_directory_parse(a, 0, &d, NULL), SM_ASM_ERR_TRUNCATED_HEADER);
    CHECK_EQ_INT(sm_asm_directory_parse(a, 11, &d, NULL), SM_ASM_ERR_TRUNCATED_HEADER);
    /* directory larger than the buffer (truncated directory) */
    CHECK_EQ_INT(sm_asm_directory_parse(a, dir_size - 1, &d, NULL), SM_ASM_ERR_DIR_SIZE_RANGE);
    memcpy(m, a, len);
    wr32(m + 8, 3);
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, NULL), SM_ASM_ERR_DIR_SIZE_RANGE);
    /* negative count */
    memcpy(m, a, len);
    wr32(m, 0x80000000u);
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, NULL), SM_ASM_ERR_NEGATIVE_COUNT);
    memcpy(m, a, len);
    wr32(m, 0xffffffffu);
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, NULL), SM_ASM_ERR_NEGATIVE_COUNT);
    /* count overflow: 4 + 24*0x0aaaaaab wraps to 8 in 32-bit arithmetic */
    memcpy(m, a, len);
    wr32(m, 0x0aaaaaabu);
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, NULL), SM_ASM_ERR_COUNT_OVERFLOW);
    /* count one too large for the directory */
    memcpy(m, a, len);
    wr32(m, K_N + 30);
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, NULL), SM_ASM_ERR_COUNT_OVERFLOW);
    /* name offset past the directory */
    memcpy(m, a, len);
    wr32(m + 4 + 24 * 2, dir_size);
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, &bad), SM_ASM_ERR_NAME_OFFSET_RANGE);
    CHECK_EQ_INT(bad, 2);
    memcpy(m, a, len);
    wr32(m + 4 + 24 * 1, 0xffffffffu);
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, &bad), SM_ASM_ERR_NAME_OFFSET_RANGE);
    CHECK_EQ_INT(bad, 1);
    /* name offset inside the record table */
    memcpy(m, a, len);
    wr32(m + 4 + 24 * 3, 4);
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, &bad), SM_ASM_ERR_NAME_OFFSET_RANGE);
    CHECK_EQ_INT(bad, 3);
    /* missing NUL: last name's terminator removed */
    memcpy(m, a, len);
    m[dir_size - 1] = 'Z';
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, &bad), SM_ASM_ERR_NAME_UNTERMINATED);
    CHECK_EQ_INT(bad, K_N - 1);
    /* failed parse leaves *out empty and free-able */
    CHECK(d.entries == NULL && d.dir_bytes == NULL && d.count == 0);
    sm_asm_directory_free(&d);
    /* data offset out of range is a validate_data error, not a parse error */
    memcpy(m, a, len);
    wr32(m + 4 + 24 * 0 + 4, 0xfffffff0u);
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, NULL), SM_ASM_ERR_DIR_SIZE_RANGE);
    memcpy(m, a, len);
    wr32(m + 4 + 24 * 1 + 4, 0xfffffff0u); /* record 1 offset */
    CHECK_EQ_INT(sm_asm_directory_parse(m, len, &d, NULL), SM_ASM_OK);
    CHECK_EQ_INT(sm_asm_directory_validate_data(&d, len, &bad), SM_ASM_ERR_DATA_RANGE);
    CHECK_EQ_INT(bad, 1);
    sm_asm_directory_free(&d);
    free(m);
    free(a);
}

static void test_tga_and_rows(void)
{
    uint8_t h[18];
    static const uint8_t expect[18] = {0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x00, 0x02, 0x80, 0x00, 0x20, 0x08};
    uint8_t src[3 * 5 + 2]; /* decoded_size 17, height 3 -> row_bytes 5 */
    uint8_t dst[15];
    int i;
    sm_asm_build_tga_header(512, 128, h);
    CHECK(memcmp(h, expect, 18) == 0);
    for (i = 0; i < (int)sizeof src; ++i) {
        src[i] = (uint8_t)i;
    }
    CHECK_EQ_INT(sm_asm_image_place_rows(dst, sizeof dst, src, sizeof src, 17, 3), SM_ASM_OK);
    CHECK_EQ_INT(dst[0], 10); /* last Java row first */
    CHECK_EQ_INT(dst[5], 5);
    CHECK_EQ_INT(dst[10], 0);
    CHECK_EQ_INT(dst[14], 4);
    CHECK_EQ_INT(sm_asm_image_place_rows(dst, sizeof dst, src, sizeof src, 17, 0), SM_ASM_ERR_BAD_IMAGE);
    CHECK_EQ_INT(sm_asm_image_place_rows(dst, 14, src, sizeof src, 17, 3), SM_ASM_ERR_BAD_IMAGE);
    CHECK_EQ_INT(sm_asm_image_place_rows(dst, sizeof dst, src, sizeof src, 0x80000000u, 3),
                 SM_ASM_ERR_BAD_IMAGE);
}

static void test_fixture(const char *path)
{
    FILE *f = fopen(path, "rb");
    uint8_t *buf;
    long sz;
    sm_asm_directory d;
    uint32_t i, bad;
    if (f == NULL) {
        fprintf(stderr, "fixture %s not readable\n", path);
        CHECK(0);
        return;
    }
    fseek(f, 0, SEEK_END);
    sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    buf = (uint8_t *)malloc(sz > 0 ? (size_t)sz : 1);
    CHECK(buf != NULL && sz > 0 && fread(buf, 1, (size_t)sz, f) == (size_t)sz);
    fclose(f);
    if (buf == NULL || sz <= 0) {
        free(buf);
        return;
    }
    CHECK_EQ_INT(sm_asm_directory_parse(buf, (size_t)sz, &d, &bad), SM_ASM_OK);
    CHECK_EQ_INT(sm_asm_directory_build_index(&d), SM_ASM_OK);
    CHECK_EQ_INT(sm_asm_directory_validate_data(&d, (uint64_t)sz, &bad), SM_ASM_OK);
    for (i = 0; i < d.count; ++i) {
        uint32_t j;
        int32_t expect = -1;
        for (j = 0; j < d.count && expect < 0; ++j) {
            if (sm_rhash_name_equal(d.entries[i].name, d.entries[j].name)) {
                expect = (int32_t)j;
            }
        }
        CHECK_EQ_INT(sm_asm_directory_find_index(&d, d.entries[i].name), expect);
    }
    printf("fixture %s: %u records\n", path, (unsigned)d.count);
    sm_asm_directory_free(&d);
    free(buf);
}

int main(int argc, char **argv)
{
    test_valid();
    test_zero_count();
    test_malformed();
    test_tga_and_rows();
    if (argc > 1) {
        test_fixture(argv[1]);
    }
    return sm_test_finish("test_asm_archive");
}

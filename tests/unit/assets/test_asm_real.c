/*
 * Optional test on the real archive (work/apk_unzip/assets/asm.mp3).
 * Exits 77 (CTest SKIP_RETURN_CODE) when the file is absent.
 *
 * Checks, for whatever record count the file actually holds:
 *   - the directory parses and every read span lies inside the file;
 *   - every name, looked up exactly and in lower case, resolves to the FIRST
 *     record whose name is equal under the original comparison (a later
 *     duplicate is shadowed -- expectation derived from cRHash semantics and
 *     confirmed against the original code in tests/differential/assets/);
 *   - data regions (read spans, in record order) are contiguous from the end
 *     of the directory to the end of the file.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sm_assets/asm_archive.h"
#include "sm_test.h"

#define SKIP 77

int main(int argc, char **argv)
{
    const char *path = argc > 1 ? argv[1] : "work/apk_unzip/assets/asm.mp3";
    FILE *f = fopen(path, "rb");
    uint8_t *buf;
    long sz;
    sm_asm_directory d;
    uint32_t i, bad = 0, shadowed = 0;
    uint64_t pos;
    char lower[4096];

    if (f == NULL) {
        printf("SKIP: %s not present\n", path);
        return SKIP;
    }
    if (fseek(f, 0, SEEK_END) != 0 || (sz = ftell(f)) <= 0 || fseek(f, 0, SEEK_SET) != 0) {
        fclose(f);
        fprintf(stderr, "cannot size %s\n", path);
        return 1;
    }
    buf = (uint8_t *)malloc((size_t)sz);
    if (buf == NULL || fread(buf, 1, (size_t)sz, f) != (size_t)sz) {
        fclose(f);
        free(buf);
        fprintf(stderr, "cannot read %s\n", path);
        return 1;
    }
    fclose(f);

    CHECK_EQ_INT(sm_asm_directory_parse(buf, (size_t)sz, &d, &bad), SM_ASM_OK);
    if (sm_test_failures != 0) {
        fprintf(stderr, "parse failed at record %u\n", (unsigned)bad);
        free(buf);
        return 1;
    }
    CHECK_EQ_INT(sm_asm_directory_build_index(&d), SM_ASM_OK);
    CHECK_EQ_INT(sm_asm_directory_validate_data(&d, (uint64_t)sz, &bad), SM_ASM_OK);

    pos = d.dir_size;
    for (i = 0; i < d.count; ++i) {
        const sm_asm_entry *e = &d.entries[i];
        uint32_t j, off = 0, n = 0;
        int32_t expect = -1;
        size_t k;
        for (j = 0; j < d.count && expect < 0; ++j) {
            if (sm_rhash_name_equal(e->name, d.entries[j].name)) {
                expect = (int32_t)j;
            }
        }
        if (expect != (int32_t)i) {
            ++shadowed;
            printf("record %u '%s' is shadowed by record %d\n", (unsigned)i, e->name, (int)expect);
        }
        CHECK_EQ_INT(sm_asm_directory_find_index(&d, e->name), expect);
        CHECK(e->name_len < sizeof lower);
        if (e->name_len < sizeof lower) {
            for (k = 0; k <= e->name_len; ++k) {
                char c = e->name[k];
                lower[k] = (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c;
            }
            CHECK_EQ_INT(sm_asm_directory_find_index(&d, lower), expect);
        }
        CHECK_EQ_INT(sm_asm_entry_read_span(e, &off, &n), SM_ASM_OK);
        CHECK_EQ_INT(off, pos);
        pos = (uint64_t)off + n;
    }
    CHECK_EQ_INT(pos, (uint64_t)sz);
    CHECK_EQ_INT(sm_asm_directory_find_index(&d, "NO/SUCH/FILE.TXT"), -1);
    printf("%s: %ld bytes, %u records, directory %zu bytes, %u shadowed duplicate(s)\n", path, sz,
           (unsigned)d.count, d.dir_size, (unsigned)shadowed);
    sm_asm_directory_free(&d);
    free(buf);
    return sm_test_finish("test_asm_real");
}

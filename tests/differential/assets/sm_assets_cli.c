/*
 * Host CLI over the reconstructed sm_assets module, used by run_diff.py to
 * compare against the original ARM32 code. Test tooling only.
 *
 *   calc KEYS                 one line per key: hash
 *   calcrep BYTE COUNT        hash of COUNT copies of BYTE
 *   table NAMES KEYS          Init(n) + Add(name_i, i); S lines, B lines, P line
 *   archive ARCHIVE KEYS      parse + build index; N line, R lines (per record:
 *                             index, 6 raw fields, read span, output extent;
 *                             -1 when the codec is unsupported), S lines,
 *                             B lines, P line
 *   image W H DECODED OUT     TGA header + row placement of the synthetic
 *                             pattern the harness feeds the original; bytes to OUT
 *
 * KEYS/NAMES files: one hex-encoded string per line (empty line = "").
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sm_assets/asm_archive.h"
#include "sm_assets/rhash.h"

typedef struct str_list {
    char **items;
    size_t count;
} str_list;

static int hexval(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int read_hex_lines(const char *path, str_list *out)
{
    FILE *f = fopen(path, "rb");
    size_t cap = 0;
    static char line[1 << 20];
    out->items = NULL;
    out->count = 0;
    if (f == NULL) {
        return -1;
    }
    while (fgets(line, sizeof line, f) != NULL) {
        size_t n = strcspn(line, "\r\n");
        size_t i;
        char *s;
        if (n % 2 != 0) {
            fclose(f);
            return -1;
        }
        s = (char *)malloc(n / 2 + 1);
        if (s == NULL) {
            fclose(f);
            return -1;
        }
        for (i = 0; i < n / 2; ++i) {
            int hi = hexval((unsigned char)line[2 * i]), lo = hexval((unsigned char)line[2 * i + 1]);
            if (hi < 0 || lo < 0 || (hi == 0 && lo == 0)) {
                free(s);
                fclose(f);
                return -1;
            }
            s[i] = (char)(hi * 16 + lo);
        }
        s[n / 2] = 0;
        if (out->count == cap) {
            size_t ncap = cap ? cap * 2 : 256;
            char **ni = (char **)realloc(out->items, ncap * sizeof(char *));
            if (ni == NULL) {
                free(s);
                fclose(f);
                return -1;
            }
            out->items = ni;
            cap = ncap;
        }
        out->items[out->count++] = s;
    }
    fclose(f);
    return 0;
}

static void free_list(str_list *l)
{
    size_t i;
    for (i = 0; i < l->count; ++i) {
        free(l->items[i]);
    }
    free(l->items);
}

static const char *list_name(void *ctx, int32_t v)
{
    const str_list *l = (const str_list *)ctx;
    if (v < 0 || (size_t)v >= l->count) {
        return NULL;
    }
    return l->items[v];
}

static void dump_table(const sm_rhash *h)
{
    uint32_t b;
    int32_t chain[4096];
    for (b = 0; b < SM_RHASH_BUCKETS; ++b) {
        size_t n = sm_rhash_chain(h, b, chain, 4096), i;
        if (n == 0) {
            continue;
        }
        printf("B %u", (unsigned)b);
        for (i = 0; i < n && i < 4096; ++i) {
            printf(" %d", (int)chain[i]);
        }
        printf("\n");
    }
    printf("P %u\n", (unsigned)h->pool_used);
}

static unsigned char *read_file(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    unsigned char *buf;
    long sz;
    if (f == NULL) return NULL;
    if (fseek(f, 0, SEEK_END) != 0 || (sz = ftell(f)) < 0 || fseek(f, 0, SEEK_SET) != 0) {
        fclose(f);
        return NULL;
    }
    buf = (unsigned char *)malloc((size_t)sz + 1);
    if (buf == NULL || fread(buf, 1, (size_t)sz, f) != (size_t)sz) {
        fclose(f);
        free(buf);
        return NULL;
    }
    fclose(f);
    *len = (size_t)sz;
    return buf;
}

int main(int argc, char **argv)
{
    if (argc >= 3 && strcmp(argv[1], "calc") == 0) {
        str_list keys;
        size_t i;
        if (read_hex_lines(argv[2], &keys) != 0) return 2;
        for (i = 0; i < keys.count; ++i) printf("%u\n", (unsigned)sm_rhash_calc(keys.items[i]));
        free_list(&keys);
        return 0;
    }
    if (argc >= 4 && strcmp(argv[1], "calcrep") == 0) {
        unsigned long byte = strtoul(argv[2], NULL, 0), count = strtoul(argv[3], NULL, 0);
        char *s = (char *)malloc(count + 1);
        if (s == NULL || byte == 0 || byte > 255) return 2;
        memset(s, (int)byte, count);
        s[count] = 0;
        printf("%u\n", (unsigned)sm_rhash_calc(s));
        free(s);
        return 0;
    }
    if (argc >= 4 && strcmp(argv[1], "table") == 0) {
        str_list names, keys;
        sm_rhash h;
        size_t i;
        if (read_hex_lines(argv[2], &names) != 0 || read_hex_lines(argv[3], &keys) != 0) return 2;
        if (sm_rhash_init(&h, (int32_t)names.count, list_name, &names) != SM_RHASH_OK) return 3;
        for (i = 0; i < names.count; ++i) {
            if (sm_rhash_add(&h, names.items[i], (int32_t)i) != SM_RHASH_OK) return 3;
        }
        for (i = 0; i < keys.count; ++i) printf("S %d\n", (int)sm_rhash_search(&h, keys.items[i]));
        dump_table(&h);
        sm_rhash_uninit(&h);
        free_list(&names);
        free_list(&keys);
        return 0;
    }
    if (argc >= 4 && strcmp(argv[1], "archive") == 0) {
        size_t len = 0, i;
        unsigned char *buf = read_file(argv[2], &len);
        str_list keys;
        sm_asm_directory d;
        uint32_t bad;
        sm_asm_status st;
        if (buf == NULL || read_hex_lines(argv[3], &keys) != 0) return 2;
        st = sm_asm_directory_parse(buf, len, &d, &bad);
        if (st != SM_ASM_OK) {
            printf("E %d %s %u\n", (int)st, sm_asm_status_str(st), (unsigned)bad);
            return 4;
        }
        if (sm_asm_directory_build_index(&d) != SM_ASM_OK) return 3;
        printf("N %u %zu\n", (unsigned)d.count, d.dir_size);
        for (i = 0; i < d.count; ++i) {
            const sm_asm_entry *e = &d.entries[i];
            uint32_t off = 0, n = 0;
            uint64_t ext = 0;
            int have_span = sm_asm_entry_read_span(e, &off, &n) == SM_ASM_OK;
            int have_ext = sm_asm_entry_output_extent(e, &ext) == SM_ASM_OK;
            printf("R %zu %u %u %u %u %u %u %lld %lld %lld\n", i, (unsigned)e->raw.name_offset,
                   (unsigned)e->raw.data_offset, (unsigned)e->raw.decoded_size, (unsigned)e->raw.stored_size,
                   (unsigned)e->raw.codec, (unsigned)e->raw.dims, have_span ? (long long)off : -1LL,
                   have_span ? (long long)n : -1LL, have_ext ? (long long)ext : -1LL);
        }
        for (i = 0; i < keys.count; ++i) printf("S %d\n", (int)sm_asm_directory_find_index(&d, keys.items[i]));
        dump_table(&d.index);
        sm_asm_directory_free(&d);
        free_list(&keys);
        free(buf);
        return 0;
    }
    if (argc >= 6 && strcmp(argv[1], "image") == 0) {
        unsigned long w = strtoul(argv[2], NULL, 0), hgt = strtoul(argv[3], NULL, 0);
        unsigned long dec = strtoul(argv[4], NULL, 0);
        uint8_t *src, *dst;
        size_t k, pix = (size_t)w * hgt * 4, rows;
        FILE *f;
        if (w > 0xffff || hgt == 0 || hgt > 0xffff || dec < pix) return 2;
        src = (uint8_t *)calloc(1, dec ? dec : 1);
        rows = (dec / hgt) * hgt;
        dst = (uint8_t *)calloc(1, SM_ASM_TGA_HEADER_SIZE + rows);
        if (src == NULL || dst == NULL) return 2;
        /* aperiodic pattern, identical to run_diff.py's pattern() */
        for (k = 0; k < pix; ++k) src[k] = (uint8_t)(((uint64_t)k * 2654435761u) >> 16);
        sm_asm_build_tga_header((uint16_t)w, (uint16_t)hgt, dst);
        if (sm_asm_image_place_rows(dst + SM_ASM_TGA_HEADER_SIZE, rows, src, dec, (uint32_t)dec,
                                    (uint16_t)hgt) != SM_ASM_OK)
            return 3;
        f = fopen(argv[5], "wb");
        if (f == NULL || fwrite(dst, 1, SM_ASM_TGA_HEADER_SIZE + rows, f) != SM_ASM_TGA_HEADER_SIZE + rows) return 2;
        fclose(f);
        free(src);
        free(dst);
        return 0;
    }
    fprintf(stderr, "usage: see source\n");
    return 1;
}

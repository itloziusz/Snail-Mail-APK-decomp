/*
 * Reconstruction of the asm.mp3 directory handling from v7a
 * (sha256 e43bc913...a466). See docs/ASSET_FORMATS.md for the format
 * and analysis/evidence/assets.jsonl for the per-claim evidence.
 */
#include "sm_assets/asm_archive.h"

#include <stdlib.h>
#include <string.h>

static uint32_t rd_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

const char *sm_asm_status_str(sm_asm_status st)
{
    switch (st) {
    case SM_ASM_OK: return "ok";
    case SM_ASM_ERR_ARG: return "invalid argument";
    case SM_ASM_ERR_TRUNCATED_HEADER: return "truncated header";
    case SM_ASM_ERR_DIR_SIZE_RANGE: return "directory size out of range";
    case SM_ASM_ERR_NEGATIVE_COUNT: return "negative record count";
    case SM_ASM_ERR_COUNT_OVERFLOW: return "record table does not fit in directory";
    case SM_ASM_ERR_NAME_OFFSET_RANGE: return "name offset out of range";
    case SM_ASM_ERR_NAME_UNTERMINATED: return "name not NUL-terminated inside directory";
    case SM_ASM_ERR_DATA_RANGE: return "data span outside archive";
    case SM_ASM_ERR_NO_MEMORY: return "out of memory";
    case SM_ASM_ERR_NOT_FOUND: return "not found";
    case SM_ASM_ERR_UNSUPPORTED_CODEC: return "unsupported codec";
    case SM_ASM_ERR_BAD_IMAGE: return "bad image geometry";
    }
    return "unknown status";
}

static void dir_zero(sm_asm_directory *d)
{
    memset(d, 0, sizeof(*d));
}

/*
 * Reconstructs the directory load of JNIDatInit (v7a:0x1533c-0x153ac):
 *   fseek(fp, start, SEEK_SET); fread(probe, 1, 244, fp)
 *   dir_size = probe.u32[2]                       (ldr r3,[sp,#0x14])
 *   gDat = malloc(dir_size)                       (RShellMemoryMalloc = b malloc, v7a:0x1b714)
 *   fseek(fp, start, SEEK_SET); fread(gDat, 1, dir_size, fp)
 *   count = gDat.u32[0]                           (ldr r1,[r3] before cRHash::Init)
 *   record i at gDat + 4 + 24*i                   (add ip,r3,(3i)<<3; ldr r1,[ip,#4])
 * and the name fix-up (v7a:0x153f4-0x153fc: [ip+4] += gDat), which here
 * becomes a validated host pointer into the owned copy instead.
 * The original validates nothing (no fread result checks, no bounds).
 */
sm_asm_status sm_asm_directory_parse(const uint8_t *buf, size_t len, sm_asm_directory *out,
                                     uint32_t *bad_index)
{
    uint32_t dir_size32;
    uint32_t count;
    uint64_t table_end;
    uint32_t i;
    sm_asm_directory d;

    if (bad_index != NULL) {
        *bad_index = UINT32_MAX;
    }
    if (out == NULL) {
        return SM_ASM_ERR_ARG;
    }
    dir_zero(out);
    if (buf == NULL) {
        return SM_ASM_ERR_ARG;
    }
    if (len < SM_ASM_MIN_HEADER) {
        return SM_ASM_ERR_TRUNCATED_HEADER;
    }
    dir_size32 = rd_le32(buf + SM_ASM_DIR_SIZE_OFFSET);
    if (dir_size32 < 4u || (uint64_t)dir_size32 > (uint64_t)len) {
        return SM_ASM_ERR_DIR_SIZE_RANGE;
    }
    count = rd_le32(buf + SM_ASM_COUNT_OFFSET);
    if (count > (uint32_t)INT32_MAX) {
        /* JNIDatInit's loop compares signed (cmp r2,fp; ble); cRHash::Init
         * would malloc(count<<3). A negative count is not a usable archive. */
        return SM_ASM_ERR_NEGATIVE_COUNT;
    }
    table_end = (uint64_t)SM_ASM_RECORD_BASE + (uint64_t)count * SM_ASM_RECORD_SIZE;
    if (table_end > (uint64_t)dir_size32) {
        return SM_ASM_ERR_COUNT_OVERFLOW;
    }

    dir_zero(&d);
    d.dir_size = (size_t)dir_size32;
    d.count = count;
    d.table_end = (uint32_t)table_end;
    d.dir_bytes = (uint8_t *)malloc(d.dir_size);
    if (d.dir_bytes == NULL) {
        return SM_ASM_ERR_NO_MEMORY;
    }
    memcpy(d.dir_bytes, buf, d.dir_size);
    if (count > 0) {
        d.entries = (sm_asm_entry *)calloc((size_t)count, sizeof(sm_asm_entry));
        if (d.entries == NULL) {
            free(d.dir_bytes);
            return SM_ASM_ERR_NO_MEMORY;
        }
    }

    for (i = 0; i < count; ++i) {
        const uint8_t *r = d.dir_bytes + SM_ASM_RECORD_BASE + (size_t)i * SM_ASM_RECORD_SIZE;
        sm_asm_entry *e = &d.entries[i];
        const uint8_t *nul;
        e->raw.name_offset = rd_le32(r + 0);
        e->raw.data_offset = rd_le32(r + 4);
        e->raw.decoded_size = rd_le32(r + 8);
        e->raw.stored_size = rd_le32(r + 12);
        e->raw.codec = rd_le32(r + 16);
        e->raw.dims = rd_le32(r + 20);
        /* Names must lie in the string area after the record table: the
         * original only requires "somewhere in gDat", but a name overlapping
         * the table would be corrupted by its own in-place fix-up. */
        if (e->raw.name_offset < d.table_end || e->raw.name_offset >= dir_size32) {
            if (bad_index != NULL) {
                *bad_index = i;
            }
            sm_asm_directory_free(&d);
            return SM_ASM_ERR_NAME_OFFSET_RANGE;
        }
        nul = (const uint8_t *)memchr(d.dir_bytes + e->raw.name_offset, 0,
                                      d.dir_size - (size_t)e->raw.name_offset);
        if (nul == NULL) {
            if (bad_index != NULL) {
                *bad_index = i;
            }
            sm_asm_directory_free(&d);
            return SM_ASM_ERR_NAME_UNTERMINATED;
        }
        e->name = (const char *)(d.dir_bytes + e->raw.name_offset);
        e->name_len = (size_t)(nul - (d.dir_bytes + e->raw.name_offset));
    }
    *out = d;
    return SM_ASM_OK;
}

/* DatHashGetString(int) (v7a:0x19954-0x1997f): *(char **)(gDat + 24*i + 4).
 * Bounds-checked here; the original indexes without checks. */
static const char *dir_name_fn(void *ctx, int32_t value)
{
    const sm_asm_directory *d = (const sm_asm_directory *)ctx;
    if (d == NULL || value < 0 || (uint32_t)value >= d->count) {
        return NULL;
    }
    return d->entries[value].name;
}

/* JNIDatInit v7a:0x153b0-0x15414:
 *   cRHash::Init(&gDatHash, count, DatHashGetString)
 *   for (i = 0; i < count; i++) cRHash::Add(&gDatHash, name_i, i)          */
sm_asm_status sm_asm_directory_build_index(sm_asm_directory *dir)
{
    uint32_t i;
    if (dir == NULL) {
        return SM_ASM_ERR_ARG;
    }
    if (dir->index_built) {
        sm_rhash_uninit(&dir->index);
        dir->index_built = 0;
    }
    if (sm_rhash_init(&dir->index, (int32_t)dir->count, dir_name_fn, dir) != SM_RHASH_OK) {
        return SM_ASM_ERR_NO_MEMORY;
    }
    for (i = 0; i < dir->count; ++i) {
        if (sm_rhash_add(&dir->index, dir->entries[i].name, (int32_t)i) != SM_RHASH_OK) {
            /* cannot happen: Init reserved one node per record */
            sm_rhash_uninit(&dir->index);
            return SM_ASM_ERR_NO_MEMORY;
        }
    }
    dir->index_built = 1;
    return SM_ASM_OK;
}

/* RShellDatFind v7a:0x1b920-0x1b97f:
 *   if (gDat == 0) return 0; i = cRHash::Search(&gDatHash, name);
 *   return i == -1 ? 0 : gDat + 24*i + 4                                    */
int32_t sm_asm_directory_find_index(const sm_asm_directory *dir, const char *name)
{
    if (dir == NULL || name == NULL || !dir->index_built) {
        return -1;
    }
    return sm_rhash_search(&dir->index, name);
}

const sm_asm_entry *sm_asm_directory_find(const sm_asm_directory *dir, const char *name)
{
    int32_t i = sm_asm_directory_find_index(dir, name);
    if (i < 0 || (uint32_t)i >= dir->count) {
        return NULL;
    }
    return &dir->entries[i];
}

sm_asm_status sm_asm_directory_validate_data(const sm_asm_directory *dir, uint64_t archive_len,
                                             uint32_t *bad_index)
{
    uint32_t i;
    if (bad_index != NULL) {
        *bad_index = UINT32_MAX;
    }
    if (dir == NULL) {
        return SM_ASM_ERR_ARG;
    }
    for (i = 0; i < dir->count; ++i) {
        const sm_asm_entry *e = &dir->entries[i];
        uint32_t off = e->raw.data_offset;
        uint32_t n = e->raw.stored_size;
        if (sm_asm_entry_read_span(e, &off, &n) != SM_ASM_OK) {
            off = e->raw.data_offset;
            n = e->raw.stored_size;
        }
        if ((uint64_t)off + (uint64_t)n > archive_len) {
            if (bad_index != NULL) {
                *bad_index = i;
            }
            return SM_ASM_ERR_DATA_RANGE;
        }
    }
    return SM_ASM_OK;
}

void sm_asm_directory_free(sm_asm_directory *dir)
{
    if (dir == NULL) {
        return;
    }
    if (dir->index_built) {
        sm_rhash_uninit(&dir->index);
    }
    free(dir->entries);
    free(dir->dir_bytes);
    dir_zero(dir);
}

uint16_t sm_asm_entry_width(const sm_asm_entry *e)
{
    return e == NULL ? 0u : (uint16_t)(e->raw.dims & 0xffffu);
}

uint16_t sm_asm_entry_height(const sm_asm_entry *e)
{
    return e == NULL ? 0u : (uint16_t)(e->raw.dims >> 16);
}

sm_asm_status sm_asm_entry_read_span(const sm_asm_entry *e, uint32_t *offset, uint32_t *length)
{
    if (e == NULL || offset == NULL || length == NULL) {
        return SM_ASM_ERR_ARG;
    }
    switch (e->raw.codec) {
    case SM_ASM_CODEC_RAW:
        *offset = e->raw.data_offset;
        *length = e->raw.decoded_size;
        return SM_ASM_OK;
    case SM_ASM_CODEC_ZIP:
    case SM_ASM_CODEC_JPEG:
    case SM_ASM_CODEC_PNG:
        *offset = e->raw.data_offset;
        *length = e->raw.stored_size;
        return SM_ASM_OK;
    default:
        return SM_ASM_ERR_UNSUPPORTED_CODEC;
    }
}

sm_asm_status sm_asm_entry_output_extent(const sm_asm_entry *e, uint64_t *extent)
{
    uint16_t h;
    if (e == NULL || extent == NULL) {
        return SM_ASM_ERR_ARG;
    }
    switch (e->raw.codec) {
    case SM_ASM_CODEC_RAW:
    case SM_ASM_CODEC_ZIP:
        *extent = e->raw.decoded_size;
        return SM_ASM_OK;
    case SM_ASM_CODEC_JPEG:
    case SM_ASM_CODEC_PNG:
        h = sm_asm_entry_height(e);
        if (h == 0 || e->raw.decoded_size > (uint32_t)INT32_MAX) {
            return SM_ASM_ERR_BAD_IMAGE;
        }
        *extent = SM_ASM_TGA_HEADER_SIZE + (uint64_t)(e->raw.decoded_size / h) * h;
        return SM_ASM_OK;
    default:
        return SM_ASM_ERR_UNSUPPORTED_CODEC;
    }
}

/* v7a:0x14d2c-0x14d74 (shared tail of the PNG and JPEG cases). */
void sm_asm_build_tga_header(uint16_t width, uint16_t height, uint8_t out[SM_ASM_TGA_HEADER_SIZE])
{
    memset(out, 0, SM_ASM_TGA_HEADER_SIZE);
    out[2] = 2;                           /* strb #2,[r4,#2]    image type: truecolour */
    out[12] = (uint8_t)(width & 0xffu);   /* strh fp,[r4,#0xc]  width  */
    out[13] = (uint8_t)(width >> 8);
    out[14] = (uint8_t)(height & 0xffu);  /* strh sb,[r4,#0xe]  height */
    out[15] = (uint8_t)(height >> 8);
    out[16] = 0x20;                       /* strb #0x20,[r4,#0x10] 32 bpp */
    out[17] = 8;                          /* strb #8,[r4,#0x11]  descriptor: 8 alpha bits, bottom-left */
}

/* JAVAC_UnPng v7a:0x1457c-0x145e0:
 *   rb = __aeabi_idiv(outSize, h)                  (signed division)
 *   if (h > 0) for r in 0..h-1:
 *       GetByteArrayRegion(out, r*rb, rb, dst + (h-1-r)*rb)                  */
sm_asm_status sm_asm_image_place_rows(uint8_t *dst, size_t dst_cap, const uint8_t *src, size_t src_len,
                                      uint32_t decoded_size, uint16_t height)
{
    uint32_t row_bytes;
    uint32_t r;
    if (dst == NULL || src == NULL) {
        return SM_ASM_ERR_ARG;
    }
    if (height == 0 || decoded_size > (uint32_t)INT32_MAX) {
        /* h == 0 divides by zero before the h > 0 test in the original */
        return SM_ASM_ERR_BAD_IMAGE;
    }
    row_bytes = decoded_size / height;
    if ((uint64_t)row_bytes * height > (uint64_t)dst_cap ||
        (uint64_t)row_bytes * height > (uint64_t)src_len) {
        return SM_ASM_ERR_BAD_IMAGE;
    }
    for (r = 0; r < height; ++r) {
        memcpy(dst + (size_t)(height - 1u - r) * row_bytes, src + (size_t)r * row_bytes, row_bytes);
    }
    return SM_ASM_OK;
}

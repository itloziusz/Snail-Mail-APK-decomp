/*
 * sm_assets/asm_archive.h -- directory of the assets/asm.mp3 archive and the
 * by-name lookup the original engine performs on it.
 *
 * Reference binary: v7a = lib/armeabi-v7a/libsnailmail.so
 *   sha256 e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466
 *
 * Original consumers (full spec: docs/ASSET_FORMATS.md):
 *   Java_..._SnailMailActivity_JNIDatInit      v7a:0x15244-0x15453  load + index
 *   DatHashGetString(int)                      v7a:0x19954-0x1997f  name getter
 *   RShellDatFind(char*)                       v7a:0x1b920-0x1b97f  lookup
 *   RShellLoadFile(char*, void*, int*)         v7a:0x1b980-0x1baeb  record use
 *   PfmLoadFileDat(void*,int,int,int,int,u16,u16) v7a:0x14c74-0x14eab decode
 *
 * On-disk layout (all little-endian, offsets from the first archive byte):
 *   0x00          u32 count (the original compares it signed)
 *   0x04 + 24*i   record i: 6 x u32
 *                   +0  name_offset   (NUL-terminated name inside directory)
 *                   +4  data_offset
 *                   +8  decoded_size
 *                   +12 stored_size
 *                   +16 codec (0 raw, 1 zip, 2 jpeg, 3 png)
 *                   +20 dims: u16 width (low half), u16 height (high half)
 *   The directory block is [0, dir_size) where dir_size is the u32 at
 *   archive offset 8 (i.e. record 0's data_offset) -- JNIDatInit reads that
 *   word from a 244-byte probe and loads exactly dir_size bytes.
 *
 * ARM64 note: the original converts name_offset into a 32-bit pointer IN PLACE
 * in the loaded directory (v7a:0x153f4-0x153fc). This module never does that:
 * on-disk fields stay uint32_t and host pointers live in a separate array.
 */
#ifndef SM_ASSETS_ASM_ARCHIVE_H
#define SM_ASSETS_ASM_ARCHIVE_H

#include <stddef.h>
#include <stdint.h>

#include "sm_assets/rhash.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SM_ASM_COUNT_OFFSET 0u
#define SM_ASM_DIR_SIZE_OFFSET 8u      /* u32 read from the probe at sp+0x14 (v7a:0x1536c) */
#define SM_ASM_MIN_HEADER 12u          /* bytes needed to read count and dir_size */
#define SM_ASM_PROBE_SIZE 244u         /* fread size of the original probe (v7a:0x15354) */
#define SM_ASM_RECORD_BASE 4u
#define SM_ASM_RECORD_SIZE 24u
#define SM_ASM_TGA_HEADER_SIZE 18u

typedef enum sm_asm_codec {
    SM_ASM_CODEC_RAW = 0,  /* PfmLoadFileDat case 0: fread decoded_size bytes */
    SM_ASM_CODEC_ZIP = 1,  /* case 1: JAVAC_UnZip (java.util.zip first entry)  */
    SM_ASM_CODEC_JPEG = 2, /* case 2: JAVAC_UnJpg (BitmapFactory) + TGA header */
    SM_ASM_CODEC_PNG = 3   /* case 3: JAVAC_UnPng (BitmapFactory) + TGA header */
} sm_asm_codec;

typedef enum sm_asm_status {
    SM_ASM_OK = 0,
    SM_ASM_ERR_ARG = 1,                /* NULL pointer argument */
    SM_ASM_ERR_TRUNCATED_HEADER = 2,   /* fewer than 12 bytes */
    SM_ASM_ERR_DIR_SIZE_RANGE = 3,     /* dir_size < 4 or dir_size > buffer length */
    SM_ASM_ERR_NEGATIVE_COUNT = 4,     /* count >= 2^31 (negative as the original's int) */
    SM_ASM_ERR_COUNT_OVERFLOW = 5,     /* 4 + 24*count does not fit inside dir_size */
    SM_ASM_ERR_NAME_OFFSET_RANGE = 6,  /* name_offset outside [table_end, dir_size) */
    SM_ASM_ERR_NAME_UNTERMINATED = 7,  /* no NUL between name_offset and dir_size */
    SM_ASM_ERR_DATA_RANGE = 8,         /* data span outside the archive */
    SM_ASM_ERR_NO_MEMORY = 9,
    SM_ASM_ERR_NOT_FOUND = 10,
    SM_ASM_ERR_UNSUPPORTED_CODEC = 11, /* codec > 3: PfmLoadFileDat does nothing */
    SM_ASM_ERR_BAD_IMAGE = 12          /* image placement impossible (height 0, sizes) */
} sm_asm_status;

/* The six on-disk u32 fields, exactly as stored. */
typedef struct sm_asm_record_raw {
    uint32_t name_offset;
    uint32_t data_offset;
    uint32_t decoded_size;
    uint32_t stored_size;
    uint32_t codec;
    uint32_t dims;
} sm_asm_record_raw;

/* Native (host) view of one record. */
typedef struct sm_asm_entry {
    sm_asm_record_raw raw;
    const char *name; /* points into sm_asm_directory.dir_bytes (NUL-terminated) */
    size_t name_len;
} sm_asm_entry;

typedef struct sm_asm_directory {
    uint8_t *dir_bytes;      /* owned, unmodified copy of archive bytes [0, dir_size) */
    size_t dir_size;
    uint32_t count;
    uint32_t table_end;      /* 4 + 24*count */
    sm_asm_entry *entries;   /* owned, count elements */
    sm_rhash index;          /* name -> record index (gDatHash equivalent) */
    int index_built;
} sm_asm_directory;

const char *sm_asm_status_str(sm_asm_status st);

/* Parses the directory from buf[0..len). `len` may be the whole archive or
 * just a prefix that contains [0, dir_size). On success *out owns copies of
 * the bytes; on failure *out is left empty (safe to free). If bad_index is
 * non-NULL it receives the record index that failed (or UINT32_MAX). */
sm_asm_status sm_asm_directory_parse(const uint8_t *buf, size_t len, sm_asm_directory *out,
                                     uint32_t *bad_index);

/* Builds the name index exactly as JNIDatInit does (v7a:0x153b0-0x15414):
 * cRHash::Init(count) then cRHash::Add(name_i, i) for i = 0..count-1. */
sm_asm_status sm_asm_directory_build_index(sm_asm_directory *dir);

/* RShellDatFind (v7a:0x1b920) without the pointer: record index for `name`,
 * or -1 when absent (or the index was not built). Case-insensitive for ASCII
 * letters only; no path normalisation; first-added record wins. */
int32_t sm_asm_directory_find_index(const sm_asm_directory *dir, const char *name);

/* RShellDatFind (v7a:0x1b920): record for `name`, or NULL. */
const sm_asm_entry *sm_asm_directory_find(const sm_asm_directory *dir, const char *name);

/* Checks that every record's read span (see sm_asm_entry_read_span; for
 * unknown codecs the stored span) lies within an archive of archive_len
 * bytes. Not done by the original. */
sm_asm_status sm_asm_directory_validate_data(const sm_asm_directory *dir, uint64_t archive_len,
                                             uint32_t *bad_index);

void sm_asm_directory_free(sm_asm_directory *dir);

/* dims field split as PfmLoadFileDat receives it: LDRH rec+0x14 / rec+0x16
 * (v7a:0x1ba0c-0x1ba18). */
uint16_t sm_asm_entry_width(const sm_asm_entry *e);
uint16_t sm_asm_entry_height(const sm_asm_entry *e);

/* The byte span PfmLoadFileDat reads from the archive (relative to the
 * archive start; the original adds gJavaAssetStart): codec 0 reads
 * decoded_size bytes (v7a:0x14d84-0x14db8); codecs 1-3 read stored_size bytes
 * (v7a:0x14dbc-0x14df8, 0x14e20-0x14e5c, 0x14cd0-0x14d0c). */
sm_asm_status sm_asm_entry_read_span(const sm_asm_entry *e, uint32_t *offset, uint32_t *length);

/* Size of the buffer region the original writes for this record when loading
 * into a caller buffer: codec 0/1 -> decoded_size; codec 2/3 -> 18-byte TGA
 * header + (decoded_size / height) * height pixel bytes. */
sm_asm_status sm_asm_entry_output_extent(const sm_asm_entry *e, uint64_t *extent);

/* The 18-byte TGA header PfmLoadFileDat writes in front of decoded JPEG/PNG
 * pixels (v7a:0x14d2c-0x14d74): type 2, 32 bpp, descriptor 8, origin 0,0. */
void sm_asm_build_tga_header(uint16_t width, uint16_t height, uint8_t out[SM_ASM_TGA_HEADER_SIZE]);

/* Row placement done by JAVAC_UnPng / JAVAC_UnJpg (v7a:0x1457c-0x145e0,
 * same shape at 0x149e8..): row_bytes = (int)decoded_size / height; Java row r
 * (source offset r*row_bytes) is copied to dst + (height-1-r)*row_bytes, i.e.
 * the image is flipped vertically. `src` is the Java byte[] of length
 * decoded_size (bitmap pixels followed by zero fill). dst receives
 * row_bytes*height bytes. */
sm_asm_status sm_asm_image_place_rows(uint8_t *dst, size_t dst_cap, const uint8_t *src, size_t src_len,
                                      uint32_t decoded_size, uint16_t height);

#ifdef __cplusplus
}
#endif

#endif /* SM_ASSETS_ASM_ARCHIVE_H */

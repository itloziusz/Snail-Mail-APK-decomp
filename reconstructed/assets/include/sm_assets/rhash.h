/*
 * sm_assets/rhash.h -- reconstruction of the original engine class cRHash.
 *
 * Reference binary: v7a = lib/armeabi-v7a/libsnailmail.so
 *   sha256 e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466
 *
 * Original methods (v7a addresses, see docs/ASSET_FORMATS.md section 4):
 *   cRHash::Calc(char*)                v7a:0x7d114-0x7d143
 *   cRHash::Add(char*, int)            v7a:0x7d144-0x7d18f
 *   cRHash::Search(char*)              v7a:0x7d190-0x7d277
 *   cRHash::Report()                   v7a:0x7d278-0x7d337 (diagnostic only)
 *   cRHash::UnInit()                   v7a:0x7d338-0x7d33f
 *   cRHash::Init(int, char*(*)(int))   v7a:0x7d340-0x7d397
 *
 * Original object layout (0x80c bytes, e.g. global gDatHash at v7a:0x101494,
 * size 2060):
 *   +0x000  256 buckets x {int32 value; node *next}   (value -1 = empty)
 *   +0x800  node *pool        (malloc(count*8), memset 0)
 *   +0x804  node *pool_next   (bump allocator)
 *   +0x808  char *(*get_name)(int value)
 * The table stores only the int values; names are fetched back through
 * get_name() when searching.
 *
 * Reconstruction differences (none change results for valid use):
 *   - chain links are 1-based pool indices (uint32_t) instead of 32-bit
 *     pointers, so the layout is pointer-width independent (ARM64 safe);
 *   - the name getter takes a context pointer instead of reading a global;
 *   - Add refuses to exceed the pool (the original writes past it);
 *   - Search treats a NULL name from the getter as "no match" (the original
 *     dereferences it);
 *   - UnInit leaves the object in a safe empty state.
 */
#ifndef SM_ASSETS_RHASH_H
#define SM_ASSETS_RHASH_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SM_RHASH_BUCKETS 256u
#define SM_RHASH_EMPTY (-1)

/* Returns the NUL-terminated name registered for `value`, or NULL. */
typedef const char *(*sm_rhash_name_fn)(void *ctx, int32_t value);

typedef struct sm_rhash_node {
    int32_t value;  /* original: node+0 (int) */
    uint32_t next;  /* original: node+4 (pointer); here 0 = end, else pool index + 1 */
} sm_rhash_node;

typedef struct sm_rhash {
    sm_rhash_node buckets[SM_RHASH_BUCKETS]; /* original: this+0x000 */
    sm_rhash_node *pool;                     /* original: this+0x800 */
    uint32_t pool_capacity;                  /* Init's count argument */
    uint32_t pool_used;                      /* original: this+0x804 (as an index) */
    sm_rhash_name_fn name_fn;                /* original: this+0x808 */
    void *name_ctx;                          /* reconstruction addition */
} sm_rhash;

typedef enum sm_rhash_status {
    SM_RHASH_OK = 0,
    SM_RHASH_ERR_ARG = 1,       /* NULL object/name, negative capacity */
    SM_RHASH_ERR_NO_MEMORY = 2, /* pool allocation failed or size overflow */
    SM_RHASH_ERR_FULL = 3       /* Add would need a pool node beyond capacity */
} sm_rhash_status;

/* cRHash::Calc (v7a:0x7d114). Sum of (byte | 0x20) over the bytes of the
 * NUL-terminated string, bytes read unsigned (LDRB), modulo 256. Empty string
 * hashes to 0. Result is in [0, 255]. */
uint32_t sm_rhash_calc(const char *s);

/* Name comparison used by cRHash::Search (v7a:0x7d1b8-0x7d274): equal length
 * and, per byte, equal after folding 'a'..'z' to 'A'..'Z' (bytes unsigned;
 * no other folding). Returns 1 when equal, 0 otherwise. */
int sm_rhash_name_equal(const char *key, const char *candidate);

/* cRHash::Init (v7a:0x7d340). All buckets become {-1, end}; allocates and
 * zeroes a pool of `capacity` chain nodes. Any previous pool is NOT freed
 * (matches the original; call sm_rhash_uninit first). */
sm_rhash_status sm_rhash_init(sm_rhash *h, int32_t capacity, sm_rhash_name_fn fn, void *ctx);

/* cRHash::Add (v7a:0x7d144). If the bucket head is empty (value == -1) the
 * value is stored in the head; otherwise a pool node is appended at the END
 * of the chain. Duplicates are not detected. */
sm_rhash_status sm_rhash_add(sm_rhash *h, const char *name, int32_t value);

/* cRHash::Search (v7a:0x7d190). Walks the chain of bucket Calc(key) in order
 * (head, then insertion order) and returns the value of the FIRST entry whose
 * name compares equal (sm_rhash_name_equal); -1 when none. */
int32_t sm_rhash_search(const sm_rhash *h, const char *key);

/* cRHash::UnInit (v7a:0x7d338): frees the pool. */
void sm_rhash_uninit(sm_rhash *h);

/* Inspection helper (the information cRHash::Report prints): writes up to
 * out_cap chain values of `bucket` in chain order and returns the full chain
 * length (0 for an empty bucket). */
size_t sm_rhash_chain(const sm_rhash *h, uint32_t bucket, int32_t *out, size_t out_cap);

#ifdef __cplusplus
}
#endif

#endif /* SM_ASSETS_RHASH_H */

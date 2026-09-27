/*
 * Reconstruction of cRHash from v7a (sha256 e43bc913...a466).
 * Every function names the original address range it reconstructs; the
 * instruction-level argument is in docs/ASSET_FORMATS.md section 4 and the
 * evidence ledger analysis/evidence/assets.jsonl (EV-ASSET-0011..0016).
 */
#include "sm_assets/rhash.h"

#include <stdlib.h>
#include <string.h>

/*
 * v7a:0x7d114-0x7d143
 *   ldrb r3,[r1]; cmp r3,#0; moveq r0,r3; bxeq lr        empty -> 0
 *   mov r0,#0
 *   loop: orr r2,r3,#0x20; ldrb r3,[r1,#1]!; add r0,r0,r2; cmp r3,#0; bne loop
 *   and r0,r0,#0xff
 * LDRB zero-extends, so bytes >= 0x80 contribute 0x80..0xff (never negative).
 * The 32-bit accumulator can wrap, which does not affect the low 8 bits.
 */
uint32_t sm_rhash_calc(const char *s)
{
    const unsigned char *p = (const unsigned char *)s;
    uint32_t sum = 0;
    if (p == NULL) {
        return 0; /* reconstruction guard; the original dereferences */
    }
    while (*p != 0) {
        sum += (uint32_t)(*p | 0x20u);
        ++p;
    }
    return sum & 0xffu;
}

/* v7a:0x7d200-0x7d234: c -> (uint8)(c - 0x61) <= 0x19 ? (uint8)(c - 0x20) : c */
static unsigned fold_upper(unsigned c)
{
    return ((unsigned)(uint8_t)(c - 0x61u) <= 0x19u) ? (unsigned)(uint8_t)(c - 0x20u) : c;
}

/*
 * v7a:0x7d1b8-0x7d274 (inner loop of Search). The original's control flow:
 *   - candidate empty  -> match iff key empty                 (0x7d1e0-0x7d1ec, 0x7d250)
 *   - key empty, candidate not -> no match                    (0x7d1b8-0x7d1c8)
 *   - per byte: equal if c1 == c2 or fold(c1) == fold(c2)    (0x7d200-0x7d238)
 *   - candidate ends: match iff key also ends                 (0x7d23c-0x7d258)
 *   - key ends first: no match                                (0x7d1f4-0x7d1fc)
 * which is exactly "same length and fold-equal at every byte". fold() never
 * maps a non-zero byte to 0, so comparing the terminators with fold() is
 * equivalent.
 */
int sm_rhash_name_equal(const char *key, const char *candidate)
{
    const unsigned char *k = (const unsigned char *)key;
    const unsigned char *c = (const unsigned char *)candidate;
    if (k == NULL || c == NULL) {
        return 0;
    }
    for (;;) {
        unsigned a = *k;
        unsigned b = *c;
        if (fold_upper(a) != fold_upper(b)) {
            return 0;
        }
        if (a == 0) {
            return 1;
        }
        ++k;
        ++c;
    }
}

/*
 * v7a:0x7d340-0x7d397
 *   [this+0x808] = fn
 *   for off in 0..0x7f8 step 8: [this+off] = -1; [this+off+4] = 0
 *   r6 = count << 3; [this+0x800] = malloc(r6); memset(pool, 0, r6)
 *   [this+0x804] = [this+0x800]
 * No NULL check on malloc and no check of count in the original.
 */
sm_rhash_status sm_rhash_init(sm_rhash *h, int32_t capacity, sm_rhash_name_fn fn, void *ctx)
{
    uint32_t i;
    if (h == NULL || capacity < 0) {
        return SM_RHASH_ERR_ARG;
    }
    h->name_fn = fn;
    h->name_ctx = ctx;
    for (i = 0; i < SM_RHASH_BUCKETS; ++i) {
        h->buckets[i].value = SM_RHASH_EMPTY;
        h->buckets[i].next = 0;
    }
    h->pool = NULL;
    h->pool_capacity = 0;
    h->pool_used = 0;
    if (capacity > 0) {
        size_t n = (size_t)(uint32_t)capacity;
        if (n > SIZE_MAX / sizeof(sm_rhash_node)) {
            return SM_RHASH_ERR_NO_MEMORY;
        }
        h->pool = (sm_rhash_node *)calloc(n, sizeof(sm_rhash_node)); /* malloc + memset 0 */
        if (h->pool == NULL) {
            return SM_RHASH_ERR_NO_MEMORY;
        }
        h->pool_capacity = (uint32_t)capacity;
    }
    return SM_RHASH_OK;
}

/*
 * v7a:0x7d144-0x7d18f
 *   b = Calc(name)
 *   if [this+b*8] == -1: [this+b*8] = value; return
 *   last = &bucket; while (last->next) last = last->next
 *   last->next = [this+0x804]; *[this+0x804] = value; [this+0x804] += 8
 * The new node's next stays 0 from Init's memset.
 */
sm_rhash_status sm_rhash_add(sm_rhash *h, const char *name, int32_t value)
{
    sm_rhash_node *last;
    uint32_t b;
    if (h == NULL || name == NULL) {
        return SM_RHASH_ERR_ARG;
    }
    b = sm_rhash_calc(name);
    if (h->buckets[b].value == SM_RHASH_EMPTY) {
        h->buckets[b].value = value;
        return SM_RHASH_OK;
    }
    if (h->pool_used >= h->pool_capacity) {
        return SM_RHASH_ERR_FULL;
    }
    last = &h->buckets[b];
    while (last->next != 0) {
        last = &h->pool[last->next - 1u];
    }
    h->pool[h->pool_used].value = value;
    h->pool[h->pool_used].next = 0;
    last->next = h->pool_used + 1u;
    h->pool_used += 1u;
    return SM_RHASH_OK;
}

/*
 * v7a:0x7d190-0x7d277
 *   b = Calc(key); if [this+b*8] == -1 return -1
 *   node = &bucket
 *   loop: cand = (*[this+0x808])(node->value)
 *         if equal(key, cand) return node->value
 *         node = node->next; if node == 0 return -1
 */
int32_t sm_rhash_search(const sm_rhash *h, const char *key)
{
    const sm_rhash_node *node;
    uint32_t b;
    if (h == NULL || key == NULL || h->name_fn == NULL) {
        return SM_RHASH_EMPTY;
    }
    b = sm_rhash_calc(key);
    if (h->buckets[b].value == SM_RHASH_EMPTY) {
        return SM_RHASH_EMPTY;
    }
    node = &h->buckets[b];
    for (;;) {
        const char *cand = h->name_fn(h->name_ctx, node->value);
        if (cand != NULL && sm_rhash_name_equal(key, cand)) {
            return node->value;
        }
        if (node->next == 0 || node->next > h->pool_used) {
            return SM_RHASH_EMPTY;
        }
        node = &h->pool[node->next - 1u];
    }
}

/* v7a:0x7d338: ldr r0,[r0,#0x800]; b free */
void sm_rhash_uninit(sm_rhash *h)
{
    uint32_t i;
    if (h == NULL) {
        return;
    }
    free(h->pool);
    h->pool = NULL;
    h->pool_capacity = 0;
    h->pool_used = 0;
    for (i = 0; i < SM_RHASH_BUCKETS; ++i) {
        h->buckets[i].value = SM_RHASH_EMPTY;
        h->buckets[i].next = 0;
    }
}

/* Chain walk as in cRHash::Report (v7a:0x7d2ac-0x7d2d8). */
size_t sm_rhash_chain(const sm_rhash *h, uint32_t bucket, int32_t *out, size_t out_cap)
{
    const sm_rhash_node *node;
    size_t n = 0;
    if (h == NULL || bucket >= SM_RHASH_BUCKETS || h->buckets[bucket].value == SM_RHASH_EMPTY) {
        return 0;
    }
    node = &h->buckets[bucket];
    for (;;) {
        if (out != NULL && n < out_cap) {
            out[n] = node->value;
        }
        ++n;
        if (node->next == 0 || node->next > h->pool_used) {
            break;
        }
        node = &h->pool[node->next - 1u];
    }
    return n;
}

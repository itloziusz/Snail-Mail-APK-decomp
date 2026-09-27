/*
 * Unit tests for the cRHash reconstruction (reconstructed/assets/src/rhash.c).
 * Expected values are derived by hand from the recovered semantics
 * (docs/ASSET_FORMATS.md section 4); the same cases are also compared
 * against the original ARM32 code by tests/differential/assets/.
 */
#include <stdlib.h>
#include <string.h>

#include "sm_assets/rhash.h"
#include "sm_test.h"

typedef struct name_table {
    const char *const *names;
    int32_t count;
} name_table;

static const char *table_name(void *ctx, int32_t v)
{
    const name_table *t = (const name_table *)ctx;
    if (v < 0 || v >= t->count) {
        return NULL;
    }
    return t->names[v];
}

static void test_calc(void)
{
    char buf[1001];
    CHECK_EQ_INT(sm_rhash_calc(""), 0);
    CHECK_EQ_INT(sm_rhash_calc("A"), 0x61);
    CHECK_EQ_INT(sm_rhash_calc("a"), 0x61);
    CHECK_EQ_INT(sm_rhash_calc("AB"), (0x61 + 0x62) & 0xff);
    CHECK_EQ_INT(sm_rhash_calc("ab"), sm_rhash_calc("AB"));
    /* bytes are unsigned (LDRB): 0xff|0x20 = 0xff, 0x80|0x20 = 0xa0 */
    CHECK_EQ_INT(sm_rhash_calc("\xff"), 0xff);
    CHECK_EQ_INT(sm_rhash_calc("\x80"), 0xa0);
    CHECK_EQ_INT(sm_rhash_calc("\x80\x80"), (0xa0 * 2) & 0xff);
    /* '@' (0x40) and '`' (0x60) fold to the same hash contribution */
    CHECK_EQ_INT(sm_rhash_calc("@"), sm_rhash_calc("`"));
    /* '/' already has bit 5 set; '_' (0x5f) contributes 0x7f */
    CHECK_EQ_INT(sm_rhash_calc("/"), 0x2f);
    CHECK_EQ_INT(sm_rhash_calc("_"), 0x7f);
    /* long string: 1000 * 0x7a = 122000 = 476*256 + 144 */
    memset(buf, 'z', 1000);
    buf[1000] = 0;
    CHECK_EQ_INT(sm_rhash_calc(buf), 144);
    CHECK_EQ_INT(sm_rhash_calc("RANDTABLE.BIN"), sm_rhash_calc("randtable.bin"));
}

static void test_name_equal(void)
{
    CHECK(sm_rhash_name_equal("", ""));
    CHECK(!sm_rhash_name_equal("", "A"));
    CHECK(!sm_rhash_name_equal("A", ""));
    CHECK(sm_rhash_name_equal("abc", "ABC"));
    CHECK(sm_rhash_name_equal("AbC/x.Tga", "aBc/X.tGA"));
    CHECK(!sm_rhash_name_equal("abc", "abcd"));
    CHECK(!sm_rhash_name_equal("abcd", "abc"));
    CHECK(!sm_rhash_name_equal("@", "`"));   /* only a-z fold */
    CHECK(!sm_rhash_name_equal("[", "{"));
    CHECK(!sm_rhash_name_equal("/", "\\")); /* no path normalisation */
    CHECK(sm_rhash_name_equal("\xe1", "\xe1"));
    CHECK(!sm_rhash_name_equal("\xe1", "\xc1")); /* no Latin-1 folding */
}

static void test_add_search(void)
{
    /* index: 0      1      2       3      4     5       6    7 */
    static const char *const names[] = {"ab", "BA", "AB", "@", "`", "", "X/Y", "x/y"};
    name_table t = {names, 8};
    sm_rhash h;
    int32_t chain[16];
    int32_t i;

    CHECK_EQ_INT(sm_rhash_init(&h, 8, table_name, &t), SM_RHASH_OK);
    for (i = 0; i < 8; ++i) {
        CHECK_EQ_INT(sm_rhash_add(&h, names[i], i), SM_RHASH_OK);
    }
    /* "ab","BA","AB" share a bucket; chain order is insertion order */
    CHECK_EQ_INT(sm_rhash_chain(&h, sm_rhash_calc("ab"), chain, 16), 3);
    CHECK_EQ_INT(chain[0], 0);
    CHECK_EQ_INT(chain[1], 1);
    CHECK_EQ_INT(chain[2], 2);
    /* first match wins, case-insensitive */
    CHECK_EQ_INT(sm_rhash_search(&h, "AB"), 0);
    CHECK_EQ_INT(sm_rhash_search(&h, "ab"), 0);
    CHECK_EQ_INT(sm_rhash_search(&h, "Ba"), 1);
    CHECK_EQ_INT(sm_rhash_search(&h, "abc"), -1);
    /* same hash, different names */
    CHECK_EQ_INT(sm_rhash_search(&h, "@"), 3);
    CHECK_EQ_INT(sm_rhash_search(&h, "`"), 4);
    /* empty string lives in bucket 0 */
    CHECK_EQ_INT(sm_rhash_search(&h, ""), 5);
    CHECK_EQ_INT(sm_rhash_search(&h, "x/Y"), 6);
    CHECK_EQ_INT(sm_rhash_search(&h, "X\\Y"), -1);
    CHECK_EQ_INT(sm_rhash_search(&h, "zzz"), -1);
    /* pool usage: heads for buckets calc(ab), calc(@), 0, calc(X/Y) ->
     * 4 heads, 4 appended nodes */
    CHECK_EQ_INT(h.pool_used, 4);
    sm_rhash_uninit(&h);
    CHECK(h.pool == NULL);
    CHECK_EQ_INT(sm_rhash_search(&h, "ab"), -1);
}

static void test_empty_and_capacity(void)
{
    static const char *const names[] = {"A", "a", "b"};
    name_table t = {names, 3};
    sm_rhash h;
    CHECK_EQ_INT(sm_rhash_init(&h, -1, table_name, &t), SM_RHASH_ERR_ARG);

    /* capacity 0: only bucket heads can be filled */
    CHECK_EQ_INT(sm_rhash_init(&h, 0, table_name, &t), SM_RHASH_OK);
    CHECK_EQ_INT(sm_rhash_search(&h, "A"), -1);
    CHECK_EQ_INT(sm_rhash_add(&h, "A", 0), SM_RHASH_OK);
    CHECK_EQ_INT(sm_rhash_add(&h, "b", 2), SM_RHASH_OK);
    CHECK_EQ_INT(sm_rhash_add(&h, "a", 1), SM_RHASH_ERR_FULL); /* original would overrun */
    CHECK_EQ_INT(sm_rhash_search(&h, "a"), 0);
    CHECK_EQ_INT(sm_rhash_search(&h, "B"), 2);
    sm_rhash_uninit(&h);

    /* value -1 stored into an empty head leaves the bucket "empty"
     * (Add tests head == -1, v7a:0x7d154-0x7d15c) */
    CHECK_EQ_INT(sm_rhash_init(&h, 2, table_name, &t), SM_RHASH_OK);
    CHECK_EQ_INT(sm_rhash_add(&h, "A", -1), SM_RHASH_OK);
    CHECK_EQ_INT(sm_rhash_chain(&h, sm_rhash_calc("A"), NULL, 0), 0);
    CHECK_EQ_INT(sm_rhash_add(&h, "a", 1), SM_RHASH_OK);
    CHECK_EQ_INT(h.pool_used, 0);
    CHECK_EQ_INT(sm_rhash_search(&h, "A"), 1);
    /* getter returning NULL (out-of-range value) is skipped, not dereferenced */
    CHECK_EQ_INT(sm_rhash_add(&h, "A", 7), SM_RHASH_OK);
    CHECK_EQ_INT(sm_rhash_calc("\xb0\xb1"), sm_rhash_calc("A")); /* 0xb0+0xb1 = 0x161 */
    CHECK_EQ_INT(sm_rhash_search(&h, "\xb0\xb1"), -1);            /* walks head(1) then node(7) */
    CHECK_EQ_INT(sm_rhash_search(&h, "Q"), -1);
    sm_rhash_uninit(&h);
}

static void test_many_collisions(void)
{
    /* 300 distinct names across all 256 buckets; every name must find itself */
    enum { N = 300 };
    static char storage[N][8];
    static const char *ptrs[N];
    name_table t = {ptrs, N};
    sm_rhash h;
    int32_t i;
    size_t total = 0;
    uint32_t b;
    for (i = 0; i < N; ++i) {
        storage[i][0] = (char)('0' + (i / 100));
        storage[i][1] = (char)('0' + ((i / 10) % 10));
        storage[i][2] = (char)('0' + (i % 10));
        storage[i][3] = (char)(0x80 + (i % 64)); /* high bytes */
        storage[i][4] = 0;
        ptrs[i] = storage[i];
    }
    CHECK_EQ_INT(sm_rhash_init(&h, N, table_name, &t), SM_RHASH_OK);
    for (i = 0; i < N; ++i) {
        CHECK_EQ_INT(sm_rhash_add(&h, ptrs[i], i), SM_RHASH_OK);
    }
    for (i = 0; i < N; ++i) {
        CHECK_EQ_INT(sm_rhash_search(&h, ptrs[i]), i);
    }
    for (b = 0; b < SM_RHASH_BUCKETS; ++b) {
        total += sm_rhash_chain(&h, b, NULL, 0);
    }
    CHECK_EQ_INT(total, N);
    sm_rhash_uninit(&h);
}

int main(void)
{
    test_calc();
    test_name_equal();
    test_add_search();
    test_empty_and_capacity();
    test_many_collisions();
    return sm_test_finish("test_rhash");
}

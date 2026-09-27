/* Minimal assertion helpers for the sm_assets unit tests (no framework). */
#ifndef SM_TEST_H
#define SM_TEST_H

#include <stdio.h>

static int sm_test_failures = 0;
static int sm_test_checks = 0;

#define CHECK(cond)                                                                  \
    do {                                                                             \
        ++sm_test_checks;                                                            \
        if (!(cond)) {                                                               \
            ++sm_test_failures;                                                      \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                            \
    } while (0)

#define CHECK_EQ_INT(a, b)                                                                     \
    do {                                                                                       \
        long long sm_a_ = (long long)(a);                                                      \
        long long sm_b_ = (long long)(b);                                                      \
        ++sm_test_checks;                                                                      \
        if (sm_a_ != sm_b_) {                                                                  \
            ++sm_test_failures;                                                                \
            fprintf(stderr, "%s:%d: CHECK_EQ failed: %s (%lld) != %s (%lld)\n", __FILE__,      \
                    __LINE__, #a, sm_a_, #b, sm_b_);                                           \
        }                                                                                      \
    } while (0)

static int sm_test_finish(const char *name)
{
    if (sm_test_failures != 0) {
        fprintf(stderr, "%s: %d of %d checks FAILED\n", name, sm_test_failures, sm_test_checks);
        return 1;
    }
    printf("%s: all %d checks passed\n", name, sm_test_checks);
    return 0;
}

#endif

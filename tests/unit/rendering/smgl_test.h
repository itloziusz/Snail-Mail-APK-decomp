/* Minimal assertion helpers for the sm_rendering unit tests (no framework). */
#ifndef SMGL_TEST_H
#define SMGL_TEST_H

#include <math.h>
#include <stdio.h>

static int smt_failures = 0;
static int smt_checks = 0;

#define CHECK(cond)                                                                  \
    do {                                                                             \
        ++smt_checks;                                                                \
        if (!(cond)) {                                                               \
            ++smt_failures;                                                          \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                            \
    } while (0)

#define CHECK_EQ_INT(a, b)                                                                \
    do {                                                                                  \
        long long smt_a_ = (long long)(a);                                                \
        long long smt_b_ = (long long)(b);                                                \
        ++smt_checks;                                                                     \
        if (smt_a_ != smt_b_) {                                                           \
            ++smt_failures;                                                               \
            fprintf(stderr, "%s:%d: CHECK_EQ failed: %s (%lld) != %s (%lld)\n", __FILE__, \
                    __LINE__, #a, smt_a_, #b, smt_b_);                                    \
        }                                                                                 \
    } while (0)

#define CHECK_NEAR(a, b, tol)                                                               \
    do {                                                                                    \
        double smt_a_ = (double)(a);                                                        \
        double smt_b_ = (double)(b);                                                        \
        ++smt_checks;                                                                       \
        if (!(fabs(smt_a_ - smt_b_) <= (double)(tol))) {                                    \
            ++smt_failures;                                                                 \
            fprintf(stderr, "%s:%d: CHECK_NEAR failed: %s (%.9g) vs %s (%.9g), tol %g\n",   \
                    __FILE__, __LINE__, #a, smt_a_, #b, smt_b_, (double)(tol));             \
        }                                                                                   \
    } while (0)

static int smt_finish(const char *name)
{
    if (smt_failures != 0) {
        fprintf(stderr, "%s: %d of %d checks FAILED\n", name, smt_failures, smt_checks);
        return 1;
    }
    printf("%s: all %d checks passed\n", name, smt_checks);
    return 0;
}

#endif

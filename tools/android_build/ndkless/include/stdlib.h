#pragma once
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
void* malloc(size_t n);
void* calloc(size_t count, size_t n);
void* realloc(void* p, size_t n);
void free(void* p);
__attribute__((__noreturn__)) void abort(void);
int atoi(const char* s);
double strtod(const char* s, char** end);
long strtol(const char* s, char** end, int base);
void qsort(void* base, size_t count, size_t size, int (*cmp)(const void*, const void*));
void srand48(long seed);
long lrand48(void);
#ifdef __cplusplus
}
#endif

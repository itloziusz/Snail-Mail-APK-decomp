#pragma once
#include <stdarg.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct __sFILE FILE;   /* opaque */
#define EOF (-1)
#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2
extern FILE* stdin;    /* bionic, API 23+ */
extern FILE* stdout;
extern FILE* stderr;
FILE* fopen(const char* path, const char* mode);
FILE* fdopen(int fd, const char* mode);
int fclose(FILE* f);
int fflush(FILE* f);
size_t fread(void* buf, size_t size, size_t count, FILE* f);
size_t fwrite(const void* buf, size_t size, size_t count, FILE* f);
int fseek(FILE* f, long off, int whence);
long ftell(FILE* f);
int fseeko(FILE* f, long off, int whence);   /* off_t is long on LP64 */
long ftello(FILE* f);
int fputc(int c, FILE* f);
int fprintf(FILE* f, const char* fmt, ...) __attribute__((__format__(printf, 2, 3)));
int snprintf(char* buf, size_t n, const char* fmt, ...) __attribute__((__format__(printf, 3, 4)));
int sprintf(char* buf, const char* fmt, ...) __attribute__((__format__(printf, 2, 3)));
int vsnprintf(char* buf, size_t n, const char* fmt, va_list ap) __attribute__((__format__(printf, 3, 0)));
int vsprintf(char* buf, const char* fmt, va_list ap) __attribute__((__format__(printf, 2, 0)));
int vfprintf(FILE* f, const char* fmt, va_list ap) __attribute__((__format__(printf, 2, 0)));
#ifdef __cplusplus
}
#endif

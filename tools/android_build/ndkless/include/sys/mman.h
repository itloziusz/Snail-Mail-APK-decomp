#pragma once
#include <sys/types.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Linux generic (arm64) values, as in bionic's <sys/mman.h> via uapi */
#define PROT_NONE 0x0
#define PROT_READ 0x1
#define PROT_WRITE 0x2
#define PROT_EXEC 0x4
#define MAP_SHARED 0x01
#define MAP_PRIVATE 0x02
#define MAP_ANONYMOUS 0x20
#define MAP_ANON MAP_ANONYMOUS
#define MAP_NORESERVE 0x4000
#define MAP_FAILED ((void*)-1)
void* mmap(void* addr, size_t len, int prot, int flags, int fd, off_t offset);
int munmap(void* addr, size_t len);
int mprotect(void* addr, size_t len, int prot);
#ifdef __cplusplus
}
#endif

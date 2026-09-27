#pragma once
#include <sys/types.h>
#ifdef __cplusplus
extern "C" {
#endif
int close(int fd);
int dup(int fd);
ssize_t read(int fd, void* buf, size_t n);
ssize_t pread(int fd, void* buf, size_t n, off_t offset);
ssize_t pread64(int fd, void* buf, size_t n, off64_t offset);
off_t lseek(int fd, off_t offset, int whence);
#ifndef SEEK_SET
#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2
#endif
#ifdef __cplusplus
}
#endif

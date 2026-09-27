#pragma once
#ifdef __cplusplus
extern "C" {
#endif
int* __errno(void) __attribute__((__const__));
#define errno (*__errno())
#define EINTR 4
#define EIO 5
#define EBADF 9
#define ENOMEM 12
#define EINVAL 22
#ifdef __cplusplus
}
#endif

#pragma once
#include <sys/types.h>
#ifdef __cplusplus
extern "C" {
#endif
struct timeval { time_t tv_sec; suseconds_t tv_usec; };   /* LP64 bionic */
struct timezone { int tz_minuteswest; int tz_dsttime; };
int gettimeofday(struct timeval* tv, struct timezone* tz);
#ifdef __cplusplus
}
#endif

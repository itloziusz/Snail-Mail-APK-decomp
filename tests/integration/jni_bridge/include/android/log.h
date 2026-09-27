// Host stand-in for <android/log.h>: same declarations; the test captures calls.
#pragma once
#ifdef __cplusplus
extern "C" {
#endif
enum { ANDROID_LOG_UNKNOWN = 0, ANDROID_LOG_DEFAULT, ANDROID_LOG_VERBOSE, ANDROID_LOG_DEBUG,
       ANDROID_LOG_INFO, ANDROID_LOG_WARN, ANDROID_LOG_ERROR, ANDROID_LOG_FATAL, ANDROID_LOG_SILENT };
int __android_log_write(int prio, const char* tag, const char* text);
int __android_log_print(int prio, const char* tag, const char* fmt, ...)
    __attribute__((__format__(printf, 3, 4)));
#ifdef __cplusplus
}
#endif

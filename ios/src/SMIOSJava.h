#ifndef SM_IOS_JAVA_H
#define SM_IOS_JAVA_H
#include "aot_host.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct sm_ios_java_config {
    const char *assets_dir;
    const char *files_dir;
    int verbose;
} sm_ios_java_config;
const aot_java_ops *sm_ios_java_ops(const sm_ios_java_config *cfg);
void *sm_ios_java_renderer(void);
void *sm_ios_java_class(const char *name);
void *sm_ios_java_file_descriptor(int fd);
#ifdef __cplusplus
}
#endif
#endif

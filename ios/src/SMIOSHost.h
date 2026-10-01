#ifndef SM_IOS_HOST_H
#define SM_IOS_HOST_H
#ifdef __cplusplus
extern "C" {
#endif
int sm_ios_host_boot(const char *assets_dir, const char *files_dir, int width, int height);
void sm_ios_host_resize(int width, int height);
void sm_ios_host_render(int paused);
void sm_ios_host_touch(int action, float x, float y);
void sm_ios_host_accelerometer(float x, float y, float z);
const char *sm_ios_host_last_error(void);
#ifdef __cplusplus
}
#endif
#endif

#pragma once
/* Only the thread-key API (bionic: pthread_key_t is int). No mutex/cond
 * layouts are declared on purpose (see aot/runtime/aot_platform.h). */
#ifdef __cplusplus
extern "C" {
#endif
typedef int pthread_key_t;
int pthread_key_create(pthread_key_t* key, void (*destructor)(void*));
void* pthread_getspecific(pthread_key_t key);
int pthread_setspecific(pthread_key_t key, const void* value);
#ifdef __cplusplus
}
#endif

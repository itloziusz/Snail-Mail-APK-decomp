/*
 * Minimal platform abstraction for the AOT runtime, chosen so the runtime
 * builds identically on glibc hosts and on Android/bionic without relying on
 * ABI details that would have to be re-declared by hand:
 *  - locks: a spinlock on compiler atomics (no pthread_mutex_t layout);
 *  - thread-local state: pthread keys (no ELF TLS, which bionic only supports
 *    from API 29, and no emulated-TLS runtime helpers).
 */
#ifndef AOT_PLATFORM_H
#define AOT_PLATFORM_H

#include <pthread.h>

typedef struct aot_lock {
    int v;
} aot_lock;
#define AOT_LOCK_INIT {0}

static inline void aot_lock_acquire(aot_lock *l)
{
    while (__atomic_exchange_n(&l->v, 1, __ATOMIC_ACQUIRE)) {
        while (__atomic_load_n(&l->v, __ATOMIC_RELAXED)) {
            /* spin: critical sections are a few dozen instructions */
        }
    }
}
static inline void aot_lock_release(aot_lock *l) { __atomic_store_n(&l->v, 0, __ATOMIC_RELEASE); }

/* thread-local pointer slot */
typedef struct aot_tls {
    pthread_key_t key;
    int ready;
    aot_lock lock;
} aot_tls;
#define AOT_TLS_INIT {0, 0, AOT_LOCK_INIT}

static inline void *aot_tls_get(aot_tls *t)
{
    if (!__atomic_load_n(&t->ready, __ATOMIC_ACQUIRE)) {
        return (void *)0;
    }
    return pthread_getspecific(t->key);
}
static inline void aot_tls_set(aot_tls *t, void *v)
{
    if (!__atomic_load_n(&t->ready, __ATOMIC_ACQUIRE)) {
        aot_lock_acquire(&t->lock);
        if (!t->ready) {
            pthread_key_create(&t->key, (void (*)(void *))0);
            __atomic_store_n(&t->ready, 1, __ATOMIC_RELEASE);
        }
        aot_lock_release(&t->lock);
    }
    pthread_setspecific(t->key, v);
}

#endif /* AOT_PLATFORM_H */

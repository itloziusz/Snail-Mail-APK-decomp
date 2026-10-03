/*
 * Port options: display adaptations layered over the translated game.
 *
 * The original draws its 2D layer on a 640x480 logical canvas stretched to the
 * whole screen (G0RenderFont, v7a:0x7b7dc: x * width/640, y * height/480), and
 * keeps its 3D vertical field of view whatever the screen shape (gluPerspective,
 * v7a:0x7b0c8, called with the real aspect by G0RenderCamera). On a 19.5:9 phone
 * that stretches every menu and HUD element 1.6x and widens the gameplay
 * camera to about 150 degrees horizontally.
 *
 * Every option has an "original" value that reproduces the game exactly; the
 * wrappers then pass straight through (docs/TESTING.md, GL-trace differential).
 */
#ifndef SM_PORT_H
#define SM_PORT_H

#ifdef __cplusplus
extern "C" {
#endif

enum { SM_PORT_FIT_STRETCH = 0, SM_PORT_FIT_ADAPTIVE = 1 };
enum { SM_PORT_FOV_ORIGINAL = 0, SM_PORT_FOV_ADAPTIVE = 1 };
enum { SM_PORT_REFRESH_60 = 0, SM_PORT_REFRESH_120 = 1, SM_PORT_REFRESH_VRR = 2 };
enum { SM_PORT_CONTROLS_LEGACY = 0, SM_PORT_CONTROLS_SMOOTH = 1 };

typedef struct sm_port_settings {
    int fit;      /* SM_PORT_FIT_*: 2D layer stretched (original) or aspect-correct */
    int fov;      /* SM_PORT_FOV_* */
    int refresh;  /* SM_PORT_REFRESH_*: presentation, applied by the platform layer */
    int controls; /* SM_PORT_CONTROLS_* */
    int smoothness; /* 0..100 percent; retained even in Legacy mode */
} sm_port_settings;

/* All options at their original values. */
void sm_port_settings_original(sm_port_settings *s);
/* The port's defaults (what a fresh install uses). */
void sm_port_settings_default(sm_port_settings *s);

void sm_port_set(const sm_port_settings *s);
void sm_port_get(sm_port_settings *s);

/* Called (on the game thread) when a port settings page changes a value,
 * so the platform layer can persist it and apply any refresh change. */
typedef void (*sm_port_listener)(const sm_port_settings *s, void *user);
void sm_port_set_listener(sm_port_listener fn, void *user);

/* Refresh modes the platform can present (bit 1 << SM_PORT_REFRESH_*). The
 * Display page offers the Refresh button only when more than 60 Hz is
 * possible. Default: 60 Hz only. */
void sm_port_set_refresh_modes(unsigned mask);
unsigned sm_port_refresh_modes(void);

/* Touch input in surface pixels -> the pixels the original expects, so its own
 * scaling (JNIMouseEvent: x * 640/width) lands on the adapted 2D layout. */
void sm_port_map_touch(float *x, float *y);

/* Once per rendered frame, before nativeRender. */
void sm_port_frame_begin(void);
/* Surface-local insets, set on the game thread; only name-entry uses them. */
void sm_port_safe_area(float left, float top, float right, float bottom);
/* Thread-safe development overlay snapshot: count, selected, then 9 floats
 * per key (code, reference rectangle, final pixel rectangle). */
int sm_port_nameentry_debug(float *dst, int capacity);
/* Game-thread host diagnostics: per widget, kind, reference and pixel bounds. */
int sm_port_ui_debug(float *dst, int capacity);
int sm_port_nameentry_input_active(void);
/* Load opening artwork before the GL surface starts. Pixels are RGBA,
 * top-first and copied. GL-context reset is called on the rendering thread. */
void sm_port_splash_asset(int slot, int width, int height, const unsigned char *rgba);
void sm_port_splash_context_created(void);
/* Development host isolation view: only the continuous background. */
void sm_port_background_debug_only(int enabled);

#ifdef __cplusplus
}
#endif

#endif /* SM_PORT_H */

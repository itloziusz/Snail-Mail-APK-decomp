/* Shared between the port modules; not part of the platform-facing API. */
#ifndef SM_PORT_INTERNAL_H
#define SM_PORT_INTERNAL_H

#include <stdint.h>

#include "port.h"

/* Guest address of a symbol of the original binary; fatal if missing. */
uint32_t port_sym(const char *name);

/* A menu changed the settings: store and notify the platform listener. */
void port_settings_changed(const sm_port_settings *s);

/* Whether full-screen backdrops should keep covering the whole screen
 * (behind a 3D scene: 3D geometry was drawn last frame) rather than be placed
 * on the 2D canvas with dimmed margins (menus). */
int port_backdrop_fills_screen(void);

#endif

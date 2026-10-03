/* Source-art coordinates, shared by rendering, touch and navigation. */
#ifndef SM_NAMEENTRY_LAYOUT_H
#define SM_NAMEENTRY_LAYOUT_H
#define SM_NAME_KEYS 64
#define SM_NAME_REF_W 480.0f
#define SM_NAME_REF_H 320.0f
typedef struct sm_name_key { float x0, y0, x1, y1; unsigned char code; } sm_name_key;
typedef struct sm_name_canvas { float scale, x, y; } sm_name_canvas;
sm_name_canvas sm_name_fit(float w, float h, float left, float top, float right, float bottom);
sm_name_canvas sm_name_scene_fit(float w, float h, float left, float top, float right, float bottom);
int sm_name_hit(const sm_name_key *keys, int n, float x, float y);
int sm_name_next(const sm_name_key *keys, int n, int selected, int dx, int dy);
int sm_name_parse(const char *text, sm_name_key *keys, int capacity);
#endif

#pragma once
#ifdef __cplusplus
extern "C" {
#endif
double acos(double);
double atan(double);
double atan2(double, double);
double cos(double);
float cosf(float);
double exp(double);
double fabs(double);
float fabsf(float);
double floor(double);
float floorf(float);
double fmod(double, double);
double pow(double, double);
double sin(double);
float sinf(float);
double sqrt(double);
float sqrtf(float);
double tan(double);
float tanf(float);
/* bionic defines the classification macros on the compiler builtins */
#define isfinite(x) __builtin_isfinite(x)
#ifdef __cplusplus
}
#endif

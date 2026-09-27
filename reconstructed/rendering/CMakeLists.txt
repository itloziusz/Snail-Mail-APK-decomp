# sm_rendering: GLES 1.1 fixed-function emulation on GLES 2.0 for the 45 GLES 1.x
# functions imported by libsnailmail.so v7a (sha256 e43bc913...a466;
# aot/runtime/sm_gl_api.h, analysis/native/gl_usage.json). See docs/RENDERING.md.
#
# Targets:
#   sm_rendering_math  GL-free matrix math and matrix stacks. Always built, so
#                      its unit test also runs in the AArch64 cross build.
#   sm_rendering       the emulation layer; needs GLES2 headers + libGLESv2.
#                      Skipped (STATUS message) when they are not found.
#   sm_host_egl        host-only offscreen EGL context + PNG dump (sources in
#                      <repo>/host); needs EGL. Never built for Android.
get_filename_component(SM_RENDERING_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)

add_library(sm_rendering_math STATIC src/smgl_math.c)
target_include_directories(sm_rendering_math PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(sm_rendering_math PRIVATE m)
set_target_properties(sm_rendering_math PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)

find_path(SM_GLES2_INCLUDE_DIR GLES2/gl2.h)
find_library(SM_GLESV2_LIBRARY NAMES GLESv2)
find_path(SM_EGL_INCLUDE_DIR EGL/egl.h)
find_library(SM_EGL_LIBRARY NAMES EGL)

if(NOT SM_GLES2_INCLUDE_DIR OR NOT SM_GLESV2_LIBRARY)
  message(STATUS "sm_rendering: GLESv2 headers/library not found; skipping sm_rendering, "
                 "sm_host_egl and the GL rendering tests (sm_rendering_math is still built)")
  return()
endif()

add_library(sm_rendering STATIC src/smgl.c)
target_include_directories(sm_rendering PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${SM_RENDERING_REPO_ROOT}/aot/runtime
  ${SM_GLES2_INCLUDE_DIR})
target_link_libraries(sm_rendering PUBLIC sm_rendering_math ${SM_GLESV2_LIBRARY})
set_target_properties(sm_rendering PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)

if(ANDROID)
  return()
endif()
if(NOT SM_EGL_INCLUDE_DIR OR NOT SM_EGL_LIBRARY)
  message(STATUS "sm_rendering: EGL not found; skipping sm_host_egl and the GL rendering tests")
  return()
endif()
# The top-level build only auto-adds reconstructed/* and tests/*, so the host
# helper directory is added from here with an explicit binary dir.
add_subdirectory(${SM_RENDERING_REPO_ROOT}/host ${CMAKE_CURRENT_BINARY_DIR}/host)

# sm_host_egl: host-only offscreen GLES2 context (EGL surfaceless platform +
# pbuffer) and PNG framebuffer dump for tests and tools. Never shipped.
# Added from reconstructed/rendering/CMakeLists.txt, which has already found
# SM_EGL_INCLUDE_DIR, SM_EGL_LIBRARY, SM_GLES2_INCLUDE_DIR and SM_GLESV2_LIBRARY.
add_library(sm_host_egl STATIC egl_offscreen.c)
target_include_directories(sm_host_egl PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${SM_EGL_INCLUDE_DIR}
  ${SM_GLES2_INCLUDE_DIR})
target_link_libraries(sm_host_egl PUBLIC ${SM_EGL_LIBRARY} ${SM_GLESV2_LIBRARY})
# Keep eglplatform.h from pulling in X11 headers.
target_compile_definitions(sm_host_egl PRIVATE EGL_NO_X11 MESA_EGL_NO_X11_HEADERS)
set_target_properties(sm_host_egl PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)

find_package(PNG QUIET)
if(PNG_FOUND)
  target_link_libraries(sm_host_egl PRIVATE PNG::PNG)
  target_compile_definitions(sm_host_egl PRIVATE SM_HOST_HAVE_PNG=1)
else()
  message(STATUS "sm_host_egl: libpng not found; sm_host_write_png() will report failure")
endif()

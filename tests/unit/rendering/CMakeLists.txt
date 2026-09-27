# Unit tests for sm_rendering (reconstructed/rendering). See docs/RENDERING.md §6.

# GL-free matrix math: built and run in every configuration (incl. AArch64).
add_executable(test_smgl_math test_smgl_math.c)
target_link_libraries(test_smgl_math PRIVATE sm_rendering_math)
add_test(NAME rendering.matrix_math COMMAND test_smgl_math)

if(NOT TARGET sm_rendering OR NOT TARGET sm_host_egl)
  message(STATUS "rendering tests: GLES2/EGL unavailable; only rendering.matrix_math is built")
  return()
endif()

# Scene tests: one executable, one CTest entry per scene. Each scene creates
# its own 64x64 EGL pbuffer and exits 77 (SKIP) if no context can be made.
add_executable(test_smgl_render test_smgl_render.c)
target_link_libraries(test_smgl_render PRIVATE sm_rendering sm_host_egl)
find_package(PNG QUIET)
if(PNG_FOUND)
  target_link_libraries(test_smgl_render PRIVATE PNG::PNG)
  target_compile_definitions(test_smgl_render PRIVATE SMT_HAVE_PNG=1)
endif()

set(SM_RENDER_SCENES backend clear untextured ortho vbo_short texture fog pushpop blend depth state png)
foreach(scene ${SM_RENDER_SCENES})
  add_test(NAME rendering.${scene} COMMAND test_smgl_render ${scene} ${CMAKE_CURRENT_BINARY_DIR})
  set_tests_properties(rendering.${scene} PROPERTIES SKIP_RETURN_CODE 77)
endforeach()

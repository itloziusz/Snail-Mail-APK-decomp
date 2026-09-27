# Ahead-of-time translated game: runtime (hand-written, shipping), the
# generated translation of the original binary (aot/generated, gitignored,
# produced by tools/aot/arm2c.py from the owner's APK), and the Linux host
# runner. See docs/ARCHITECTURE.md "AOT execution path".
option(SM_AOT_TRACE_CALLS "Compile translated code with guest function-entry tracing" OFF)

set(SM_AOT_GENERATED ${CMAKE_CURRENT_SOURCE_DIR}/generated)

add_library(sm_aot_runtime STATIC
  runtime/aot_core.c
  runtime/aot_libc.c
  runtime/aot_gl.c
  runtime/aot_jni.c)
target_include_directories(sm_aot_runtime PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/runtime)
find_package(Threads REQUIRED)
target_link_libraries(sm_aot_runtime PUBLIC Threads::Threads m)
if(SM_AOT_TRACE_CALLS)
  target_compile_definitions(sm_aot_runtime PUBLIC AOT_TRACE_CALLS=1)
endif()

if(NOT EXISTS ${SM_AOT_GENERATED}/aot_table.c)
  message(STATUS "aot: ${SM_AOT_GENERATED} not generated; run tools/aot/arm2c.py "
                 "(needs the original APK). Skipping the translated game and host runner.")
  return()
endif()

file(GLOB SM_AOT_GEN_SOURCES ${SM_AOT_GENERATED}/aot_funcs_*.c)
# The runtime and the generated tables reference each other, so they form one
# static library (sm_aot_game = runtime sources + translated sources).
add_library(sm_aot_game STATIC
  runtime/aot_core.c runtime/aot_libc.c runtime/aot_gl.c runtime/aot_jni.c
  ${SM_AOT_GEN_SOURCES} ${SM_AOT_GENERATED}/aot_table.c)
target_include_directories(sm_aot_game PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/runtime ${SM_AOT_GENERATED})
target_link_libraries(sm_aot_game PUBLIC Threads::Threads m)
if(SM_AOT_TRACE_CALLS)
  target_compile_definitions(sm_aot_game PUBLIC AOT_TRACE_CALLS=1)
endif()
# Generated code: optimise, keep ARM FP semantics (no FMA contraction), wrap
# signed arithmetic, no strict aliasing; silence warnings about unused locals.
set_source_files_properties(${SM_AOT_GEN_SOURCES} ${SM_AOT_GENERATED}/aot_table.c PROPERTIES
  COMPILE_OPTIONS "-O2;-w;-ffp-contract=off;-fwrapv;-fno-strict-aliasing")

# ---------------------------------------------------------------- host runner
find_package(PNG QUIET)
find_package(JPEG QUIET)
find_package(ZLIB QUIET)
if(NOT PNG_FOUND OR NOT JPEG_FOUND OR NOT ZLIB_FOUND)
  message(STATUS "aot: libpng/libjpeg/zlib not all found; skipping snailmail_host")
  return()
endif()
set(SM_HOST_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../host)
add_executable(snailmail_host ${SM_HOST_DIR}/main.c ${SM_HOST_DIR}/java_emul.c ${SM_HOST_DIR}/null_gl.c)
target_include_directories(snailmail_host PRIVATE ${SM_HOST_DIR})
target_link_libraries(snailmail_host PRIVATE sm_aot_game PNG::PNG JPEG::JPEG ZLIB::ZLIB)
if(TARGET sm_rendering AND TARGET sm_host_egl)
  target_link_libraries(snailmail_host PRIVATE sm_rendering sm_host_egl)
  target_compile_definitions(snailmail_host PRIVATE SM_HOST_HAVE_GL=1)
endif()

# Reconstructed Snail Mail native code: host + AArch64 build of the
# evidence-backed modules and their tests. The Android shell build lives in
# android/ and consumes the same module targets.
cmake_minimum_required(VERSION 3.22)
project(snailmail_reconstructed LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(SM_SANITIZE "Build with ASan+UBSan (host builds)" OFF)
option(SM_BUILD_TESTS "Build unit tests" ON)

# Keep FP semantics strict until reference behaviour is characterised
# (docs/ABI_PORTING.md). -ffp-contract=off prevents fused multiply-add
# contraction that ARMv7 VFPv2 (the original target) could not perform.
add_compile_options(-Wall -Wextra -Wshadow -Wpointer-arith -Wcast-align
                    -Wformat=2 -ffp-contract=off -fno-strict-aliasing)
if(SM_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer
                      -fno-sanitize-recover=undefined)
  add_link_options(-fsanitize=address,undefined)
endif()

if(SM_BUILD_TESTS)
  enable_testing()
endif()

file(GLOB SM_MODULE_DIRS LIST_DIRECTORIES true ${CMAKE_CURRENT_SOURCE_DIR}/reconstructed/*)
foreach(dir ${SM_MODULE_DIRS})
  if(EXISTS ${dir}/CMakeLists.txt)
    add_subdirectory(${dir})
  endif()
endforeach()

if(SM_BUILD_TESTS)
  file(GLOB SM_TEST_DIRS LIST_DIRECTORIES true ${CMAKE_CURRENT_SOURCE_DIR}/tests/unit/*)
  foreach(dir ${SM_TEST_DIRS})
    if(EXISTS ${dir}/CMakeLists.txt)
      add_subdirectory(${dir})
    endif()
  endforeach()
endif()

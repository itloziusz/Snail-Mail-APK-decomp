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
option(SM_BUILD_TESTS "Build unit tests" ON)\noption(SM_BUILD_IOS_APP "Build the native iOS application target" OFF)

# Semantics-preserving flags, identical to android/app/src/main/cpp so host,
# qemu-AArch64 and device builds agree (docs/ABI_PORTING.md):
#  -ffp-contract=off  VFPv2 VMLA/VMLS round twice; AArch64 FMADD would not
#  -funsigned-char    plain char was unsigned on ARM EABI (x86 hosts differ)
#  -fwrapv            signed overflow wrapped in the original machine code
add_compile_options(-Wall -Wextra -Wshadow -Wpointer-arith -Wcast-align
                    -Wformat=2 -ffp-contract=off -funsigned-char -fwrapv
                    -fno-strict-aliasing)
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

# Ahead-of-time translated game + runtime + host runner (after the modules, so
# sm_rendering / sm_host_egl targets exist).
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/aot/CMakeLists.txt)
  add_subdirectory(aot)
endif()

if(SM_BUILD_TESTS)
  find_package(Python3 COMPONENTS Interpreter)
  if(Python3_Interpreter_FOUND)
    # Packaging/contract checks (host tools; independent of the target ISA).
    add_test(NAME platform.elf_alignment_selftest
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/validation/platform/check_elf_alignment.py --self-test)
    add_test(NAME platform.shell_parity
             COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/validation/platform/check_shell_parity.py)
    set_tests_properties(platform.shell_parity PROPERTIES SKIP_RETURN_CODE 77)
  endif()
  file(GLOB SM_TEST_DIRS LIST_DIRECTORIES true ${CMAKE_CURRENT_SOURCE_DIR}/tests/unit/*
                                                ${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/*)
  foreach(dir ${SM_TEST_DIRS})
    if(EXISTS ${dir}/CMakeLists.txt)
      add_subdirectory(${dir})
    endif()
  endforeach()
endif()

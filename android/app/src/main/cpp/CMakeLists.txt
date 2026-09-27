# Native build of the Snail Mail arm64-v8a port (invoked by AGP
# externalNativeBuild; see docs/BUILDING.md).
#
# Produces libsnailmail.so = JNI bridge (jni_bridge.cpp) + every reconstructed
# module found under <repo>/reconstructed/<area>/ that provides a
# CMakeLists.txt defining the static library target sm_<area>
# (docs/CONVENTIONS.md "Build/test layout").
cmake_minimum_required(VERSION 3.22.1)
project(snailmail_android LANGUAGES C CXX)

if(NOT ANDROID_ABI STREQUAL "arm64-v8a")
  message(FATAL_ERROR "The Snail Mail port is arm64-v8a only (got ANDROID_ABI='${ANDROID_ABI}').")
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# This file lives in <repo>/android/app/src/main/cpp.
get_filename_component(SM_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../../../.." ABSOLUTE)

# Semantics-preserving flags (docs/ABI_PORTING.md):
#  -ffp-contract=off : ARMv7 VFPv2 VMLA rounds the product; AArch64 FMADD would not.
#  -funsigned-char   : plain char was unsigned on ARM EABI (also the AArch64
#                      default; stated explicitly so host and device agree).
#  -fno-strict-aliasing : the original code type-puns through casts freely.
#  -fwrapv           : signed overflow wrapped on the original target.
add_compile_options(-Wall -Wextra -ffp-contract=off -funsigned-char -fno-strict-aliasing -fwrapv)

set(SM_MODULE_LIBS "")
file(GLOB SM_MODULE_DIRS LIST_DIRECTORIES true "${SM_REPO_ROOT}/reconstructed/*")
foreach(dir ${SM_MODULE_DIRS})
  if(IS_DIRECTORY "${dir}" AND EXISTS "${dir}/CMakeLists.txt")
    get_filename_component(area "${dir}" NAME)
    add_subdirectory("${dir}" "${CMAKE_CURRENT_BINARY_DIR}/reconstructed/${area}")
    if(TARGET sm_${area})
      list(APPEND SM_MODULE_LIBS sm_${area})
    endif()
  endif()
endforeach()
message(STATUS "Snail Mail reconstructed modules linked: ${SM_MODULE_LIBS}")

add_library(snailmail SHARED jni_bridge.cpp)
target_link_libraries(snailmail PRIVATE ${SM_MODULE_LIBS} GLESv1_CM log android)
target_link_options(snailmail PRIVATE
  "-Wl,-z,max-page-size=16384"   # 16 KB page-size devices (checked by tools/validation/platform/check_elf_alignment.py)
  "-Wl,-z,noexecstack"
  "-Wl,-z,relro,-z,now"
  "-Wl,--no-undefined"
  "-Wl,--build-id=sha1")

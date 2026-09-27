# Runs android/app/src/main/cpp/jni_bridge.cpp (unchanged) against a fake
# JNIEnv on the host or, with the AArch64 toolchain, under qemu-aarch64.
set(SM_JDK_INCLUDE "" CACHE PATH "Directory containing jni.h (JDK include/)")
if(NOT SM_JDK_INCLUDE)
  foreach(cand $ENV{JAVA_HOME}/include /usr/lib/jvm/java-21-openjdk-amd64/include)
    if(EXISTS ${cand}/jni.h)
      set(SM_JDK_INCLUDE ${cand})
      break()
    endif()
  endforeach()
endif()
if(NOT SM_JDK_INCLUDE)
  message(STATUS "jni.h not found: skipping tests/integration/jni_bridge")
  return()
endif()
add_executable(test_jni_bridge test_jni_bridge.cpp
               ${CMAKE_SOURCE_DIR}/android/app/src/main/cpp/jni_bridge.cpp)
target_include_directories(test_jni_bridge PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/include ${SM_JDK_INCLUDE} ${SM_JDK_INCLUDE}/linux)
target_compile_definitions(test_jni_bridge PRIVATE SM_REPO_ROOT="${CMAKE_SOURCE_DIR}")
target_link_libraries(test_jni_bridge PRIVATE sm_platform)
add_test(NAME integration.jni_bridge COMMAND test_jni_bridge)
set_tests_properties(integration.jni_bridge PROPERTIES SKIP_RETURN_CODE 77)

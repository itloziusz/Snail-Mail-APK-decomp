# sm_platform: Android-independent platform pieces reconstructed from
# libsnailmail.so v7a (sha256 e43bc913...a466). JNI glue stays in
# android/app/src/main/cpp/jni_bridge.cpp and calls into this library.
add_library(sm_platform STATIC
  src/dat.c
)
target_include_directories(sm_platform PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(sm_platform PUBLIC sm_assets)
set_target_properties(sm_platform PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)

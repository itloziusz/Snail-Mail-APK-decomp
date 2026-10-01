if(NOT CMAKE_SYSTEM_NAME STREQUAL "iOS")
  message(FATAL_ERROR "ios/ is only built with -DCMAKE_SYSTEM_NAME=iOS")
endif()
if(NOT TARGET sm_aot_game)
  message(FATAL_ERROR "sm_aot_game is unavailable. Generate aot/generated from your original APK first (tools/ios_build/build_ios.py).")
endif()
if(NOT TARGET sm_rendering)
  message(FATAL_ERROR "sm_rendering is unavailable. The iOS bootstrap requires the GLES2 compatibility backend.")
endif()

set(SM_IOS_ASSETS_DIR "" CACHE PATH "Prepared Snail Mail iOS asset directory (asm.mp3 plus audio)")
if(NOT SM_IOS_ASSETS_DIR OR NOT EXISTS "${SM_IOS_ASSETS_DIR}/asm.mp3")
  message(FATAL_ERROR "SM_IOS_ASSETS_DIR must point at the prepared assets directory containing asm.mp3")
endif()

find_library(SM_UIKIT UIKit REQUIRED)
find_library(SM_FOUNDATION Foundation REQUIRED)
find_library(SM_QUARTZCORE QuartzCore REQUIRED)
find_library(SM_OPENGLES OpenGLES REQUIRED)
find_library(SM_COREMOTION CoreMotion REQUIRED)
find_library(SM_AVFOUNDATION AVFoundation REQUIRED)
find_library(SM_AUDIOTOOLBOX AudioToolbox REQUIRED)
find_library(SM_IMAGEIO ImageIO REQUIRED)
find_library(SM_COREGRAPHICS CoreGraphics REQUIRED)

file(GLOB SM_IOS_SOURCES CONFIGURE_DEPENDS src/*.mm)
file(GLOB SM_IOS_HEADERS CONFIGURE_DEPENDS src/*.h)
file(GLOB SM_IOS_ASSETS CONFIGURE_DEPENDS "${SM_IOS_ASSETS_DIR}/*")

add_executable(SnailMailIOS MACOSX_BUNDLE ${SM_IOS_SOURCES} ${SM_IOS_HEADERS} ${SM_IOS_ASSETS})
target_include_directories(SnailMailIOS PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/aot/runtime
  ${CMAKE_SOURCE_DIR}/aot/port
  ${CMAKE_SOURCE_DIR}/reconstructed/rendering/include)
target_link_libraries(SnailMailIOS PRIVATE
  sm_aot_game sm_rendering
  ${SM_UIKIT} ${SM_FOUNDATION} ${SM_QUARTZCORE} ${SM_OPENGLES}
  ${SM_COREMOTION} ${SM_AVFOUNDATION} ${SM_AUDIOTOOLBOX}
  ${SM_IMAGEIO} ${SM_COREGRAPHICS} z)

foreach(asset IN LISTS SM_IOS_ASSETS)
  set_source_files_properties("${asset}" PROPERTIES MACOSX_PACKAGE_LOCATION "Resources/assets")
endforeach()

configure_file(${CMAKE_CURRENT_SOURCE_DIR}/Info.plist.in ${CMAKE_CURRENT_BINARY_DIR}/Info.plist @ONLY)
set_target_properties(SnailMailIOS PROPERTIES
  MACOSX_BUNDLE TRUE
  MACOSX_BUNDLE_INFO_PLIST ${CMAKE_CURRENT_BINARY_DIR}/Info.plist
  XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER "com.itloziusz.snailmail.port.ios"
  XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY "1,2"
  XCODE_ATTRIBUTE_IPHONEOS_DEPLOYMENT_TARGET "15.0"
  XCODE_ATTRIBUTE_CLANG_ENABLE_OBJC_ARC "YES"
  XCODE_ATTRIBUTE_ENABLE_BITCODE "NO"
  XCODE_ATTRIBUTE_CODE_SIGN_STYLE "Automatic")

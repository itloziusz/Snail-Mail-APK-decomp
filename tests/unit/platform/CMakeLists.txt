add_executable(test_platform_dat test_dat.c)
target_link_libraries(test_platform_dat PRIVATE sm_platform)
target_include_directories(test_platform_dat PRIVATE ${CMAKE_SOURCE_DIR}/tests/unit/assets)
target_compile_definitions(test_platform_dat PRIVATE
  SM_FIXTURE_DIR="${CMAKE_SOURCE_DIR}/tests/fixtures/assets"
  SM_REPO_ROOT="${CMAKE_SOURCE_DIR}")
add_test(NAME platform.dat_synthetic COMMAND test_platform_dat synthetic)
add_test(NAME platform.dat_real_apk COMMAND test_platform_dat real)
set_tests_properties(platform.dat_real_apk PROPERTIES SKIP_RETURN_CODE 77)

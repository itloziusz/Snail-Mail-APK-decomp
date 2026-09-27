# Unit tests for sm_assets (reconstructed/assets). Plain C, no framework.
add_executable(test_sm_assets_rhash test_rhash.c)
target_link_libraries(test_sm_assets_rhash PRIVATE sm_assets)
add_test(NAME assets.rhash COMMAND test_sm_assets_rhash)

add_executable(test_sm_assets_archive test_asm_archive.c)
target_link_libraries(test_sm_assets_archive PRIVATE sm_assets)
add_test(NAME assets.archive_synthetic
         COMMAND test_sm_assets_archive
                 ${PROJECT_SOURCE_DIR}/tests/fixtures/assets/synthetic_small.asm)

# Real archive: SKIP (exit 77) when work/apk_unzip/assets/asm.mp3 is absent.
add_executable(test_sm_assets_real test_asm_real.c)
target_link_libraries(test_sm_assets_real PRIVATE sm_assets)
add_test(NAME assets.archive_real
         COMMAND test_sm_assets_real ${PROJECT_SOURCE_DIR}/work/apk_unzip/assets/asm.mp3)
set_tests_properties(assets.archive_real PROPERTIES SKIP_RETURN_CODE 77)

# Host-side check of the Python extractor against the synthetic fixture
# (runs the host interpreter even in cross builds).
find_package(Python3 COMPONENTS Interpreter QUIET)
if(Python3_Interpreter_FOUND)
  add_test(NAME assets.extractor_fixture
           COMMAND ${Python3_EXECUTABLE}
                   ${PROJECT_SOURCE_DIR}/tools/extraction/asm_archive.py
                   --self-test ${PROJECT_SOURCE_DIR}/tests/fixtures/assets/synthetic_small.asm)
endif()

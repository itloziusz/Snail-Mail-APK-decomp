# sm_assets: asm.mp3 archive directory + cRHash name lookup, reconstructed
# from libsnailmail.so v7a (sha256 e43bc913...a466). See docs/ASSET_FORMATS.md.
add_library(sm_assets STATIC
  src/rhash.c
  src/asm_archive.c
)
target_include_directories(sm_assets PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
set_target_properties(sm_assets PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)

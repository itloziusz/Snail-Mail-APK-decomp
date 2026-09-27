# Differential test: `sm_assets` vs. the original ARM32 code

`run_diff.py` compares the reconstructed module `reconstructed/assets/` with the **original v7a machine code**. The reference binary is sha256 `e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466`, and it runs in Unicorn through `tools/validation/arm32_ref/armref.py`, which is analysis-only.

```sh
python3 tests/differential/assets/run_diff.py      # writes result.json; exit 0 iff 0 mismatches
```

The run takes about 6 s.

## How it works

**Reconstructed side.** `sm_assets_cli.c` and the module sources are compiled with the host `cc` (`-std=c11 -O1 -Wall -Wextra -Werror`) into a temporary directory. The CLI prints results as text: hashes, search results, bucket chains, per-record fields and spans, and image bytes.

**Original side.** The same inputs go to the original functions inside Unicorn:

| Suite | Original code | Inputs | Compared exactly |
|---|---|---|---|
| `calc` | `cRHash::Calc` v7a:0x7d114 | Every byte 0x01–0xff; 961 byte pairs from boundary bytes; the real names, lower-cased and case-swapped; 3,000 random strings; strings of 1 KB, 4 KB and 64 KB; one 16,843,010-byte string whose 32-bit sum wraps | hash values |
| `table` | `cRHash::Init/Add/Search` (0x7d340/0x7d144/0x7d190), with the getter supplied as a Python callback | 417 synthetic names (collisions, exact and case duplicates, the empty string, bytes ≥ 0x80); 1,800 probe keys | search results, **every bucket chain in order**, pool nodes used |
| `jnidatinit[…]` | `JNIDatInit` v7a:0x15244 → `DatHashGetString` → `RShellDatFind` v7a:0x1b920 | Runs twice: on the real archive and on the synthetic fixture. The archive sits behind a fake fd after 0x1235 bytes of padding (start offset 0x1235). Probes: all names; lower-case, case-swapped, truncated and suffixed names; `\` in place of `/`; `./` prefixes; the empty string; random strings. | globals (`gJavaAssetStart/Length`, `gDatFP`); the guest directory image (in-place pointer fix-ups equal `gDat + name_offset` from C; all other bytes equal the file); lookup indices; `gDatHash` chains |
| `loader[…]` | `JAVA_RegisterFunctions`, `RShellLoadFile` v7a:0x1b980 → `PfmLoadFileDat` v7a:0x14c74 → `JAVAC_UnZip/UnJpg/UnPng` | Every record reachable by name (733 real, 6 synthetic) | `RShellLoadFile(name,(void*)-1)` returns C's `data_offset`, and `*size` equals C's `decoded_size`; raw bytes equal `archive[C read_span]`; the Java method matches C's codec; the Java input array equals `archive[C read_span]`; the Java output array length equals C's `decoded_size`; zip output bytes; TGA header plus placed rows equal the C `sm_asm_build_tga_header` + `sm_asm_image_place_rows` output; nothing is written past C's `output_extent`; unknown codecs cause no Java call and leave the buffer untouched |

**Java stand-ins.** The Java side of the loader is replaced by Python stand-ins registered as JNI handlers:

- `JAVAUnZip` decodes the first zip entry, with the same copy loop as `ADRenderer.JAVAUnZip`.
- `JAVAUnPng` and `JAVAUnJpg` fill the output array with an aperiodic synthetic pattern of `w*h*4` bytes. Width and height come from the image's own header.

So the harness validates the native marshalling, the field semantics, the TGA header and the row flip. It does **not** validate Android's bitmap decoding.

## Result (`result.json`)

`result.json` records:

- the reference binary sha256, the harness, the Unicorn version and the relocation counts;
- sha256 of every reconstructed source and the compiler;
- the archive and fixture sha256;
- per suite: inputs, comparisons, mismatches and the first divergence.

Last run: **17,894 comparisons, 0 mismatches.**

## Does the harness catch errors? (mutation check)

Deliberately broken copies of the module were run with `SM_DIFF_RECON_DIR=<copy>`; in that mode `result.json` is not written.

| Mutation | Detected |
|---|---|
| m1: sign-extend bytes in `Calc` | **no, and cannot be.** The result is `sum & 0xff` and sign extension only changes bits 8–31, so signedness is unobservable (EV-ASSET-0012). |
| m2: no a–z case folding in the name comparison | yes (1,866 mismatches) |
| m3: prepend to the chain instead of appending | yes (167; the chain order differs) |
| m4: no vertical row flip | yes (30) |
| m5: raw records read `stored_size` instead of `decoded_size` | yes (2). Caught on the fixture's `DATA/SHORT.BIN`: the original read exactly 2 of 5 bytes. |
| m6: TGA descriptor `0x28` instead of `0x08` | yes (30) |

## Limits

- Agreement holds for the tested inputs only.
- Malformed archives are rejected by the C parser but are undefined behaviour in the original, so they cannot be compared.
- Bitmap decoding, and the JNI and GL behaviour of a real device, are out of scope. Checking them needs an original-execution capture.

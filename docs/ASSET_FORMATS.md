# Asset formats: `assets/asm.mp3`

This spec was recovered from the code that reads the archive, not from what the bytes happen to look like. Every claim cites a v7a address range or an archive byte range. The evidence IDs (`EV-ASSET-nnnn`) point into `analysis/evidence/assets.jsonl`.

| Short name | Identity |
|---|---|
| `v7a` (reference) | `lib/armeabi-v7a/libsnailmail.so`, sha256 `e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466` |
| `v5` | `lib/armeabi/libsnailmail.so`, sha256 `96dbeaeb…8136`. Only `cRHash::Calc` was re-verified here (EV-ASSET-0030) |
| archive | `assets/asm.mp3`, 8,188,993 B, sha256 `59740ec3a2cd1f7e9ff250e3c6128ffff922193925e3d0316db22a4118861b6a`, stored uncompressed in the APK |

Confidence words follow `docs/CONVENTIONS.md`: **established**, **likely** and **hypothesis**.

---

## 1. Summary

`asm.mp3` is a flat archive. It has three parts:

1. A header holding the record count.
2. A table of 24-byte records.
3. A block of NUL-terminated names, followed by the payloads.

Every offset in the archive counts from the archive's first byte. The Android build never scrambles the archive (EV-ASSET-0027).

The loading path works like this:

- The game loads the whole directory block into memory once.
- It indexes the names with its own 256-bucket hash, `cRHash`.
- It finds files by case-insensitive exact name.
- Each payload is one of four codecs:
  - **raw**: read as-is;
  - **zip**: a one-entry zip, decoded by Java `java.util.zip`;
  - **jpeg** and **png**: decoded by Android `BitmapFactory` and converted into an in-memory TGA image.

---

## 2. On-disk format

All integers are little-endian. "Offset" means a byte offset from the first byte of the archive. On device, that is the file descriptor's start offset inside the APK.

### 2.1 Header and directory block

| Archive offset | Width | Name | Signedness in original | Meaning | Evidence | Confidence |
|---|---|---|---|---|---|---|
| `0x00` | u32 | `count` | **signed** (`cmp`/`ble`/`bgt` at v7a:0x153d8, 0x15410) | number of records | EV-ASSET-0006 | established |
| `0x08` | u32 | `dir_size` | passed to malloc / fread (unsigned size) | size of the directory block `[0, dir_size)` loaded into memory | EV-ASSET-0003 | established |
| `0x04 + 24*i` | 24 B | record *i* | — | see 2.2 | EV-ASSET-0006, -0010 | established |
| `table_end = 4 + 24*count` … `dir_size` | bytes | names | bytes compared unsigned (LDRB) | NUL-terminated names that the records point to | EV-ASSET-0006 | established |

**`dir_size` doubles as a record field.** `dir_size` is not a separate header field. JNIDatInit reads a 244-byte probe and takes the u32 at probe+8 (v7a:0x15354-0x1536c). When `count ≥ 1`, that word is `record[0].data_offset`. The format therefore assumes that record 0's payload starts right after the directory. In the real archive, `dir_size` = 34,701 = `0x878d`.

The original performs no checks at all: no fread result checks, no bounds checks, and no NUL checks.

### 2.2 Record (24 bytes at `4 + 24*i`)

| Rec offset | Width | Field | Consumer and use | Evidence | Confidence |
|---|---|---|---|---|---|
| +0 | u32 | `name_offset` | JNIDatInit rewrites it in place to `gDat + name_offset` (v7a:0x153f4-0x153fc). DatHashGetString returns it (v7a:0x19970). | EV-ASSET-0006, -0008 | established |
| +4 | u32 | `data_offset` | PfmLoadFileDat: `fseek(fp, data_offset + gJavaAssetStart)`. RShellLoadFile(name, (void*)-1) returns it. | EV-ASSET-0010, -0018, -0022 | established |
| +8 | u32 | `decoded_size` | `*outSize`, the `malloc` size when the caller passes NULL, the raw read length, and the size of the Java output array | EV-ASSET-0010, -0017, -0022 | established (use); the name is an interpretation |
| +12 | u32 | `stored_size` | Bytes read from the archive for codecs 1–3 (`malloc` + `fread`); the size of the Java input array | EV-ASSET-0017 | established (use) |
| +16 | u32 | `codec` | PfmLoadFileDat switch (`cmp #3; addls pc,…`, v7a:0x14cb4) | EV-ASSET-0017 | established |
| +20 | u16 | `width` | `ldrh [rec,#0x14]`: TGA header width and the JAVAC_Un* `w` argument | EV-ASSET-0010, -0021 | established |
| +22 | u16 | `height` | `ldrh [rec,#0x16]`: TGA header height and the row count for row placement | EV-ASSET-0010, -0020 | established |

The original passes `data_offset`, `decoded_size` and `stored_size` around as `int`. PfmLoadFileDat's prototype is `(void*, int, int, int, int, unsigned short, unsigned short)`.

### 2.3 Codec values (PfmLoadFileDat, v7a:0x14c74-0x14e98)

| `codec` | Bytes read at `data_offset` | Decoder | Output written to `dst` |
|---|---|---|---|
| 0 raw | **`decoded_size`** (not `stored_size`; see note) | none | `decoded_size` bytes |
| 1 zip | `stored_size` | `JAVAC_UnZip` → Java `JAVAUnZip([B out, [B in)`, method-table entry 14. It copies the first `ZipInputStream` entry. | up to `decoded_size` bytes (the Java output array length) |
| 2 jpeg | `stored_size` | `JAVAC_UnJpg` → `JAVAUnJpg` (entry 15), which calls `BitmapFactory` with ARGB_8888 and then `copyPixelsToBuffer` | 18-byte TGA header + `(decoded_size / height) * height` bytes of rows, flipped vertically |
| 3 png | `stored_size` | `JAVAC_UnPng` → `JAVAUnPng` (entry 16), same as jpeg | same as jpeg |
| > 3 | nothing | none | nothing. The buffer is left untouched and no error is reported. |

Note on raw reads: case 0 reads `decoded_size` bytes. This was checked by running the original code on a synthetic record with `decoded_size` = 2 and `stored_size` = 5; exactly 2 bytes were read (EV-ASSET-0017). In the real archive, `decoded_size == stored_size` for all 636 raw records.

**Synthesised TGA header** (v7a:0x14d2c-0x14d74, EV-ASSET-0021):

- bytes `00 00 02 00 00 00 00 00 00 00 00 00`
- `u16 width`
- `u16 height`
- `20` (32 bpp)
- `08` (8 alpha bits, bottom-left origin)

**Row placement** (v7a:0x1457c-0x145e0, EV-ASSET-0020):

- `row_bytes = __aeabi_idiv(decoded_size, height)`, a signed division that happens *before* the `height > 0` test.
- Java row *r* is copied to `dst + 18 + (height-1-r)*row_bytes`.
- Java writes `w*h*4` bitmap bytes. The rest of its `decoded_size`-byte array stays zero.

In the archive, `decoded_size = w*h*4 + 19` for all 28 images. The 19 is the 18-byte header plus 1; what the extra byte is for is a **hypothesis** (probably slack).

**Pitfall:** `row_bytes` equals `w*4` only while `19 < height`. A shorter image would come out sheared. No real image is affected: the smallest height is 64.

---

## 3. Loading path (as recovered)

```
Java SnailMailActivity.onCreate                       (SnailMailActivity.java:70-73)
  AssetFileDescriptor fd = openFd("asm.mp3")
  JNIDatInit(fd.getFileDescriptor(), (int)startOffset, (int)length)
      │
      ▼
JNIDatInit                                            v7a:0x15244-0x15450
  FindClass("java/io/FileDescriptor"), GetFieldID("descriptor","I"), GetIntField
  gJavaAssetFid = dup(fd); gJavaAssetStart = start; gJavaAssetLength = length
  gDatFP = fdopen(dupfd, "rb")                (NULL → message, return)
  fseek(start); fread(probe,1,244); dir_size = probe.u32[2]
  gDat = malloc(dir_size); fseek(start); fread(gDat,1,dir_size)
  cRHash::Init(&gDatHash, gDat.count, DatHashGetString)
  for i in 0..count-1: rec_i.name_offset += gDat  (in place!); cRHash::Add(&gDatHash, name_i, i)
      │
      ▼
RShellLoadFile(name, buf, *size)                      v7a:0x1b980  (17 callers via the (char*,int*) overload v7a:0x1baec, 16 direct)
  rec = RShellDatFind(name)                           v7a:0x1b920 → cRHash::Search → gDat+4+24*i
  miss → "FileMissing from gDat" → RShellConvertFileName (A-Z→a-z, '/'→'_') → PfmLoadFile (Java JAVAFindFile/JAVALoadFile)
  *size = decoded_size
  buf == (void*)-1 → return data_offset
  buf == NULL      → buf = malloc(decoded_size)
  PfmLoadFileDat(buf, data_offset, decoded_size, stored_size, codec, width, height)   v7a:0x14c74
      │
      ▼
runtime consumers (partial)
  textures: G0TextureLoad v7a:0x7b24c reads the TGA image (w @+0xc, h @+0xe, bpp @+0x10);
            glTexImage2D(GL_TEXTURE_2D, 0, bpp==32 ? GL_RGBA : GL_RGB, w, h, 0, same, GL_UNSIGNED_BYTE, tga+0x12)
            (EV-ASSET-0029, likely)
  other callers of RShellLoadFile: cRObject::Load/ReLoad, ObjectTextLoad ("%s/_Object.txt"), cRDirectX::Load ("X/%s"),
            cRGalaxy::Open ("Galaxy/_Galaxy.txt"), cRSubTracks::Init ("Levels/%s"), cRSMTracks::Import/Replace,
            cRVoiceManager::Init ("Voice/_Voice.txt"), cRSubHighScore::MiniLoad, cRSplashManager::SetBar,
            gRMathRand2Init (RANDTABLE.BIN). Their payload formats (.SMO, .TXT grammars, .X) are NOT analysed here.
```

Other facts about this path:

- **Existence checks.** `RShellFindFile(name, false)` (v7a:0x1bf68) uses `RShellDatFind` for existence checks and logs `"Missing Dat File %s"`. With `true`, or when there is no `gDat`, it asks Java instead.
- **Teardown.** Java `onStop` calls `JNIDatUnInit` (v7a:0x13c70). That function only calls `wprintf`, which is a no-op in this build (v7a:0x19948). `RShellUnInit` (fclose + `gDat = 0`) has no callers. So the archive stays open and the directory stays in memory for the whole process lifetime (EV-ASSET-0028).
- **Sounds and music are not in `asm.mp3`.** `JAVALoadSample` and `JAVAPlayMusic` open `assets/<name>.ogg` directly through `AssetManager` (ADRenderer.java:88, :171).
- **Dead scrambled-archive path.** `RShellDatInit`, `RShellLoadFileHeader` and `RShellUnScrambleDat` belong to a scrambled-archive path used on other platforms. Nothing in v7a references them (EV-ASSET-0027).

---

## 4. `cRHash`: the name index

The class is 0x80c bytes; `gDatHash` is 2060 bytes (EV-ASSET-0013):

| Offset | Content |
|---|---|
| `+0x000` | 256 × `{ int32 value; node *next; }`. Bucket heads; `value == -1` means empty. |
| `+0x800` | `node *pool`: `malloc(count*8)`, zeroed |
| `+0x804` | `node *pool_next`: bump allocator |
| `+0x808` | `char *(*get_name)(int value)`. It is `DatHashGetString` for the directory and `TexturesFunctionGetName` for `cRTextures` (v7a:0x1cb58). |

The table stores **only the int values** (record indices). Names are read back through `get_name`.

| Method | v7a range | Semantics | Evidence |
|---|---|---|---|
| `Calc(char*)` | 0x7d114-0x7d143 | `(Σ (byte \| 0x20)) & 0xff` over the bytes before the NUL. Bytes are read with **LDRB** (zero-extended); the empty string hashes to 0. Upper and lower case letters hash alike, and so do some non-letters (`'@'` and `` '`' ``). | EV-ASSET-0011 |
| `Init(int n, getter)` | 0x7d340-0x7d397 | All heads become `{-1, NULL}`; the pool is `malloc(n<<3)` and memset to 0; no NULL or overflow checks | EV-ASSET-0013 |
| `Add(char*, int v)` | 0x7d144-0x7d18f | If the head's value is -1, the head takes `v`. Otherwise `v` is **appended at the chain tail** from the bump pool. There is no duplicate check and no pool bound check. `Add(x, -1)` into an empty head leaves the bucket empty. | EV-ASSET-0014 |
| `Search(char*)` | 0x7d190-0x7d277 | Walks the chain head-first in insertion order. It returns the value of the **first** entry whose `get_name(value)` equals the key, or -1. Equality means same length and, byte by byte, equal after folding only `a`–`z` to `A`–`Z`. | EV-ASSET-0015 |
| `UnInit()` | 0x7d338-0x7d33f | `free(pool)`. The heads are not reset. | EV-ASSET-0016 |
| `Report()` | 0x7d278-0x7d337 | Diagnostic: prints each bucket's chain length and the maximum. | — |

Lookup semantics that follow from this:

- Lookup is case-insensitive for ASCII letters only.
- There is no path normalisation: `\` does not match `/`, and `./x` does not match `x`.
- There is no extension handling.
- A missing name returns -1, so `RShellDatFind` returns NULL.
- When names are duplicated, **the lowest record index wins**.

**Signedness.** The hash's result depends only on the low 8 bits of each byte, and the comparison only tests byte equality and whether a byte is in `a`–`z`. So plain-`char` signedness is unobservable here. A sign-extending mutation of the reconstruction produced zero differences against the original (EV-ASSET-0012). The reconstruction still uses `unsigned char`, to mirror LDRB.

---

## 5. Contents of this archive (observations, not format rules)

Produced by `tools/extraction/asm_archive.py`; full metadata is in `analysis/assets/asm_manifest.json` (EV-ASSET-0023..0026).

**Layout**

| Item | Value |
|---|---|
| Records observed | 734 |
| `dir_size` | 34,701 |
| Record table | `[4, 17620)` |
| Names | `[17620, 34701)`, packed in record order with no slack |
| Payloads | contiguous in record order from 34,701 to EOF (8,188,993) |
| Gaps, overlaps, trailing bytes | none |
| Names | all printable ASCII and upper case; longest is 37 bytes |
| Hash buckets used | 240 of 256; longest chain 11 |

**Codecs**

| Codec | Records | Content (sniffed by magic or structure) |
|---|---|---|
| raw (0) | 636 | 272 text files (2 end with NUL bytes: `DATA/LEVELSDIR.TXT`, `DATA/SEGMENTSDIR.TXT`); 364 `.SMO` binaries with no magic, classified **unknown** |
| zip (1) | 70 | Each is a complete one-entry deflate zip with a central directory. The decoded length always equals `decoded_size` and the CRC always matches. Contents: 66 TGA files (65 with the TGA 2.0 footer; 1, `SPRITES/WHITE.TGA`, with no footer), 2 unknown binaries (`RANDTABLE.BIN`, `DATA/NORMALSTABLE.BIN`), 1 DirectX `.X` text file, 1 XML file |
| jpeg (2) | 11 | 512×512. The JPEG SOF dims equal the record dims. |
| png (3) | 17 | 16×64 up to 512×512. The PNG IHDR dims equal the record dims. |

**Anomalies**

- **Unreachable duplicate.** Record 54 `SPRITES/MOUSE.TGA` has the same name as record 43, with different bytes. Lookups return 43, so record 54 can never be loaded by name. The original code confirms this.
- **`dims` on zip records.** On codec 1, `dims` holds the TGA's own dims (66 of 66 match) or 0 for non-images, but the loader ignores it for codec 1.
- **Zip entry names.** The entry name inside each zip is `RShellConvertFileName(record name)` (70 of 70). How the builder produced these is a **hypothesis**.

---

## 6. ARM64 / porting hazards

1. **32-bit pointers written into the directory.** JNIDatInit overwrites `name_offset` in place with a 32-bit pointer (v7a:0x153fc). On LP64 this cannot be done: a pointer does not fit in the field. The reconstruction keeps the on-disk fields as `uint32_t` and stores host `const char *` separately.
2. **32-bit pointers in the hash.** `cRHash` stores node pointers in 32-bit slots and sizes its pool as `count << 3`. The reconstruction uses 1-based `uint32_t` pool indices instead.
3. **32-bit offset arithmetic.** `data_offset + gJavaAssetStart`, the sizes, and `count` are `int` on ARM32, and Java truncates `getStartOffset()` to int. Compute file offsets in 64 bits and validate them.
4. **No validation in the original.** There are no fread checks, bounds checks or NUL checks, and a negative `count` is not handled. The reconstruction rejects these inputs with explicit status codes (section 7).
5. **Decoder behaviour.** Image decoding goes through Android `BitmapFactory` with ARGB_8888. By default its output is **premultiplied RGBA** (hypothesis, not verified here), and JPEG output is decoder-dependent. A native port needs its own decoder, and its pixel output cannot be byte-identical without a capture from the original app.
6. **Division by zero.** When `height == 0`, the row-size division happens before the height check (v7a:0x14584).

---

## 7. Reconstruction and tests

Status per function uses the vocabulary in `docs/CONVENTIONS.md`.

| Original | Reconstruction (`reconstructed/assets/`) | Status |
|---|---|---|
| `cRHash::Calc` v7a:0x7d114 | `sm_rhash_calc` | RECONSTRUCTED, UNIT_TESTED, REFERENCE_VALIDATED |
| `cRHash::Init/Add/Search/UnInit` | `sm_rhash_init/add/search/uninit` (+ `sm_rhash_chain`, the same walk as Report) | RECONSTRUCTED, UNIT_TESTED, REFERENCE_VALIDATED (results and chain structure) |
| JNIDatInit directory load + index (v7a:0x1533c-0x15414) | `sm_asm_directory_parse`, `sm_asm_directory_build_index` | RECONSTRUCTED, UNIT_TESTED, REFERENCE_VALIDATED (directory image, lookups) |
| `DatHashGetString` / `RShellDatFind` | `dir_name_fn` (static) / `sm_asm_directory_find[_index]` | RECONSTRUCTED, UNIT_TESTED, REFERENCE_VALIDATED |
| PfmLoadFileDat read span and output extent | `sm_asm_entry_read_span`, `sm_asm_entry_output_extent` | RECONSTRUCTED, UNIT_TESTED, REFERENCE_VALIDATED |
| TGA header / row placement (v7a:0x14d2c, 0x1457c) | `sm_asm_build_tga_header`, `sm_asm_image_place_rows` | RECONSTRUCTED, UNIT_TESTED, REFERENCE_VALIDATED (with a synthetic Java stand-in) |
| JAVAUnZip / JAVAUnPng / JAVAUnJpg (Java) | not reconstructed | DISCOVERED |

The reconstruction differs from the original only on inputs the original mishandles:

- It rejects: `count ≥ 2^31`; a record table larger than `dir_size`; `name_offset` outside `[table_end, dir_size)`; a missing NUL; a buffer shorter than 12 bytes or than `dir_size`.
- `Add` refuses to overflow the pool.
- `Search` skips a NULL name.
- `UnInit` resets the bucket heads.

Tests:

| Test | Where | What it checks |
|---|---|---|
| Unit | `tests/unit/assets/` (CTest `assets.*`) | Synthetic archives built in the test; malformed inputs; the committed fixture `tests/fixtures/assets/synthetic_small.asm` (generated by `make_synthetic_asm.py`) |
| Real archive | `assets.archive_real` | Every name resolves to the first record with an equal name; spans are contiguous. Exits 77 (SKIP) when the archive is absent. |
| Differential | `tests/differential/assets/run_diff.py` → `result.json` | Unicorn runs the original code on the same inputs; see that directory's README |

Extractor: `python3 tools/extraction/asm_archive.py` writes `analysis/assets/asm_manifest.json` and bytes under `analysis/assets/extracted/` (gitignored). Its `--self-test FIXTURE` mode runs in CTest.

---

## 8. Unknowns and open questions

- **`.SMO` payloads (364 records).** No magic; the format is unknown. The consumers are probably `cRObject::Load` / `cRDirectX` (not analysed).
- **Text grammars.** The `_Object.txt`, level and segment `.TXT` grammars, and the `.X` usage, are not analysed.
- **`RANDTABLE.BIN` and `DATA/NORMALSTABLE.BIN`.** Their semantics are unknown; they are consumed by `gRMathRand2Init` (v7a:0x19670) and others.
- **Exact pixels from `BitmapFactory`.** Premultiplication, JPEG decoder and dithering (`inDither = true`) are all unknown. They need an original-execution capture on a device.
- **The `+1` in `decoded_size = w*h*4 + 19`.** Hypothesis: slack.
- **24-bit zipped TGAs.** Whether they are swapped to RGB before `glTexImage2D` (`RShellSwapTgaRGB` exists) was not traced.
- **`cRTextures`' own `cRHash`.** It uses a different getter and is only noted here.
- **v5.** Only `Calc` was re-verified; JNIDatInit and PfmLoadFileDat in v5 were not compared.

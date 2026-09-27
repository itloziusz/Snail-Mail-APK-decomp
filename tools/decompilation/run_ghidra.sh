#!/usr/bin/env bash
# Reproducible Ghidra headless pipeline for the Snail Mail native libraries.
#
# Usage: tools/decompilation/run_ghidra.sh <v7a|v5> <analyze|export|all>
#
#   analyze : (re)create work/ghidra/SnailMail_<bin>.gpr, import the library with
#             the pinned processor, run SmPreAnalysis.java (ARM-only context +
#             $d regions as data), then Ghidra's default auto-analysis.
#   export  : open the saved program (-noanalysis) and run SmExport.java, which
#             writes decompiled C, call graph, string xrefs and a summary.
#
# Environment: GHIDRA_HOME (default /opt/re-tools/ghidra_11.4.2_PUBLIC),
#              MAXMEM (default 6G), GHIDRA_CPUS (default 3), DECOMP_TIMEOUT (s, default 60)
# Only one Ghidra instance should run at a time on the 4-core/15 GB box.
set -euo pipefail
BIN="${1:?binary v7a|v5}"
PHASE="${2:-all}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
GHIDRA_HOME="${GHIDRA_HOME:-/opt/re-tools/ghidra_11.4.2_PUBLIC}"
export MAXMEM="${MAXMEM:-6G}"
CPUS="${GHIDRA_CPUS:-3}"
TIMEOUT="${DECOMP_TIMEOUT:-60}"
case "$BIN" in
  v7a) LIB="$REPO/work/apk_unzip/lib/armeabi-v7a/libsnailmail.so"; LANG_ID="ARM:LE:32:v7";
       SHA=e43bc913e9ba99abd2fed4d2cee40d4a33a951ecbcf8ca154d099cc8cabaa466 ;;
  v5)  LIB="$REPO/work/apk_unzip/lib/armeabi/libsnailmail.so"; LANG_ID="ARM:LE:32:v5t";
       SHA=96dbeaeb20c60d687301ca769656727467371489db5e3ed744a93248bc8d8136 ;;
  *) echo "unknown binary $BIN" >&2; exit 2 ;;
esac
echo "$SHA  $LIB" | sha256sum -c --quiet -
PROJ_DIR="$REPO/work/ghidra"
PROJ="SnailMail_$BIN"
SCRIPTS="$REPO/tools/decompilation/ghidra"
mkdir -p "$PROJ_DIR"
HEADLESS="$GHIDRA_HOME/support/analyzeHeadless"

if [[ "$PHASE" == analyze || "$PHASE" == all ]]; then
  python3 "$REPO/tools/decompilation/ghidra_prepare.py" "$BIN" "$PROJ_DIR/${BIN}_data_regions.txt"
  rm -rf "$PROJ_DIR/$PROJ.gpr" "$PROJ_DIR/$PROJ.rep"
  "$HEADLESS" "$PROJ_DIR" "$PROJ" \
     -import "$LIB" -processor "$LANG_ID" -cspec default \
     -loader ElfLoader -loader-imagebase 0 \
     -scriptPath "$SCRIPTS" \
     -preScript SmPreAnalysis.java "$PROJ_DIR/${BIN}_data_regions.txt" \
     -max-cpu "$CPUS" -analysisTimeoutPerFile 7200 \
     -log "$PROJ_DIR/${BIN}_analyze.log" -scriptlog "$PROJ_DIR/${BIN}_analyze_script.log"
fi

if [[ "$PHASE" == export || "$PHASE" == all ]]; then
  OUT="$REPO/analysis/native"
  mkdir -p "$OUT/generated/decomp/$BIN"
  "$HEADLESS" "$PROJ_DIR" "$PROJ" -process libsnailmail.so -noanalysis -readOnly \
     -scriptPath "$SCRIPTS" \
     -postScript SmExport.java "$BIN" "$SHA" "$OUT" "$TIMEOUT" \
     -max-cpu "$CPUS" \
     -log "$PROJ_DIR/${BIN}_export.log" -scriptlog "$PROJ_DIR/${BIN}_export_script.log"
fi

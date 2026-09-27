#!/bin/sh
# Build and run the ABI C checks natively (host), as AArch64 under
# qemu-aarch64, and as ARMv7-A VFP softfp under qemu-arm.
# Artifacts go to work/scratch-platform/abi-c (gitignored).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
OUT=${OUT:-$REPO/work/scratch-platform/abi-c}
mkdir -p "$OUT"
FLAGS=${FLAGS:-"-O2 -std=c11 -Wall"}
build() { # name cc extra-flags src...   (honours FLAGS from the environment)
  n=$1; cc=$2; shift 2; ex=$1; shift
  if command -v "$cc" >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    $cc $FLAGS $ex -o "$OUT/$n" "$@" -lm && echo "built $n"
  else
    echo "SKIP $n: $cc not found"
  fi
}
run() { # label runner binary args
  lab=$1; shift
  echo "---- $lab"
  "$@" || echo "(exit $?)"
}
for t in host aarch64 armv7; do
  case $t in
    host) CC=gcc; EX=""; RUN="";;
    aarch64) CC=aarch64-linux-gnu-gcc; EX=""; RUN="qemu-aarch64 -L /usr/aarch64-linux-gnu";;
    armv7) CC=arm-linux-gnueabi-gcc; EX="-march=armv7-a -mfpu=vfp -mfloat-abi=softfp -marm"; RUN="qemu-arm -L /usr/arm-linux-gnueabi";;
  esac
  build rand48_check.$t $CC "$EX" "$HERE/rand48_check.c"
  build rand48_unseeded.$t $CC "$EX" "$HERE/rand48_unseeded.c"
  build f2i_check.$t $CC "$EX" "$HERE/f2i_check.c"
  # GCC's default C dialect (gnu17) implies -ffp-contract=fast; -std=c11 would imply off.
  FLAGS="-O2 -Wall" build fpcontract_default.$t $CC "$EX" "$HERE/fpcontract_demo.c"
  build fpcontract_off.$t $CC "$EX -ffp-contract=off" "$HERE/fpcontract_demo.c"
done
if command -v clang >/dev/null 2>&1; then
  for v in default off; do
    ex="--target=aarch64-linux-gnu -fuse-ld=bfd"
    [ $v = off ] && ex="$ex -ffp-contract=off"
    # shellcheck disable=SC2086
    clang -O2 $ex -o "$OUT/fpcontract_clang_$v.aarch64" "$HERE/fpcontract_demo.c" 2>/dev/null \
      && echo "built fpcontract_clang_$v.aarch64" || echo "SKIP clang aarch64 build ($v)"
  done
fi
for t in host aarch64 armv7; do
  case $t in
    host) RUN="";;
    aarch64) RUN="qemu-aarch64 -L /usr/aarch64-linux-gnu";;
    armv7) RUN="qemu-arm -L /usr/arm-linux-gnueabi";;
  esac
  [ -x "$OUT/rand48_check.$t" ] || continue
  U=$($RUN "$OUT/rand48_unseeded.$t")
  run "$t rand48" env RAND48_UNSEEDED_FIRST="$U" $RUN "$OUT/rand48_check.$t" "$OUT/RandTable.predicted.$t.bin"
  run "$t f2i" $RUN "$OUT/f2i_check.$t"
  run "$t fp-contract default" $RUN "$OUT/fpcontract_default.$t"
  run "$t fp-contract=off" $RUN "$OUT/fpcontract_off.$t"
done
if command -v aarch64-linux-gnu-objdump >/dev/null 2>&1; then
  echo "---- aarch64 fused multiply-add instructions in dot():"
  for v in default off clang_default clang_off; do
    [ -f "$OUT/fpcontract_$v.aarch64" ] || continue
    n=$(aarch64-linux-gnu-objdump -d "$OUT/fpcontract_$v.aarch64" | awk '/<dot>:/,/ret/' | grep -c -E 'fn?m(add|sub)')
    echo "   fpcontract_$v.aarch64: $n fmadd/fmsub"
    [ "${v#clang}" != "$v" ] && run "aarch64 $v" qemu-aarch64 -L /usr/aarch64-linux-gnu "$OUT/fpcontract_$v.aarch64"
  done
fi
cmp -s "$OUT/RandTable.predicted.host.bin" "$OUT/RandTable.predicted.aarch64.bin" 2>/dev/null && echo "predicted RandTable identical host vs aarch64"

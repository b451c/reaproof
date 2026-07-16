#!/usr/bin/env bash
# Build the ReaProof reference native extension.
#   build_ext.sh <output_dir>
# Produces <output_dir>/reaper_reaproof_testext.dylib (REAPER loads
# UserPlugins/reaper_*.dylib as extensions).
set -euo pipefail
OUT="${1:?output dir}"
SRC="$(cd "$(dirname "$0")" && pwd)/reaproof_testext.c"
ARCH="${ARCH:-arm64}"

mkdir -p "$OUT"
clang -O2 -Wall -dynamiclib -arch "$ARCH" \
      -o "$OUT/reaper_reaproof_testext.dylib" "$SRC"
echo "built $OUT/reaper_reaproof_testext.dylib"

# faulty variant for the U1 fault-forensics gate (deliberate null deref)
FAULT_SRC="$(cd "$(dirname "$0")" && pwd)/reaproof_crashext.c"
clang -O0 -Wall -dynamiclib -arch "$ARCH" \
      -o "$OUT/reaper_reaproof_crashext.dylib" "$FAULT_SRC"
echo "built $OUT/reaper_reaproof_crashext.dylib"

# hook-only variant for the U3 battery (loads, registers nothing observable)
NOOP_SRC="$(cd "$(dirname "$0")" && pwd)/reaproof_noopext.c"
clang -O2 -Wall -dynamiclib -arch "$ARCH" \
      -o "$OUT/reaper_reaproof_noopext.dylib" "$NOOP_SRC"
echo "built $OUT/reaper_reaproof_noopext.dylib"

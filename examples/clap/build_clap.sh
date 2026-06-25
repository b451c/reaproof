#!/usr/bin/env bash
# Build the ReaProof CLAP reference subjects as proper macOS .clap bundles.
#   build_clap.sh <clap_include_dir> <output_dir>
# Produces <output_dir>/reaproof_gain.clap and reaproof_gain_broken.clap (bundles).
set -euo pipefail
INC="${1:?clap include dir}"
OUT="${2:?output dir}"
SRC="$(cd "$(dirname "$0")" && pwd)/reaproof_gain.c"
ARCH="${ARCH:-arm64}"

build_one() {
  local name="$1"; shift
  local bundle="$OUT/$name.clap"
  rm -rf "$bundle"
  mkdir -p "$bundle/Contents/MacOS"
  clang -O2 -Wall -dynamiclib -arch "$ARCH" -I "$INC" "$@" \
        -o "$bundle/Contents/MacOS/$name" "$SRC"
  cat > "$bundle/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>$name</string>
  <key>CFBundleIdentifier</key><string>com.reaproof.$name</string>
  <key>CFBundleName</key><string>$name</string>
  <key>CFBundlePackageType</key><string>BNDL</string>
  <key>CFBundleVersion</key><string>0.0.1</string>
  <key>CFBundleShortVersionString</key><string>0.0.1</string>
</dict></plist>
PLIST
  printf 'BNDL????' > "$bundle/Contents/PkgInfo"
  echo "built $bundle"
}

build_one reaproof_gain
build_one reaproof_gain_broken -DREAPROOF_BROKEN=1

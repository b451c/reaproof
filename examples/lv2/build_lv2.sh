#!/usr/bin/env bash
# Build the ReaProof LV2 reference subjects as .lv2 bundles.
#   build_lv2.sh <output_dir>
# Produces <out>/reaproof_gain.lv2 and <out>/reaproof_gain_broken.lv2.
set -euo pipefail
OUT="${1:?output dir}"
SRC="$(cd "$(dirname "$0")" && pwd)/reaproof_gain_lv2.c"
ARCH="${ARCH:-arm64}"

build_one() {
  local name="$1"; shift
  local uri_suffix="$1"; shift
  local bundle="$OUT/$name.lv2"
  rm -rf "$bundle"
  mkdir -p "$bundle"
  clang -O2 -Wall -dynamiclib -arch "$ARCH" "$@" \
        -o "$bundle/$name.dylib" "$SRC"
  cat > "$bundle/manifest.ttl" <<TTL
@prefix lv2:  <http://lv2plug.in/ns/lv2core#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<https://reaproof.dev/plugins/gain$uri_suffix>
    a lv2:Plugin ;
    lv2:binary <$name.dylib> ;
    rdfs:seeAlso <$name.ttl> .
TTL
  cat > "$bundle/$name.ttl" <<TTL
@prefix doap: <http://usefulinc.com/ns/doap#> .
@prefix lv2:  <http://lv2plug.in/ns/lv2core#> .

<https://reaproof.dev/plugins/gain$uri_suffix>
    a lv2:Plugin, lv2:AmplifierPlugin ;
    doap:name "ReaProof Gain LV2$uri_suffix" ;
    doap:license <https://opensource.org/licenses/MIT> ;
    lv2:optionalFeature lv2:hardRTCapable ;
    lv2:port [
        a lv2:InputPort, lv2:ControlPort ;
        lv2:index 0 ;
        lv2:symbol "gain_db" ;
        lv2:name "Gain (dB)" ;
        lv2:default 0.0 ;
        lv2:minimum -24.0 ;
        lv2:maximum 24.0 ;
    ] , [
        a lv2:InputPort, lv2:AudioPort ;
        lv2:index 1 ;
        lv2:symbol "in" ;
        lv2:name "In" ;
    ] , [
        a lv2:OutputPort, lv2:AudioPort ;
        lv2:index 2 ;
        lv2:symbol "out" ;
        lv2:name "Out" ;
    ] .
TTL
  echo "built $bundle"
}

build_one reaproof_gain ""
build_one reaproof_gain_broken "-broken" -DREAPROOF_BROKEN=1

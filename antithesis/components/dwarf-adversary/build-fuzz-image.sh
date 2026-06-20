#!/usr/bin/env bash
# build-fuzz-image.sh — stage the host-built dwarf-decoder-fuzz binary + IOG libs
# + seed corpus into ./dist-fuzz and build the runtime image. Run on build-host
# after `cabal build -w ghc-9.6.7 exe:dwarf-decoder-fuzz`.
# Usage: ./build-fuzz-image.sh <image-tag> [corpus-dir]   (corpus default: /tmp/harvest)
set -euo pipefail
cd "$(dirname "$0")"

TAG="${1:-ghcr.io/cyber-castellum/dwarf-decoder-fuzz:dev}"
CORPUS_SRC="${2:-/tmp/harvest}"
BIN="dist-newstyle/build/x86_64-linux/ghc-9.6.7/dwarf-adversary-0.1.0.0/x/dwarf-decoder-fuzz/build/dwarf-decoder-fuzz/dwarf-decoder-fuzz"

if [ ! -x "$BIN" ]; then
    echo "binary not found at $BIN — run 'cabal build -w ghc-9.6.7 exe:dwarf-decoder-fuzz' first" >&2
    exit 1
fi

rm -rf dist-fuzz && mkdir -p dist-fuzz/corpus
cp -f "$BIN" dist-fuzz/dwarf-decoder-fuzz
cp -fL /usr/local/lib/libsodium.so.23 dist-fuzz/libsodium.so.23
cp -fL /usr/local/lib/libsecp256k1.so.2 dist-fuzz/libsecp256k1.so.2
cp -f "$CORPUS_SRC"/*.cbor dist-fuzz/corpus/ 2>/dev/null || true
echo "staged dist-fuzz/: $(ls -1 dist-fuzz | tr '\n' ' ') (corpus: $(ls -1 dist-fuzz/corpus | wc -l) files)"
docker build -f Dockerfile.fuzz -t "$TAG" .
echo "built $TAG"

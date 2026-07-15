#!/usr/bin/env bash
#
# build-image.sh — stage the host-built binary + IOG libs into ./dist and
# build the thin dwarf-adversary runtime image. Run on the build host
# (cardano-box) after `cabal build exe:dwarf-adversary`.
#
# Usage: ./build-image.sh [image-tag]   (default: ghcr.io/cyber-castellum/dwarf-adversary:dev)
set -euo pipefail
cd "$(dirname "$0")"

TAG="${1:-ghcr.io/cyber-castellum/dwarf-adversary:dev}"
BIN="dist-newstyle/build/x86_64-linux/ghc-9.6.7/dwarf-adversary-0.1.0.0/x/dwarf-adversary/build/dwarf-adversary/dwarf-adversary"

if [ ! -x "$BIN" ]; then
    echo "binary not found at $BIN — run 'cabal build -w ghc-9.6.7 exe:dwarf-adversary' first" >&2
    exit 1
fi

mkdir -p dist
cp -f "$BIN" dist/dwarf-adversary
cp -fL /usr/local/lib/libsodium.so.23 dist/libsodium.so.23
cp -fL /usr/local/lib/libsecp256k1.so.2 dist/libsecp256k1.so.2

echo "staged dist/: $(ls -1 dist | tr '\n' ' ')"
docker build -t "$TAG" .
echo "built $TAG"

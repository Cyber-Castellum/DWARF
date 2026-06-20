#!/usr/bin/env bash
#
# build-image.sh — stage the host-built binary + IOG libs into ./dist and
# build the thin dwarf-adversary runtime image. Run on the build host
# (build-host) after `cabal build exe:dwarf-adversary`.
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

FUZZBIN="dist-newstyle/build/x86_64-linux/ghc-9.6.7/dwarf-adversary-0.1.0.0/x/dwarf-decoder-fuzz/build/dwarf-decoder-fuzz/dwarf-decoder-fuzz"
CORPUS_SRC="${2:-/tmp/harvest}"

mkdir -p dist dist/corpus
cp -f "$BIN" dist/dwarf-adversary
cp -fL /usr/local/lib/libsodium.so.23 dist/libsodium.so.23
cp -fL /usr/local/lib/libsecp256k1.so.2 dist/libsecp256k1.so.2
# Bundle the decoder-fuzz harness + seed corpus into the same image (see Dockerfile).
[ -x "$FUZZBIN" ] && cp -f "$FUZZBIN" dist/dwarf-decoder-fuzz || { echo "decoder-fuzz binary missing — run 'cabal build exe:dwarf-decoder-fuzz'" >&2; exit 1; }
cp -f "$CORPUS_SRC"/*.cbor dist/corpus/ 2>/dev/null || true
# Genesis for the applyblock decoder-fuzz target (DWARF_GENESIS_DIR=/ledger-genesis).
rm -rf dist/ledger-genesis && cp -r ledger-genesis dist/ledger-genesis
# Conway-Tx seed corpus for the applyblock target (it decodes a raw Conway Tx,
# not the wire-GenTx envelope used by the tx target's /corpus).
rm -rf dist/corpus-conway && mkdir -p dist/corpus-conway
cp -f corpora/conwaytx/*.cbor dist/corpus-conway/ 2>/dev/null || true

echo "staged dist/: $(ls -1 dist | tr '\n' ' ') (corpus: $(ls -1 dist/corpus | wc -l) files)"
docker build -t "$TAG" .
echo "built $TAG"

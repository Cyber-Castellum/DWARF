#!/bin/bash
# dwarf-cov-run <surface> <seconds> — coverage-guided AFL++ run for one
# cardano-node surface. Surfaces: tx block header ledger applytx applyblock handshake txsub keepalive.
set -e
SURFACE="${1:-applytx}"; SECS="${2:-60}"
case "$SURFACE" in
  tx)         ENV=tx;         CORP=/opt/corpora/tx ;;
  block)      ENV=block;      CORP=/opt/corpora/block ;;
  header)     ENV=header;     CORP=/opt/corpora/block ;;
  ledger)     ENV=ledger;     CORP=/opt/corpora/txbody ;;
  applytx)    ENV=applytx;    CORP=/opt/corpora/conwaytx ;;
  applyblock) ENV=applyblock; CORP=/opt/corpora/conwaytx ;;
  handshake)  ENV=handshake;  CORP=/opt/corpora/handshake ;;
  txsub)      ENV=txsub;      CORP=/opt/corpora/txsub ;;
  keepalive)  ENV=keepalive;  CORP=/opt/corpora/keepalive ;;
  *) echo "unknown surface: $SURFACE (tx|block|header|ledger|applytx|applyblock|handshake|txsub|keepalive)"; exit 2 ;;
esac
OUT="${AFL_OUT:-/out/$SURFACE}"; mkdir -p "$OUT"
# applyblock builds an initial Conway ledger state from genesis (baked at
# /opt/ledger-genesis); other surfaces ignore DWARF_GENESIS_DIR.
echo "[dwarf-cov] surface=$SURFACE seconds=$SECS corpus=$CORP"
exec env DWARF_DECODER="$ENV" DWARF_GENESIS_DIR="${DWARF_GENESIS_DIR:-/opt/ledger-genesis}" \
  afl-fuzz -i "$CORP" -o "$OUT" -V "$SECS" -- /usr/local/bin/dwarf-decode-any @@

#!/usr/bin/env bash
# Build a from-807 amaru store off the d807adv cardano cluster (k=20, epochLength=400).
# Proven recipe: db-analyser boundary points -> amaru snapshot create -> amaru node bootstrap.
set -euo pipefail
export PATH=/home/nigel/.local/bin:$HOME/.cargo/bin:$PATH

D=/home/nigel/d807-amaru
AMARU=/home/nigel/codebases/amaru/target/release/amaru
EPOCH_LEN=400
TARGET_EPOCH=3          # bootstrap consumes epochs 0,1,2

# 1) writable copy of p1's chain db (db-analyser opens it rw; the volume is root-owned)
echo "== staging writable db copy =="
rm -rf "$D/db"; mkdir -p "$D/db"
docker cp d807-p1:/state/immutable "$D/db/immutable" 2>/dev/null
docker cp d807-p1:/state/volatile  "$D/db/volatile"  2>/dev/null || true
docker cp d807-p1:/state/ledger    "$D/db/ledger"    2>/dev/null || true
du -sh "$D/db"

# 2) epoch-boundary points (last block of each epoch + its parent)
echo "== extracting epoch boundary points =="
db-analyser --db "$D/db" --show-slot-block-no --in-mem --config "$D/configs/config.json" \
  > "$D/blocks.txt" 2>&1 || true
python3 - "$D/blocks.txt" "$EPOCH_LEN" "$TARGET_EPOCH" > "$D/points.txt" <<'PY'
import re,sys
blocks=[]
for ln in open(sys.argv[1]):
    m=re.search(r"BlockNo (\d+)\s+SlotNo (\d+)\s+([0-9a-f]{64})", ln)
    if m: blocks.append((int(m.group(1)),int(m.group(2)),m.group(3)))
blocks.sort(key=lambda b:b[1])
EL=int(sys.argv[2]); TGT=int(sys.argv[3])
out=[]
for e in range(TGT):
    lo,hi=e*EL,(e+1)*EL
    idxs=[i for i,b in enumerate(blocks) if lo<=b[1]<hi]
    if not idxs: sys.exit(f"ERROR: no blocks in epoch {e} (slots {lo}-{hi}); chain too short")
    i=idxs[-1]; b=blocks[i]; p=blocks[i-1]
    out.append(f"{b[1]}.{b[2]}::{p[1]}.{p[2]}")
print(" ".join(out))
PY
POINTS=$(cat "$D/points.txt")
echo "points: $POINTS"

# 3) snapshot create (3 consecutive epochs)
echo "== amaru snapshot create =="
SNAPDIR="$D/snapshots/testnet_42"
rm -rf "$D/snapshots"; mkdir -p "$SNAPDIR"
ARGS=""; for p in $POINTS; do ARGS="$ARGS --snapshot $p"; done
cd "$D"
$AMARU snapshot create --network testnet_42 \
  --cardano-node-db "$D/db" --cardano-node-config-dir "$D/configs" \
  --epoch "$TARGET_EPOCH" --snapshot-dir "$SNAPDIR" $ARGS 2>&1 | grep -iE "created|error" | tail -6
ls -la "$SNAPDIR"

# 4) node bootstrap (creates BOTH ledger + chain stores; needs the local-manifest patch)
echo "== amaru node bootstrap =="
. "$D/globals.env"
rm -rf "$D/store"; mkdir -p "$D/store"
cd "$D"   # bootstrap_snapshots reads ./snapshots/<network>/
$AMARU node bootstrap --network testnet_42 --epoch "$TARGET_EPOCH" \
  --ledger-dir "$D/store/ledger.testnet_42.db" \
  --chain-dir  "$D/store/chain.testnet_42.db" 2>&1 | grep -iE "nonces|opcert|header.import|error|import.utxo" | tail -8
echo "== resulting store =="
du -sh "$D/store"/* 2>/dev/null

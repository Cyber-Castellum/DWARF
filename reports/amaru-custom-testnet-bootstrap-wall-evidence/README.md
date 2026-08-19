# Evidence bundle — Amaru cannot bootstrap a custom testnet out of the box (fixable)

Supporting patches, logs, and inputs for the finding
`finding-amaru-custom-testnet-bootstrap-wall.md` (included). Generated 2026-08-19 while standing up
Amaru v10.11.20260807 on a DWARF-owned custom `testnet_42` (cardano-node 11.1.0 producer).

## Contents

- **`finding-amaru-custom-testnet-bootstrap-wall.md`** — the write-up: why `amaru node bootstrap` is
  limited to the three Amaru-published networks out of the box, the three source changes that restore
  custom-net bootstrap (one is a genuine upstream bug fix), the full working recipe, and the live
  mixed-devnet differential result.
- **`environment.txt`** — Amaru version/commit, reference node, snapshot tool, network parameters.

- **`amaru-custom-bootstrap-patches.diff`** — the three DWARF patches as a `git diff`:
  - `bootstrap/mod.rs` — local-manifest branch (build the bootstrap manifest from local `.tar.zst`
    archives + a local era-history, skipping the S3 listing) — **the unblock**.
  - `cardano_node/tvar.rs` — the UTxO definite-map decode fix (see the separate
    `finding-amaru-tvar-definite-map-decode-bug.md` — a standalone upstream bug).
  - `dev/ledger/states/import.rs` — `--global-parameters` overrides for custom nets.

- **`chain-dir-wall.txt`** — the pre-fix walls: `amaru node run` and `dev ledger nonces set` both open
  the chain store with `create_if_missing=false`, so nothing initialized it for a custom net until the
  local-manifest `node bootstrap` path was restored.

- **`ledger-import-success.txt`** — the post-fix proof: `Imported 3 snapshot(s) successfully`
  (epochs 0/1/2, UTxO imported), then the node boots serving.

- **`snapshots-genesis-list.txt`** — the three epoch-0/1/2 `.tar.zst` bootstrap archives generated via
  `amaru snapshot create` from the producer's immutable DB.

- **`testnet_42.era-history.json`** — the hand-authored era-history for the custom net
  (`epoch_size_slots=500`), supplied via `AMARU_ERA_HISTORY` to bootstrap + run.

## What is proven vs. open
- **Proven:** with the three patches, `amaru node bootstrap` creates both the ledger and chain stores
  for a custom `testnet_42` (deriving the consensus nonces from the snapshot, importing packaged
  headers) and `amaru node run` boots serving N2N + submit-API — no S3, no peer, no external nonce
  source. See `ledger-import-success.txt`.
- **Inherent limit:** amaru still will not forward-sync blocks from a single peer (open upstream
  #736), so the node runs serve-only — which is exactly the decoder surface DWARF fuzzes, and matches
  how the upstream `cardano_amaru` runs operate.
- **Trust-root takeaway:** cardano-node builds/validates any network from genesis; amaru resumes one of
  three Amaru-curated chains from a pinned snapshot. Different trust root — this bundle is the
  end-to-end demonstration of that gap and its fix.

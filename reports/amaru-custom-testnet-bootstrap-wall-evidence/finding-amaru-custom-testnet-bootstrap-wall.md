# Finding — Amaru cannot bootstrap a custom testnet out of the box (fixable; full recipe + patches)

**Target:** `amaru` v10.11.20260807 (git `493bffba`)
**Substrate:** DWARF-owned devnet, `intersectmbo/cardano-node:11.1.0` producer on `testnet_42`
(`epochLength=500`, `slotLength=0.1s`, `securityParam=5`, `activeSlotsCoeff=0.05`)
**Date:** 2026-08-19 · a DWARF test host
**Status:** RESOLVED-WITH-PATCHES — amaru now bootstraps + runs on a DWARF-owned testnet. Three
source changes were required (one is a genuine upstream bug fix). Proven end-to-end.

## Summary

Out of the box, amaru v10.11.20260807 **cannot** bootstrap a from-scratch / custom Cardano testnet:
its only supported bootstrap path (`node bootstrap`) is hardwired to the three Amaru-published
networks (mainnet/preprod/preview) via an S3 snapshot listing that also requires a bundled
era-history — custom nets have neither. In 807 the older local-manifest path (a hand-written
`snapshots.json`, used in the 20260730-era recipe) was refactored away. This is a real trust-root
divergence from cardano-node, which builds and validates any network from genesis.

However, it is **not a structural wall** — the rest of the bootstrap machinery (nonce derivation
from the snapshot, chain-store creation, packaged-header import) is intact. A ~40-line patch that
teaches `bootstrap_snapshots` to build its manifest from **local** archives + a local era-history
restores custom-net bootstrap. With three patches applied, a real amaru node stands up on a
DWARF-owned testnet_42 and serves N2N + submit-API. This confirms and extends
[Gap #1 bootstrap trust-source](finding-amaru-bootstrap-nonce-vrf.md): amaru's trust root is the
Amaru snapshot bucket, not genesis.

## The three required changes (all in the local amaru source tree)

1. **`bootstrap/mod.rs` — local manifest branch** *(the unblock)*.
   `bootstrap_snapshots` now first checks the local `snapshots/<network>/` dir: if it holds the three
   `.tar.zst` archives, it builds the `Vec<Snapshot>` from them using a local era-history
   (`AMARU_ERA_HISTORY`) and returns — skipping the S3 listing and the bundled-era-history
   requirement. Everything downstream (`bootstrap()`) is unchanged: it imports the 3 snapshots,
   derives the consensus nonces from the third snapshot (`nonce_tail`), `RocksDBStore::create`s the
   chain store, `store_chain_state` writes the nonces, and `import_packaged_blocks` seeds the
   bootstrap headers from each archive's `bootstrap.blocks.json`. No S3, no peer, no external nonce
   source needed.

2. **`cardano_node/tvar.rs` — UTxO reader bug fix** *(genuine upstream bug; worth submitting)*.
   The UTxO `tvar` reader probed `d.datatype()?` for a CBOR `Break` **before** checking the known
   definite-map length, so it read one byte past the last pair and raised `end of input bytes` on the
   small *definite-length* UTxO maps `db-analyser` emits. Large public-network snapshots stream as
   *indefinite* maps terminated by `Break`, so this had never surfaced. Fix: check the known length
   before probing for `Break` (no effect on indefinite maps, where the length is `None`).

3. **`dev/ledger/states/import.rs` — global-parameter overrides** *(convenience; `node bootstrap`
   already accepts them natively)*. Lets the ledger-only `dev ledger states import` accept
   `--global-parameters` for custom nets, mirroring `node run` / `node bootstrap`.

Additionally (no source change): amaru derives epoch length as
`active_slot_coeff_inverse × epoch_length_scale_factor × consensus_security_param` and **ignores the
genesis `epochLength`**; the two must be reconciled (here `--epoch-length-scale-factor 5` → 500) and
a matching `--era-history` JSON supplied to `node run`. A genesis whose explicit `epochLength`
contradicts its k/f would be read differently by the two clients — a further trust-source divergence.

## Working recipe (custom testnet_42, cardano-node 11.1.0 → amaru 807)

1. Stand up a cardano-node producer on testnet_42; let it pass ≥3 short epochs.
2. Recover epoch-boundary points from the immutable DB:
   `db-analyser --db <db> --show-slot-block-no --in-mem --config <config.json>` → last block of
   epochs 0/1/2 (+ parents). (Koios point resolution is unavailable for a custom net.)
3. `amaru snapshot create --network testnet_42 --epoch 3 --cardano-node-db <db>
   --cardano-node-config-dir <env> --snapshot <slot.hash>::<parent> ×3` → three `.tar.zst` archives.
4. Stage the archives in `./snapshots/testnet_42/`, set `AMARU_ERA_HISTORY=<era-history.json>` and the
   `AMARU_GLOBAL_*` params, then `amaru node bootstrap --network testnet_42 --epoch 3
   --ledger-dir … --chain-dir …` → **creates both stores** (ledger + chain, nonces + headers).
5. `amaru node run --network testnet_42 --era-history … --ledger-dir … --chain-dir …
   --listen-address 0.0.0.0:3011 --submit-api-address 0.0.0.0:3013 --peer-address <dummy>` →
   boots (`build_ledger tip.slot=1462`), serves N2N + submit-API, stays up.

Note: amaru still will not forward-sync blocks from a single peer (open upstream #736), so the node
runs serve-only — which is exactly the surface DWARF fuzzes (submit-API + N2N decoders). This matches
how the client's own `cardano_amaru` runs operate.

## Live differential result (mixed devnet)

DWARF's submit-API differential (same mutated tx → amaru:3013 vs cardano-submit-api:8090,
decode-agreement oracle) on the mixed devnet:

| seed | amaru | cardano-node | verdict |
|------|-------|--------------|---------|
| canonical 0x84 | DECODED | DECODED | AGREE |
| 3-element 0x83 | DECODE-REJECT | DECODE-REJECT | AGREE (807 arity fix confirmed) |
| 2-element 0x82 | DECODE-REJECT | DECODE-REJECT | AGREE |
| **trailing-bytes** | **DECODED** | **DECODE-REJECT** | ***DIVERGE*** |

The trailing-bytes case reproduces the open
[submit-trailing-bytes finding](finding-amaru-submit-trailing-bytes.md) **live** on a mixed net:
amaru's submit-API decoder does not enforce end-of-input, so `valid_tx || junk` decodes as the same
tx-id while cardano-node rejects at decode. 12 fuzz iterations: 0 unexpected disagreements, 0 panics.

## Artifacts
- `reports/dwarf-latest-devnets-evidence/{cardano-only,amaru-only,mixed-differential}-scenario-result.txt`
- `reports/amaru-custom-testnet-bootstrap-wall-evidence/amaru-custom-bootstrap-patches.diff` — all 3 patches
- `…/testnet_42.era-history.json` — custom era-history (epoch_size_slots=500)
- Amaru patches applied (uncommitted) in the local amaru source tree atop the release tag.

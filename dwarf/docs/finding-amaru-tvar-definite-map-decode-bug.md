# Amaru's snapshot importer aborts on definite-length UTxO maps (`end of input bytes`)

**Component:** `amaru` snapshot bootstrap — UTxO `tables/tvar` reader (`crates/amaru/src/cardano_node/tvar.rs`)
**Type:** CBOR decode robustness / interop bug in the bootstrap path (fails‑closed — rejects a well‑formed snapshot; does *not* admit malformed data)
**Status:** Confirmed (root cause in source; fix written and verified end‑to‑end — the same snapshot that aborted now imports). Present in the latest release. Fails‑closed, so it is a correctness/interop defect, **not** an acceptance vulnerability.
**Found by:** DWARF — bootstrapping Amaru on a DWARF‑owned custom `testnet_42` from cardano‑node `db-analyser` snapshots; the import aborted the instant the UTxO section began.
**Date:** 2026‑08‑19

---

## Summary

Amaru's snapshot UTxO reader (`import_tvar_utxo`) aborts with `end of input bytes` whenever the
`tables/tvar` UTxO section is encoded as a **definite‑length** CBOR map with no trailing bytes.
`db-analyser` — the cardano‑node tool Amaru itself invokes during `amaru snapshot create` —
serializes small UTxO sets exactly that way (`a8` = definite map of 8 pairs). Large public‑network
snapshots stream the UTxO as an **indefinite** map terminated by a CBOR `Break`, so the bug never
fires on mainnet/preprod/preview — but it deterministically breaks bootstrap for any snapshot whose
UTxO section is a small/definite map (custom or local testnets, minimal genesis, early epochs).

The decoder *fails closed* (it rejects a valid snapshot rather than accepting a bad one), so the
security impact is low. The operational impact is that Amaru cannot bootstrap such a network without
a source patch, compounding the broader gap that Amaru cannot bootstrap a from‑scratch / custom
Cardano network out of the box (`amaru node bootstrap` supports only the three Amaru‑published
networks).

## Environment

| | |
|---|---|
| Amaru | `v10.11.20260807` (git `493bffba`) |
| Snapshot source | `db-analyser` from `cardano-node` 11.1.0 / 11.0.1 (identical `tablesCodecVersion:1` output) |
| Network | DWARF‑owned `testnet_42` (k=5, epochLength=500) — 8 UTxOs at epoch 0 |
| Path | `amaru node bootstrap` / `amaru dev ledger states import` → `import_tvar_utxo` |

## Observed behaviour

Importing a `testnet_42` epoch‑0 snapshot whose UTxO set is a definite map of 8 entries:

| Snapshot UTxO encoding | cardano‑node produces it? | Amaru import |
|---|---|---|
| **definite** map (`a8 …`, no `Break`, no trailing bytes) — small UTxO sets | yes (`db-analyser`, small nets/early epochs) | **aborts**: `ERROR amaru::cli: error description=end of input bytes`, immediately after `import.utxo` begins |
| **indefinite** map (`bf … ff`) — large UTxO sets | yes (`db-analyser`, mainnet/preprod/preview) | imports normally |

The failing tvar begins:

```
00000000: 81a8 5822 11ca 21de 8044 8488 1a64 3d20   ..X"..!..D...d=
          ^^ ^^ ^^^^
          |  |  +-- 58 22 = byte string (34) — first UTxO key
          |  +-- a8 = DEFINITE map, 8 pairs (no Break, no trailing bytes)
          +-- 81 = array(1)
```

## Root cause

`import_tvar_utxo` decodes `array(1)` then `map(N)` (`Some(N)` for a definite map, `None` for an
indefinite one), then loops reading `(bytes, bytes)` UTxO pairs. The loop probes for a `Break`
**before** it checks the known map length:

```rust
loop {
    if d.datatype()? == cbor::data::Type::Break { d.skip()?; done = true; break; }   // (A)
    if size.is_some_and(|len| actual_size + chunk_size >= len) { done = true; break; } // (B)
    // ... read (input, output) byte pair ...
}
```

For a **definite** map (`size = Some(N)`), after the Nth pair the loop iterates once more and calls
`d.datatype()?` **(A)** *before* the length check **(B)**. When the map is the last thing in the file
(no trailing bytes, no `Break`), that `datatype()` reads past end‑of‑input and raises
`Error::end_of_input()`; the outer `LazyDecoder` treats end‑of‑input as "need more bytes", reads 0
more, and re‑raises — aborting the import. For an **indefinite** map the first pair‑less iteration
hits the `Break` byte at (A) and terminates cleanly, which is why real network snapshots never trip
it. The `LazyDecoder` buffer is 2 MiB, so the whole 743‑byte tvar is already resident — this is purely
the check ordering, not a streaming/refill issue.

## Severity

**Low — fails‑closed.** The decoder *rejects* a well‑formed snapshot; it never accepts malformed or
malicious input, so there is no memory‑safety or admission consequence. The impact is operational:
Amaru cannot bootstrap any network whose snapshot carries a small/definite‑length UTxO map — i.e. any
custom or local testnet — without this fix. It cannot occur on the three Amaru‑published networks
(their snapshots use indefinite maps), which is also why upstream CI has not caught it.

## Reproduction

```
# 1. Produce a small-UTxO snapshot from a cardano-node db (custom/local testnet), e.g.
amaru snapshot create --network testnet_42 --epoch 3 --cardano-node-db <db> \
     --cardano-node-config-dir <cfg> --snapshot <slot.hash>::<parent> ...   # 3 archives

# 2. Import it:
amaru node bootstrap --network testnet_42 --epoch 3 --ledger-dir L --chain-dir C
# stock v10.11.20260807 -> aborts:
#   INFO  amaru::bootstrap: constitutional_committee.import ...
#   ERROR amaru::cli: error description=end of input bytes
# with the fix below -> imports:
#   INFO  amaru::bootstrap: import.utxo size=8
#   Imported 3 snapshot(s) successfully
```

The tvar of any such archive shows the definite‑map header (`… a8 …`) with no trailing `Break`.

## Suggested remediation

Check the known (definite) map length **before** probing for a `Break`. This has no effect on
indefinite maps (where `size` is `None`, so the length check never fires and `Break` still governs):

```diff
             loop {
-                if d.datatype()? == cbor::data::Type::Break {
-                    d.skip()?;
+                // check the known (definite) map length BEFORE probing for a Break
+                if size.is_some_and(|len| actual_size + chunk_size >= len) {
                     done = true;
                     break;
                 }
-                if size.is_some_and(|len| actual_size + chunk_size >= len) {
+                if d.datatype()? == cbor::data::Type::Break {
+                    d.skip()?;
                     done = true;
                     break;
                 }
```

Verified end‑to‑end: after the reorder, `amaru node bootstrap` imports all three `testnet_42` epoch
snapshots (`import.utxo size=8` ×3) and the node boots serving from the resulting store. Recommend
landing the reorder upstream and adding a bootstrap regression fixture with a definite‑length UTxO map
(the public‑network snapshots CI exercises are all indefinite and cannot catch this).

## How this was found

While extending DWARF coverage to a from‑scratch, DWARF‑owned `testnet_42` on the latest releases
(cardano‑node 11.1.0 + amaru v10.11.20260807), the ledger import aborted at `end of input bytes` the
moment the UTxO section began. Dumping the snapshot's `tables/tvar` showed a definite `a8` map header;
comparing the reader's loop ordering against an indefinite‑map path identified the premature `Break`
probe. This is distinct from the previously reported Amaru findings (tx‑array‑arity, submit‑trailing‑
bytes, epoch‑transition rewards, restart rollback, consumer sync‑stall) — a different subsystem (the
bootstrap snapshot reader) and a fails‑closed defect rather than an acceptance divergence.

## Artifacts
- `reports/amaru-tvar-definite-map-decode-evidence/tvar-reader.patch` — the fix (diff)
- `reports/amaru-tvar-definite-map-decode-evidence/tvar-definite-map.hex` — the `81 a8 …` header proof
- `reports/amaru-tvar-definite-map-decode-evidence/import-before-after.txt` — error → success logs
- Related: `finding-amaru-custom-testnet-bootstrap-wall.md`, `finding-amaru-bootstrap-nonce-vrf.md`

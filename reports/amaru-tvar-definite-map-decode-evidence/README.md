# Evidence bundle — Amaru snapshot importer aborts on definite-length UTxO maps

Supporting source, proof bytes, and reproduction for the finding
`finding-amaru-tvar-definite-map-decode-bug.md` (included). Generated 2026-08-19 on cardano-box
while bootstrapping Amaru v10.11.20260807 on a DWARF-owned custom `testnet_42` from cardano-node
`db-analyser` snapshots.

## Contents

- **`finding-amaru-tvar-definite-map-decode-bug.md`** — the write-up (summary, root cause, severity,
  reproduction, fix).
- **`environment.txt`** — Amaru version/commit, snapshot tool, network parameters, path exercised.

- **`tvar-definite-map.hex`** — `xxd` of a real `testnet_42` epoch-0 snapshot's `tables/tvar`. Shows
  the header `81 a8 …` = `array(1)` then a **definite** map of 8 pairs (`a8`) with no trailing `Break`
  and no trailing bytes. **This is the input shape that triggers the bug.**

- **`import-before-after.txt`** — the same snapshot imported with stock vs. patched Amaru:
  - **before:** `ERROR amaru::cli: error description=end of input bytes` (abort, immediately after the
    UTxO section begins).
  - **after:** `import.utxo size=8` ×3 → `Imported 3 snapshot(s) successfully`.

- **`tvar-reader.patch`** — the fix as a `git diff`: check the known (definite) map length **before**
  probing for a CBOR `Break`. No effect on indefinite maps (where the length is unknown so `Break`
  still governs).

- **`source/`** — the exact Amaru source establishing root cause:
  - `tvar.rs` — `crates/amaru/src/cardano_node/tvar.rs`; the `import_tvar_utxo` loop whose first action
    each iteration is `d.datatype()?` (probe for `Break`) *before* the definite-length check — the
    ordering that reads past end-of-input on a definite map. (This copy is the **patched** file; the
    stock ordering and the one-line fix are in `../tvar-reader.patch`.)
  - `AMARU-COMMIT.txt` — the pragma-org/amaru commit + tag the source is from.

## One-line reproduction

```bash
# Produce a small-UTxO testnet snapshot (definite map) and import it:
amaru snapshot create --network testnet_42 --epoch 3 --cardano-node-db <db> \
      --cardano-node-config-dir <cfg> --snapshot <slot.hash>::<parent> ... # 3 archives
amaru node bootstrap --network testnet_42 --epoch 3 --ledger-dir L --chain-dir C
# stock v10.11.20260807 -> ERROR ... end of input bytes  (abort)
# with tvar-reader.patch -> import.utxo size=8 ... Imported 3 snapshot(s) successfully
```

The failing snapshot's `tables/tvar` begins with a definite-map header (`… a8 …`, see
`tvar-definite-map.hex`); a public-network snapshot's UTxO is an indefinite map (`bf … ff`) and does
not trip the bug.

## What is proven vs. open
- **Proven (empirical + source):** a `tables/tvar` whose UTxO is a definite-length CBOR map with no
  trailing bytes aborts the import at `end of input bytes` (`import-before-after.txt`), because the
  reader probes `datatype()` for a `Break` before checking the known map length (`source/tvar.rs`).
  Reordering the two checks fixes it and the same snapshot imports (`tvar-reader.patch`).
- **Scoped:** the bug **fails closed** — it *rejects* a well-formed snapshot; it never accepts
  malformed or malicious data. Impact is operational (can't bootstrap a custom/small-UTxO network),
  not a memory-safety or admission vulnerability. It cannot occur on mainnet/preprod/preview (their
  snapshots use indefinite UTxO maps), which is also why upstream CI has not caught it.
- **Recommendation:** land the reorder upstream + add a bootstrap regression fixture with a
  definite-length UTxO map.

# Evidence bundle — Amaru's submit-API accepts trailing bytes after a transaction

Supporting logs, seeds, source, and reproduction for the finding
`finding-amaru-submit-trailing-bytes.md` (included). Generated 2026-08-01 against a live
Amaru node (`v10.11.20260730`) and a live cardano-node reference (`cardano-submit-api` 10.7.1),
testnet_42.

## The finding in one line

`minicbor::decode(&body)` in Amaru's submit-API (`crates/amaru-node/src/submit_api.rs:77`) does
not enforce end-of-input, so `<valid tx> ‖ <arbitrary bytes>` decodes to the **same transaction id**
and is admitted; cardano-node rejects the same bytes with `DecoderErrorLeftover "Shelley Tx"`.

## Contents

- **`finding-amaru-submit-trailing-bytes.md`** — the write-up (summary, root cause, severity, fix).
- **`environment.txt`** — images, versions, network, endpoints.

- **`responses/`** — raw HTTP responses (headers + body) from each node:
  - `amaru_clean.txt` / `cardano_clean.txt` — the unmodified tx on both (baseline: both decode, fail validation).
  - `amaru_trailing-ff.txt` — **decodes, same tx id `9ec42389…58fba6`** as the clean tx (the appended `0xff` was ignored).
  - `cardano_trailing-ff.txt` — **HTTP 400**, `DecoderErrorLeftover "Shelley Tx" "\255"` → `TxCmdTxReadError` (the leftover byte is named and rejected).
  - `*_trailing-3bytes.txt` — same divergence with a 3-byte suffix.

- **`differential/`**
  - `mechanism-probe.txt` — the decisive probe: appending `0xff` / 3 bytes / a map to a valid tx → Amaru returns the **same** tx id each time (`SAME-ID(trailing-ignored)`), cardano-node decode-rejects.
  - `mutation-sweep.txt` — the broader sweep (1021 single-byte array/map length-header mutations over the corpus) → 39 decode-divergences, all reducible to the trailing-bytes root cause.

- **`resource/`** — the resource-exhaustion analysis (**refuted**):
  - `escalating-padding.txt` — 1 MB accepted; 10/50/100 MB → `HTTP 413` (Amaru caps the request body at ~2 MB, axum `DefaultBodyLimit`). Cap boundary: 1024 KiB accepted, 2048 KiB `413`.
  - `amaru_pad-1MB.txt` / `amaru_pad-2MB.txt` — the raw accept/`413` responses at the boundary.
  - (Flood: 2000 requests @ 300 concurrency of ~1.9 MB padded txs → peak memory 285.8 MiB, node survived, 0 panics — see the finding's *Severity* section.)

- **`seeds/`** — the deep Conway corpus (`c01`–`c10`: plain, stake-reg, vote-deleg, drep-reg, gov-info-action, drep-vote, datum-hash, multi-cert, metadata, validity-interval), built with `cardano-cli conway transaction build-raw` (no funds; they decode on both nodes, then fail validation).

- **`source/`** — `submit_api.rs` (the `minicbor::decode` ingress), `transaction.rs` (shows the `Transaction` type has no original-bytes field → canonical re-encode on relay), `AMARU-COMMIT.txt`.

- **`workload/`** — the tooling: `gen_deep_corpus.sh` (corpus generator), `hunt.py` (the mutation-sweep differential), `mech.py` (the mechanism/trailing-byte probe).

## One-line reproduction

```bash
cp tx.cbor tx_pad.cbor && printf '\xff' >> tx_pad.cbor
curl -s -X POST http://<cardano-submit-api>:8090/api/submit/tx -H 'Content-Type: application/cbor' --data-binary @tx_pad.cbor   # 400  DecoderErrorLeftover "\255"
curl -s -X POST http://<amaru>:3011/api/submit/tx            -H 'Content-Type: application/cbor' --data-binary @tx_pad.cbor   # decoded -> same tx id as clean tx
```

## What is proven vs. open

- **Proven (empirical):** Amaru decodes `tx ‖ junk` to the same tx id (trailing ignored); cardano-node rejects with `DecoderErrorLeftover`. Root cause pinned to `minicbor::decode(&body)` with no consumption check (`source/submit_api.rs`).
- **Proven (measurement):** **not** a resource-exhaustion vector — ~2 MB body cap + bounded concurrent buffering; memory flat under large-body and flood tests (`resource/`).
- **Bounded (source):** no propagation — `Transaction` has no raw-bytes field, so Amaru re-emits canonical bytes on relay; the junk is stripped. Ingress-only.
- **Severity:** Low — mempool-ingress decode conformance, not consensus-affecting.

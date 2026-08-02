# amaru_baked_dwarf — DWARF baked-store Amaru + submit-api fuzz (Antithesis, TEMP path)

Gets **Amaru running under Antithesis now**, with DWARF fuzzing preserved, without
waiting on the upstream peer-sync fix.

## 2026-08-01 — bumped to Amaru v10.11.20260730 (image `amaru-baked:0.2.0`)

Base image is now the upstream release `ghcr.io/pragma-org/amaru:v10.11.20260730`
(Debian trixie, non-root `amaru` user, binary `/usr/local/bin/amaru`) in place of the
old lambdasistemi 10.10 producer. The 10.10 store is **format-incompatible** with 10.11
(panics `pools::Row TypeMismatch` on load), so `baked-store.tgz` was **regenerated in
the 10.11 format** — a fresh testnet_42 store bootstrapped from the live `cardano_amaru`
devnet's p1 chain (epochs 0/1/2, target epoch 3) via `amaru snapshot create` +
`amaru node bootstrap` (full recipe recorded in the memory note `antithesis-amaru-audit`).
Verified on cardano-box: baked image boots clean, `build_ledger tip.slot=371`, submit-api
up, no permission/panic. **Note the tx-3element finding is now FIXED in this image** — the
`0x83` (3-element) tx is rejected at decode (`CBOR array length mismatch: expected 4 got 3`),
matching cardano-node; the bundle therefore now demonstrates the *fixed* decoder (and stays
useful as a live 10.11 submit-api fuzz + differential substrate for the scenarios).

## Antithesis observability wiring (2026-08-01)

Matched to the standard `cardano-node-antithesis` setup (`docs/testnets/cardano-node-master.md`):

- **Logs.** Antithesis captures each container's **stdout** natively. **Amaru logs to stdout**
  (`AMARU_LOG=info`) → captured directly, no sidecar needed. **cardano-node does not stdout its
  logs** (it ships to `cardano-tracer` → files), so the differential compose runs a **`log-tailer`**
  (`cardano-node-antithesis/log-tailer`) that streams `p1`'s tracer JSON logs to its own stdout for
  the Logs Explorer. The single-target (pure-Amaru) compose needs no tailer.
- **Setup signal.** Antithesis will not start fault injection until the system signals *setup
  complete*. The **workload driver** now emits it: `driver.py` waits until every target's submit-API
  port is reachable, then calls `antithesis.lifecycle.setup_complete(...)` (verified locally:
  `target ready → setup_complete signalled → fuzzing`). This replaces needing the cardano-centric
  `sidecar` (which is POOLS/tip-convergence-specific and doesn't fit a pure-Amaru run).
- **Assertions.** `workload.py` emits the SDK `always`/`sometimes` properties per fuzzed tx.
- **Reading results:** `moog antithesis properties` currently truncates to the first 50
  (cardano-foundation/moog #187) — paginate or you may miss real failures.

## Why this bundle exists

The mixed Haskell+Amaru bundle (`../cardano_amaru_dwarf/`) can't produce a
meaningful paid run yet: **Amaru never syncs a block from a single Haskell peer.**
Root cause is a *known, open* Amaru limitation, not our config:

- `pragma-org/amaru` **#736 "Properly handle in/out/duplex peers" — OPEN** (future
  milestone). The connection manager keeps a single state per peer and mishandles a
  peer that both dials in and out; Amaru completes the handshake but the outbound
  chain-sync/block-fetch never progresses.
- The scary `ERROR ... timeout fetching blocks` line is cosmetic-only
  (`#1050`/`#1051` — "no upstream peers => pause fetch without error log").
- Reproduced on **both** our deploy **and** the client's unmodified `cardano_amaru`
  running 2 days — both sit at zero blocks adopted.

## What this bundle does instead

**Sidestep peer-sync entirely.** Amaru boots from a *baked pre-synced store* and
serves; DWARF fuzzes it as a client:

```
workload  --POST mutated tx CBOR-->  amaru-baked :3011 (HTTP submit-api)
                                      amaru-baked :3001 (N2N listen)
```

- `amaru-baked` (`Dockerfile.amaru-baked`): pragma-org amaru v10.11.20260730 + a baked
  testnet_42 store (chain.db + ledger.db + era-history) baked into the image, so it
  is hermetic (Antithesis gives no host mounts). Entrypoint runs
  `amaru run --listen-address 0.0.0.0:3001 --submit-api-address 0.0.0.0:3011
  --peer-address <dummy> --peer-removal-cooldown-secs 86400`.
  **Verified locally:** loads ledger (`build_ledger tip.hash=4f3e12fb…`), starts the
  submit-api, listens, and stays up (`restarts=0`) — no crash, no
  `RewardsSummaryNotReady`.
- `workload` (`workload/`): loops `workload.py drive-submit`, POSTing mutated
  transaction CBOR to the submit-api and reading the **real** response —
  `200`=accepted, `400`=rejected (with the node's decode/validation reason),
  connection-drop=panic. This is a genuine oracle, **not** the old stubbed
  `accepted=True` — the HTTP status *is* the observation (closes the #43 gap for
  this surface).

### Assertions emitted (per fuzzed tx)
- `always` Amaru does not panic on fuzzed CBOR
- `always` Amaru stays alive after fuzzed CBOR
- `sometimes` Amaru rejects malformed CBOR
- (multi-target) `always` implementations agree on accept/reject

## Verified locally (2026-07-21, cardano-box)
`docker compose up` → amaru-baked Up `restarts=0` (ledger loaded, submit-api +
listen live), workload Up driving. Manual + looped `drive-submit` over the 20-file
corpus → real `400` rejects, `panic=False`, Amaru survives a sustained stream, 0
panics. `docker compose config` clean.

## Two configs in this bundle
- **`docker-compose.yaml`** — single-target MVP: baked Amaru + workload only.
  "Amaru's tx decoder never panics/hangs and sometimes rejects under mutation."
- **`docker-compose.differential.yaml`** — **Haskell-vs-Amaru differential** (built
  + validated 2026-07-22): a real cardano-node (configurator → single block-producer
  `p1`) + `cardano-submit-api` alongside baked Amaru; the workload POSTs the **same**
  mutated tx to BOTH `POST /api/submit/tx` endpoints and asserts they **agree** on
  accept/reject. A disagreement (one accepts what the other rejects) is a
  consensus-relevant divergence = a finding. Neither node needs to sync — p1 forges
  its own testnet_42 chain; Amaru serves its baked store.

  Verified live on cardano-box: cold `docker compose -f docker-compose.differential.yaml
  up` → all 6 services up, p1 forging, submit-api connected (400+ txs processed),
  workload driving both → `{'assertions': 7, 'targets': 2, 'agree': True, 'panic':
  False, detail: {amaru: 400, cardano: 400}}`, 0 panics, all nodes `restarts=0`.

## Seeds + oracle: what makes this a REAL test (2026-07-22)
- **Corpus = real serialized Conway transactions** (`workload/corpus/tx*.cbor`, built
  with `cardano-cli conway transaction build-raw` + `sign`). These **decode** on BOTH
  nodes (then fail ledger validation) — the correct substrate. NB: cuddle's raw
  `transaction`-rule CBOR is NOT submittable (it's the ledger-CDDL shape, not the
  serialized wire tx) and is decode-rejected by both nodes, so it can't surface a
  divergence — that path was tried and rejected.
- **Oracle classifies decode-failure vs validation-failure** (`classify_decode` in
  `workload.py`) by parsing the response body, because the submit-api returns `400`
  for both. The key assertion is **"both implementations agree on whether the bytes
  DECODE as a tx"**: a split (one decodes, the other rejects at decode) is a
  CDDL-conformance divergence = a finding. `drive_submit` returns `decode_agree` and
  the per-node `decoded` flag + reason.

## First-pass result: a CANDIDATE divergence
Running the real corpus with light mutation immediately surfaced one:
**flipping the tx's top-level array header `0x84`→`0x83` (array-of-4 → array-of-3)**
→ cardano-node **decode-rejects** (`DeserialiseFailure … Expected/found mismatch`),
Amaru **decodes it, computes the tx id, and advances to validation**. Reproducible
across 4 base txs. Both still return 400 (Amaru fails later), so it is a
decoder-conformance divergence, not a demonstrated mempool split. Needs Amaru-source
confirmation before it is a confirmed finding — see
`dwarf/docs/finding-candidate-amaru-tx-array-arity.md`.

## Known depth limits / next layer (honest)
1. **No confirmed ACCEPT path yet.** Seeds are decodable but not *valid* on the
   forged chain (synthetic inputs), so unmutated txs reject at validation on both.
   To test the accept path (200) + a mempool-level divergence, seeds must be valid,
   funded transactions — blocked here because this devnet's configurator does not
   expose the genesis UTxO signing key. Options: a configurator variant that exposes
   utxo-keys, or capturing txs from a `tx-generator`.
2. **Ledger-state agreement is decode-level, not deep-validation.** p1 forges its own
   chain and Amaru serves a different (baked) store, so the two ledgers differ.
   Structural/decode (CDDL) agreement is apples-to-apples; deep UTxO/fee agreement
   would need shared ledger state.
3. **Baked store is shallow (k=5 testnet_42, early epochs).**

## Launch (moog / Antithesis)
1. Build + push the images **public** (Antithesis anon-pulls at setup):
   - `docker build -f Dockerfile.amaru-baked -t ghcr.io/j-gainsec/amaru-baked:0.2.0 .`
     (build context must contain `baked-store.tgz`)
   - `docker build -f workload/Dockerfile -t ghcr.io/j-gainsec/dwarf-submit-workload:0.2.0 workload`
   - push both to a public registry. (`ghcr.io/intersectmbo/cardano-submit-api:10.7.1`
     and the cardano-node/configurator/tracer images are already public.)
2. Commit this dir to `Cyber-Castellum/DWARF`. Pick the compose to run:
   single-target (`docker-compose.yaml`) or differential
   (`docker-compose.differential.yaml`).
3. `moog create-test` with the requester secrets file (token id + wallet passphrase
   + GitHub PAT). No secrets live in this bundle.

`baked-store.tgz` is the pre-synced testnet_42 store (10.11 format):
`tar czf baked-store.tgz chain.testnet_42.db ledger.testnet_42.db era-history.json`.
To regenerate for a new Amaru version, rebuild the store with that version's
`amaru snapshot create` + `amaru node bootstrap` against a cardano-node testnet_42 chain
(recipe in the `antithesis-amaru-audit` memory note) — a store from a different major
version will panic on load.

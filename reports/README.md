# Reports & evidence

Client-facing results from DWARF campaigns against `cardano-node` and Amaru.

## Campaign reports (`campaign-reports/`)

- `dwarf-consensus-chain-selection-differential-campaign.html` — **cardano-node vs
  Amaru chain-selection differential**, 4-hour soak campaign on the real
  `cardano_amaru` mesh. Five regimes (honest reorg, within-k partition recovery,
  epoch boundary, VRF tiebreak, private-fork-under-delay); **466 iterations, zero
  genuine divergences** — cardano-node and Amaru selected the identical chain under
  every regime tested. (9 stage1 flags were benign 1-slot Amaru-relay propagation
  jitter, not divergences.)
- `dwarf-consensus-false-leadership-vrf-differential.html` — the **false-leadership
  (forged-VRF) rejection differential**: does each node enforce the Praos leader-value
  threshold, or only that the VRF proof verifies? We forged blocks with a valid VRF proof
  but an unearned win (leader value above the active-slot threshold) via a patched
  `db-synthesizer` (honest forges 27 blocks; patched forges 320 over the same window) and
  served them to both nodes. **Both reject — cardano-node with `VRFLeaderValueTooBig`,
  Amaru with `Insufficient leader stake`. Differential: AGREE.** Amaru enforces the
  leadership threshold — secure against any-stake block forgery. (A separate Amaru
  epoch-transition panic surfaced during setup is reported as a robustness finding.)
  Backing patch/logs/scripts in `consensus-false-leadership-vrf-evidence/`.
- `dwarf-consensus-genesis-config-differential.html` — the **genesis/config-acceptance
  differential**: a new surface — the `shelley-genesis.json` both nodes ingest at startup (the
  "stored" input, vs the messages/blocks fuzzing already covers). Mutate one field at a time and
  compare accept/reject. **Finding 1 (confirmed):** cardano-node crashes with an uncaught
  arithmetic exception on `epochLength=0`/`slotLength=0` instead of rejecting (it rejects every
  other malformed field cleanly in ~20 ms). **Finding 2 (well-supported):** the two clients
  validate genesis fundamentally differently — cardano-node directly at startup (~20 ms); Amaru in
  a separate component (`bootstrap-producer`), slowly, non-deterministically/hanging, and never
  re-validated at `amaru run`. **Finding 3 (preliminary, held back):** an Amaru-leniency lead not
  yet confirmed (flaky derivations). Battery + raw data in `consensus-genesis-config-evidence/`.
- `dwarf-consensus-bootstrap-trust-source-differential.html` — the **bootstrap trust-source
  differential** ("where did this node's truth come from?"). Source analysis: **cardano-node can
  bootstrap trustlessly from genesis** (and ships checkpoints); **Amaru cannot validate from
  genesis** — it structurally requires a trusted imported snapshot and carries **compiled-into-the-
  binary trust anchors** per network (initial leader-election **nonces**, headers, snapshot point;
  well-known-net snapshots via a hardcoded Mithril aggregator). So the two clients **do not share a
  root of trust for their initial state**, including the consensus-critical nonces — a
  trust-provenance gap a tip-convergence oracle can't see. No exploit; a model gap + new test
  dimension. Source citations in `consensus-bootstrap-trust-source-evidence/`.
- `dwarf-consensus-state-lifecycle-differential.html` — the **state-lifecycle / recovery
  differential** ("what happens across restart, crash-recovery, and cold-start after bootstrap?").
  Source analysis: **cardano-node** keeps a **self-contained ledger state** and **replays from the
  newest snapshot — falling back to genesis** — to self-heal on startup, so it is operational
  immediately. **Amaru** splits state across **four cross-consistent stores**, needs
  `MIN_LEDGER_SNAPSHOTS = 3` epochs of history to operate, and crossing an epoch boundary reads the
  **N-2 snapshot + N-2 stake distribution**. Amaru also has **no from-genesis rebuild** and **panics**
  on store inconsistency where cardano-node validates-and-truncates. So the two clients **recover
  differently** — a lifecycle model gap a tip oracle can't see. **A live DWARF run
  (`consensus-state-lifecycle-bootstrap-differential`, schema + registry valid) corrected one predicted
  finding:** a fresh-bootstrapped real Amaru crossed epoch boundaries **cleanly** (49+ consecutive, 0
  panics/errors) because the shipped bundle contains the 3 required snapshots — refuting the "valid
  block fails at first boundary" claim for the normal path (that failure needs an *incomplete*
  bootstrap). No exploit; a model gap + new test dimension. Source, live-run log, and observed-crash
  note in `consensus-state-lifecycle-evidence/`; scenario in
  `dwarf/scenarios/consensus-state-lifecycle-bootstrap-differential.yaml`.

## Transaction submit-API differential (`amaru-tx-3element-array-evidence/`)

- **Amaru accepts 3-element (non-canonical) Conway transactions that cardano-node rejects at
  decode.** DWARF submitted the same mutated transaction CBOR to both nodes' `POST /api/submit/tx`
  and compared, with an oracle that distinguishes **decode failure** from **validation failure**.
  Flipping a real transaction's top-level CBOR array header `0x84`→`0x83` (array-of-4 → array-of-3):
  **cardano-node rejects at deserialization** (`DecoderErrorDeserialiseFailure`); **Amaru decodes it**
  (its `Transaction` decoder makes the trailing `auxiliary_data: Option<_>` an omittable array
  element under minicbor), computes the tx id, and advances to validation. `array(2)` is correctly
  rejected (required field enforced) and `array(3)`/`array(4)` share a tx id → non-canonical-encoding
  / malleability. **Confirmed at decode level with root cause in Amaru source**; the mempool-accept
  (HTTP 202) and block-relay impact are **open** (a valid funded tx couldn't be built on the local
  devnet). Write-up: `dwarf/docs/finding-amaru-tx-3element-array.md`. Raw node responses, seeds,
  differential run, and the Amaru source (pinned commit) in `amaru-tx-3element-array-evidence/`.
  **Update (2026-08-01): fixed upstream** — `v10.11.20260730` now rejects the 3-element form at
  decode (`assert_len(4)`); confirmed empirically in `amaru-tx-3element-array-evidence/resolution-10.11/`.

- **Amaru's submit-API accepts trailing bytes after a transaction (no end-of-input check).**
  A follow-on hunt over a deeper certificate/governance corpus (built with `cardano-cli conway
  transaction build-raw`). Appending arbitrary bytes to a valid tx — `<tx> ‖ 0xff` — is decoded by
  Amaru to the **same transaction id** (trailing bytes ignored) and admitted; **cardano-node rejects**
  the same bytes with `DecoderErrorLeftover "Shelley Tx" "\255"`. Root cause: `minicbor::decode(&body)`
  in `crates/amaru-node/src/submit_api.rs` does not enforce end-of-input. **Still present in
  `v10.11.20260730`** (distinct from the 3-element fix above). **Severity low** — mempool-ingress
  conformance only: Amaru re-encodes canonically on relay (no propagation), and it is **not** a
  resource-exhaustion vector (~2 MB request-body cap, memory flat under large-body and flood tests).
  Write-up: `dwarf/docs/finding-amaru-submit-trailing-bytes.md`. Raw responses, seeds, mutation sweep,
  resource analysis, and pinned source in `amaru-submit-trailing-bytes-evidence/`.

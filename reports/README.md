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

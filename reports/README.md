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

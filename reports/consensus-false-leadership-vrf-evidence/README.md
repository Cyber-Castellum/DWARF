# False-leadership (forged-VRF) differential evidence

Backs `../campaign-reports/dwarf-consensus-false-leadership-vrf-differential.html`,
`../../dwarf/docs/consensus-report-false-leadership-vrf.md`, and the finding note
`../../dwarf/docs/finding-amaru-vrf-leadership-epoch-panic.md`.

## The test

Does each node enforce the Praos **leader-value threshold** (that a block producer's
VRF draw is below the bound set by the active-slot coefficient `f` and the producer's
stake `σ`), or only that the VRF proof verifies? A node that skips the threshold check
would accept **any-stake block forgery**. We forge blocks with a valid VRF proof but an
illegitimate (too-large) leader value and serve them to both `cardano-node` and `Amaru`.

## Result — AGREE (no vulnerability)

- **cardano-node** rejects with `VRFLeaderValueTooBig` (see `logs/cardano-node-vrf-rejection.txt`).
- **Amaru** rejects with `Insufficient leader stake` at slot 649, adopting nothing past
  its start tip 646 (see `logs/amaru-false-leadership.log`).

Both enforce the threshold; both accept only the genuinely-winnable early slots and reject
the first illegitimate block. **Amaru is secure against this attack.**

## The forgery (real crypto, logical time)

`patch/db-synthesizer-false-leadership.patch` patches `ouroboros-consensus-cardano`
`db-synthesizer` (v0.25.1.0): with env `DWARF_FALSE_LEADERSHIP=1` it raises the per-era
active-slot coefficient (`praosLeaderF` / `tpraosLeaderF`) to ~0.99 in the consensus
config used to forge and to validate its own forged blocks, while leaving
`praosRandomnessStabilisationWindow` and the genesis config untouched (nonce evolution
unchanged → the forged chain still connects to the real genesis root). The forger then
"wins" ~every slot regardless of stake. Proof it is real: same pool/keys/window, honest
forges **27** blocks, patched forges **320**.

## `scripts/`

- `build_patched_synthesizer.sh` — apply the patch and build (with ghcup GHC **9.6.7**,
  not the system 9.4.7).
- `forge_false_leadership.sh` — honest-vs-patched proof, then build the attack chain:
  honest chain to the bundle tip (slot 646, epoch 5), `db-truncater` to 646, clear
  `volatile/ledger/gsm`, then `db-synthesizer --append` the false-leadership tail kept
  **within epoch 5** (before the 5→6 boundary at slot 750 — this avoids the Amaru
  epoch-transition panic; see the finding note).
- `serve_and_run_victims.sh` — relay serving the attack chain, `bootstrap-producer` to
  build the Amaru bundle from the honest chain, the Amaru victim, and the cardano-node
  control.

## `logs/`

- `amaru-false-leadership.log` — full Amaru run: boots at the bundle tip, begins sync,
  fails header validation at slot 649 with `Insufficient leader stake`, drops the peer.
- `cardano-node-vrf-rejection.txt` — the cardano-node chain-sync client rejection
  (`VRFLeaderValueTooBig <leaderVal> (σ=1/5) (ActiveSlotCoeff f)`).

## Notes / gotchas (non-obvious)

- Devnet = **testnet_42** (f=0.2, k=5, epoch 125, magic 42) — the net Amaru actually runs
  on. Genesis + pool keys come from the running p1 producer's config volume.
- Two configs: `config.forge.json` (Dijkstra key **removed** — db-synthesizer 0.25 has no
  Dijkstra era) and `config.json` (Dijkstra key **kept** — cardano-node 10.7.1 requires it).
- db-synthesizer chain-dbs lack cardano-node's `protocolMagicId` (and `lock`) marker files;
  create them before a node/bootstrap-producer opens the db.
- `bootstrap-producer` requires the honest chain to cross the 3rd epoch boundary.
- **Amaru panics** (`state.rs:514 "unexpected stake distribution for epoch"`) if it crosses
  an epoch boundary during forward-sync from a bootstrap snapshot — hence keeping the forged
  blocks within one epoch. Reported as a robustness finding.

No credentials present (operator keys stay on the build host; only forged public chain data,
the patch, harness scripts, and node logs are here).

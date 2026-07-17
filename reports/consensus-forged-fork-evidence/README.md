# Consensus forged-fork + Genesis-follow-on evidence

Backs `../campaign-reports/dwarf-consensus-long-range-forged-fork-differential.html`
and the finding note (`finding-amaru-chain-selection.md`, also here). This is the
**costless-simulation** long-range work (Plan B) plus the Genesis-enabled follow-on.

## The forging pipeline (real crypto, logical slot time)

Built from the `ouroboros-consensus-cardano` v0.25.1.0 tools already in the stack
(`db-truncater`, `db-synthesizer`, `immdb-server`), compiled via `scripts/build_tools.sh`:

1. Copy a honest chain-db; `db-truncater --truncate-after-block 30500` to the fork
   point (> k behind).
2. `db-synthesizer --append` with an honest operator's real KES/VRF/opcert
   credentials forges a validly-signed alternative continuation **in logical slot
   order** — see `scripts/forge_synth.sh` and `logs/db-synthesizer-forge.log`
   (**"forged and adopted 53 blocks; reached SlotNo 186584"**). Forged fork tip =
   block 30553, forking at 30500. This sidesteps the wall-clock/KES-exhaustion wall
   that defeats any live-node forging approach.
3. Serve the forged fork over the real N2N protocol — via `immdb-server`
   (`scripts/run_immdb.sh`) for cardano-node, or via a real cardano-node relay
   seeded at the forged chain-db (`scripts/docker-compose.planb.yaml`) for Amaru,
   which does not sync reliably from `immdb-server` directly.

## Result — both implementations refuse the forged fork

Victims seeded on the honest chain at block 30520 (just past the fork) so the forged
fork (30553) is genuinely longer but forks 20 blocks (> k=5) back:
- **cardano-node** refused — held 30520, dropped the peer.
- **Amaru** refused — `intersect not found` (its ~k-shallow chain-sync intersect can
  not reach a fork 20 blocks back); held 30520.
- **Differential: AGREE, no divergence.**

## Genesis-enabled follow-on

- `cardano-node` GenesisMode (LoE/GDD/CSJ) confirmed working on the devnet
  (`ConsensusMode: GenesisMode` + `MinBigLedgerPeersForTrustedState: 0`).
- **Amaru is Praos-only** (no Genesis density rule).
- Divergence probe (`scripts/watch_divergence.sh`,
  `logs/genesis-vs-praos-probe.log`): **no reproducible divergence** on the 2-peer
  devnet — the k-rollback limit is shared by both implementations and both consensus
  modes, so it governs the deep-fork decision regardless of Genesis vs Praos. A fork
  Amaru can engage (within k) is not one GDD would distinctively reject; a fork GDD
  would distinctively reject (deep, sparse) is one Amaru's k-limit already refuses.
  GDD's distinctive protection is a mainnet-scale, many-peer bootstrapping
  phenomenon a small devnet cannot reproduce.

See `finding-amaru-chain-selection.md` for the two behavioural differences worth the
Amaru team's attention (Praos-only; shallow chain-sync intersect) and
recommendations.

## `scripts/`
- `build_tools.sh` — build db-truncater/db-synthesizer/immdb-server.
- `forge_synth.sh` — the db-synthesizer forge invocation.
- `run_immdb.sh` — serve a forged chain-db over N2N.
- `docker-compose.planb.yaml`, `amaru-victim-entry.sh` — real-relay serving + the
  seeded Amaru/cardano-node victims and the Genesis-mode node.
- `watch_divergence.sh` — the Genesis-vs-Praos decision oracle.

No credentials present (operator keys stay on the build host; only forged public
chain data + harness are here).

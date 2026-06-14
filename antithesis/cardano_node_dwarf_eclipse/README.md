# cardano_node_dwarf_eclipse — SP3a (block-fetch) ECLIPSE bundle

Self-contained Antithesis bundle for the SP3a block-fetch decode target. The
node under test (relay2) is ECLIPSED: it sits on an isolated `eclipse` docker
network and can reach ONLY the dwarf-adversary, which bridges both networks
(default → chain-syncs its upstream p1; eclipse → serves relay2). relay2 uses
`relay-eclipse-topology.json` (adversary-only, ledger peers off).

Why eclipse (vs the dual-peer `cardano_node_dwarf` used for tx-submission):
block-fetch fuzzing requires the node to fetch block BODIES from the adversary.
With trusted producers as peers the node fetches every body from them and never
asks the adversary. Eclipse makes the adversary the sole body source. The node
still bootstraps from origin via the adversary's VALID headers (0 VRFKeyBadProof,
verified), then block-fetches MUTATED bodies and runs its block decoder on them
(`dwarf_served_mutated_block`).

Generated from ../cardano_node_dwarf by dwarf/profile_manager/antithesis_generator.py
(`_apply_eclipse`). Local gate: ../../tools/sp3a_eclipse_repro.sh (served 62,
VRFKeyBadProof 0, RestartCount 0). Adversary image: ghcr.io/j-gainsec/dwarf-adversary:0.9.0.

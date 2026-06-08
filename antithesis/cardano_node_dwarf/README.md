# cardano_node_dwarf — Antithesis testnet (Phase 3a pipeline-proof)

Verbatim copy of the Cardano Foundation `cardano_node_adversary` testnet from
https://github.com/cardano-foundation/cardano-node-antithesis (Apache-2.0),
used to prove the Dwarf → Moog → Antithesis pipeline for `Cyber-Castellum/DWARF`
before Phase 3b adds the Dwarf CBOR-fuzz adversary mode.

All container images are CF's already-public images (referenced by digest);
nothing is built here. Launched via:

    moog requester create-test -d antithesis/cardano_node_dwarf -c <sha> \
      -r Cyber-Castellum/DWARF --try <N> -t 1 [--no-faults]

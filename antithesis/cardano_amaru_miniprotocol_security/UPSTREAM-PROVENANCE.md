# Upstream provenance — mixed mini-protocol state-machine security

Audit date: 2026-09-07 UTC. This file contains no credentials.

## Runtime substrate

- Cardano/Amaru control source:
  `cardano-foundation/cardano-node-antithesis@fe039ac5582081297b38709a6862083cc2fb6c00`.
- Latest upstream main recheck:
  `cardano-foundation/cardano-node-antithesis@6711c4ab8fe02f6418897b73bf42ca41e6efa697`.
  The delta is only PR #239's daily-check observation repair; `testnets/cardano_amaru`
  and `components` are unchanged.
- Current Amaru source/issues/PR audit:
  `pragma-org/amaru@835e32c62321571fc6571de87ca548eebfc0d43a`.
- Current Cardano-node source/issues audit:
  `IntersectMBO/cardano-node@c2ebdc87dfe07706a83e52f219e712c60d1b0a56`.
- Packaged Amaru runtime source: `ea1f34e42c7a1806d8ee60b3f512e58daae7ccc1` in
  `ghcr.io/lambdasistemi/amaru-bootstrap-producer@sha256:aabaf9e1fc1f58045329e14c1127c5424ba4794855d39bce05e3b426b7025c36`.
- Cardano-node runtime:
  `ghcr.io/intersectmbo/cardano-node@sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c`.
- SP4 runtime:
  `ghcr.io/j-gainsec/dwarf-adversary@sha256:c5e35065b9a58c337cd770ef0317bf11c1057a5869d654f7873275d3c8fe1aa1`.
  Its OCI index is
  `sha256:81345e7dfeecbeadc8d94de4f868b15b6540a0563843298e80430fe976bbcb95`.
- Workload base runtime:
  `ghcr.io/j-gainsec/dwarf-kes-workload@sha256:f3b5603f2d880a617c3ef3b9624c589d6af01ad30951f41e807cfd436f167a20`.

The inherited control assets are byte-identical to
`antithesis/cardano_amaru_relay_bootstrap_control`. Its provenance remains the
controlling source for snapshot generation, epoch prerequisites, Amaru ports,
consumer isolation, genesis-clock normalization, and convergence.

## Rechecked overlap

DWARF reports/docs/scenarios, the `dwarf-latest` and `moog` workbenches, Amaru
wiki/source/issues/PRs, Cardano-node issues, cardano-node-antithesis W30-W35,
and the successful upstream mixed topology were rechecked immediately before
implementation. Existing mixed work covers bootstrap, consensus/forks/rollback,
epoch boundaries, transaction decoding/fees, BlockFetch CBOR mutation, and KES.
SP4 covers this state-machine surface only against Cardano-node. No audited item
delivers the same generated illegal protocol-state cases to both Cardano-node
and Amaru and compares containment/recovery.

Adjacent Amaru networking PRs #1319-#1327 change handshake, mux timeout, bearer,
handler-termination, churn, and duplex behavior. They make the surface current,
but do not contradict or duplicate this scenario.

The W34/W35 harness failures and previously reported Amaru listener
`EADDRINUSE` candidate are background classifications, not discoveries this
package may claim.

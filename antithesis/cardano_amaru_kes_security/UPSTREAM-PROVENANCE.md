# Upstream provenance — Cardano/Amaru relay-bootstrap control

Audit date: 2026-09-05 UTC

This file freezes the implementation boundary for the new additive DWARF control.
It contains no credentials. The Compose package must use digest-only image names;
the source tag below exists only to make the artifact reproducible.

## Audited upstream revisions

| Project | Audited revision | Date (UTC) | Purpose |
| --- | --- | --- | --- |
| [pragma-org/amaru](https://github.com/pragma-org/amaru/commit/a4f15e71cd16f1cef7175cbca49352c3fb9777bc) | `a4f15e71cd16f1cef7175cbca49352c3fb9777bc` | 2026-09-04 | Current Amaru source/docs/issues audit |
| [lambdasistemi/amaru-bootstrap](https://github.com/lambdasistemi/amaru-bootstrap/commit/6f641855baecbe9632eb10c14bd58c351ffc2a2c) | `6f641855baecbe9632eb10c14bd58c351ffc2a2c` | 2026-09-04 | Exact bootstrap image source |
| [cardano-foundation/cardano-node-antithesis](https://github.com/cardano-foundation/cardano-node-antithesis/commit/fe039ac5582081297b38709a6862083cc2fb6c00) | `fe039ac5582081297b38709a6862083cc2fb6c00` | 2026-09-02 | Proven mixed topology and properties |
| [cardano-foundation/cardano-ignite](https://github.com/cardano-foundation/cardano-ignite/commit/dcef99c113fd0509f72aa651a197d55ed1a0dc36) | `dcef99c113fd0509f72aa651a197d55ed1a0dc36` | 2026-08-19 | Independent mixed-network reference |

The selected bootstrap image contains Amaru `10.11.0` at
`ea1f34e42c7a1806d8ee60b3f512e58daae7ccc1`, as reported by
`/bin/amaru --version` inside the pulled artifact.

## Exact runtime artifact

- Source tag: `6f641855baecbe9632eb10c14bd58c351ffc2a2c`
- Tag lookup digest: `sha256:aabaf9e1fc1f58045329e14c1127c5424ba4794855d39bce05e3b426b7025c36`
- Required Compose spelling:
  `ghcr.io/lambdasistemi/amaru-bootstrap-producer@sha256:aabaf9e1fc1f58045329e14c1127c5424ba4794855d39bce05e3b426b7025c36`
- Manifest media type: `application/vnd.docker.distribution.manifest.v2+json`
- Image config digest: `sha256:bf7d2c7551b57c56a0f58caee45c09d0d0253908628fa1f4130c6e7495f9f3f2`
- Packaged `db-analyser`: `ouroboros-consensus-exe-db-analyser-3.0.1.0`

Anonymous tag and digest requests both returned HTTP 200 and the same
`docker-content-digest`. An anonymous `docker pull` by digest also succeeded on
`cardano-box` and `docker image inspect` returned that exact repository digest.

The image contains executable paths:

```text
/bin/amaru
/bin/db-analyser
/bin/bootstrap-producer
/bin/amaru-relay-bootstrap
```

Invoking the relay entrypoint without configuration fails closed with:

```text
amaru-relay-bootstrap: RELAY_NAME is required (env or $1)
```

The image's default entrypoint is the lower-level one-shot
`bootstrap-producer`. Every relay service therefore must explicitly override it
with `entrypoint: amaru-relay-bootstrap`.

## Bootstrap contract

The controlling deployment documentation is
[amaru-bootstrap: Antithesis deployment](https://github.com/lambdasistemi/amaru-bootstrap/blob/6f641855baecbe9632eb10c14bd58c351ffc2a2c/docs/antithesis.md).
Each long-running relay must:

1. mount its paired Cardano ChainDB at `/live:ro`;
2. mount the matching generated Cardano configuration;
3. mount matching `era-history.json` and `global-parameters.json` runtime files;
4. write its early deployment marker;
5. repeatedly copy the live ChainDB and derive three completed-epoch targets;
6. run the packaged snapshot/bootstrap pipeline; and
7. replace the wrapper with `amaru run` after atomic bundle promotion.

The old separate one-shot service dependency is explicitly rejected by the
current documentation. A startup marker means only that the relay accepted its
deployment contract; it is not bootstrap, sync, or participation evidence.

The audited relay binary reports `listen_address="0.0.0.0:3000"` at runtime.
That downstream Amaru port is distinct from the `p1.example:3001` or
`p2.example:3001` Cardano upstream port. The control therefore routes its
isolated consumer to the relays on `3000` and tests that value explicitly.

Amaru's current [bootstrap documentation](https://github.com/pragma-org/amaru/blob/a4f15e71cd16f1cef7175cbca49352c3fb9777bc/docs/BOOTSTRAP.md)
requires a window of three consecutive epoch snapshots and a compatible
`db-analyser`. The Amaru wiki's September 2025 testnet notes independently
document why ChainDB format, snapshots, era history, genesis/system start, and
tool versions must remain aligned.

## Existing mixed-network proof

- [cardano-node-antithesis PR #177](https://github.com/cardano-foundation/cardano-node-antithesis/pull/177)
  reports a completed one-hour, faults-enabled `cardano_amaru` run with zero
  failing properties, no Amaru crashes/critical logs, convergence, and commands
  completing.
- [cardano-node-antithesis PR #197](https://github.com/cardano-foundation/cardano-node-antithesis/pull/197)
  reports a later completed 60-minute run where the isolated Cardano consumer's
  only upstream path was through Amaru; `amaru-served consumer reached producer
  tip` had 21 examples and zero counterexamples.
- Cardano Ignite independently maintains
  [`simple_mixed_nodes_source`](https://github.com/cardano-foundation/cardano-ignite/tree/dcef99c113fd0509f72aa651a197d55ed1a0dc36/testnets/simple_mixed_nodes_source)
  and
  [`global_network_mixed_nodes`](https://github.com/cardano-foundation/cardano-ignite/tree/dcef99c113fd0509f72aa651a197d55ed1a0dc36/testnets/global_network_mixed_nodes).
  These are supporting mixed-network references, not the controlling Antithesis
  contract.

## Known failure excluded from this control

[Amaru issue #1104](https://github.com/pragma-org/amaru/issues/1104) is the exact
epoch-transition total-rewards panic reproduced by the old DWARF control image.
It was fixed by [Amaru PR #1101](https://github.com/pragma-org/amaru/pull/1101)
and [Amaru PR #1125](https://github.com/pragma-org/amaru/pull/1125).

The image's Amaru revision is a descendant of both merge commits:

| Fix | Merge commit | Compare result to image Amaru revision |
| --- | --- | --- |
| PR #1101 | `d70b43c3ee8ab3f038420480063f90e0d41c5fab` | ahead, behind 0 |
| PR #1125 | `ea932d7802c4b755130ca45fd1713e479ceeb3c0` | ahead, behind 0 |

The known-bad `cf657b91...` artifact is forbidden in the new package.

## Current evidence boundary

The selected September 4 image passed its
[Build Gate and Live Bootstrap Producer](https://github.com/lambdasistemi/amaru-bootstrap/actions/runs/33861687558)
checks, and its
[publication workflow](https://github.com/lambdasistemi/amaru-bootstrap/actions/runs/33862083000)
succeeded. This proves artifact construction and a live Cardano bootstrap path.

It does **not** prove the exact image has completed a full mixed, fault-enabled
Antithesis run. GitHub-wide exact-revision searches found only the supplier bump
PR. The Cardano Foundation daily integration did not reach the downstream run
because its watcher stopped while the cold supplier build was still running;
[cardano-node-antithesis PR #239](https://github.com/cardano-foundation/cardano-node-antithesis/pull/239)
documents that controller defect. The missing full-run proof is why this DWARF
control must pass locally before any security delta or Antithesis submission.

## Cardano-node issue audit

A current `IntersectMBO/cardano-node` issue/PR search found no Amaru-specific
Cardano-node defect explaining the old rewards panic. Relevant upstream support
is tool packaging—particularly
[cardano-node PR #6478](https://github.com/IntersectMBO/cardano-node/pull/6478)—while
the selected bootstrap image deliberately packages its known-compatible
`db-analyser` itself.

## Local configurator correction

The pinned configurator's `/configurator.sh` calls `date` separately for every
pool in its final loop. If that loop crosses a wall-clock second, the generated
Shelley `systemStart` and Byron `startTime` differ between pools, producing a
split control network. A fresh local run observed this exact one-second split.

The new package retains the upstream generator and topology, then normalizes
only those two clock fields to pool 1 before the configurator exits. A separate
runtime probe reads all three producer files and refuses to continue unless the
clock pairs are identical. An isolated configurator execution on `cardano-box`
then produced identical SHA-256 values for all three Shelley genesis files and
identical SHA-256 values for all three Byron genesis files.

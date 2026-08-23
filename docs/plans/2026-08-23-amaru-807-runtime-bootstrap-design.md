# Amaru 807 Runtime Bootstrap Design

## Objective

Create a new, additive Antithesis package that bootstraps Amaru
`v10.11.20260807` from a fresh Cardano testnet generated inside the run. The
package must preserve the live Cardano lineage, avoid the historical Amaru
reward-transition panic, serve an isolated Cardano consumer through Amaru, and
exercise Amaru with DWARF BlockFetch mutations. Existing scenarios and image
packages remain unchanged.

## Decision

Build one new source-pinned `amaru-807-custom-bootstrap` image. It contains:

- Amaru source pinned to commit `493bffba`;
- the documented local-manifest, definite-map TVar, and custom-parameter
  patches already proven on `cardano-box`;
- `db-analyser` from the same immutable Cardano 10.7.1 image used by the testnet;
- a bootstrap entrypoint and the fixed Amaru runtime binary.

This single-image design prevents bootstrap/runtime schema drift. A two-image
resolver/bootstrap pipeline adds unnecessary readiness and image-identity
boundaries. A baked store is rejected because it does not test runtime bootstrap
or guarantee lineage with the fresh Cardano chain.

## Data Flow

1. The configurator creates a fresh three-producer Cardano testnet.
2. The bootstrap service waits until the live chain contains three complete
   epochs and a usable immutable tip.
3. It copies `p1` ChainDB into a writable scratch volume. It never writes to the
   live producer database.
4. `db-analyser --show-slot-block-no --in-mem` enumerates the copied immutable
   chain. A small deterministic resolver selects the final block and its parent
   for each of three consecutive epochs.
5. Patched `amaru snapshot create` creates three local archives from those
   explicit points.
6. Patched `amaru node bootstrap` imports the archives and creates native schema
   v5 ledger and chain stores, including nonces, bootstrap headers, and pool
   opcert sequence state.
7. The completed bundle is copied independently into each Amaru relay's private
   state volume.
8. The target relay peers the honest Cardano relay and DWARF; the control relay
   peers only its honest Cardano relay.
9. A Cardano consumer attached only to the Amaru consumer network must advance
   through the two Amaru relays.

## Failure Handling

The bootstrap container remains a single process and retries only precondition
failures: an immature chain, a racing snapshot copy, or incomplete immutable
history. It exits non-zero for malformed analyser output, missing epoch/parent
points, snapshot import failures, schema mismatches, or incomplete final stores.
A completion marker is written only after both schema-v5 stores and the era
history are present. Relay startup is gated on that marker.

All image references are literal and ultimately digest-pinned. Infrastructure,
Cardano, DWARF, bootstrap, consumer, and oracle services have exact
`container_name` values matching their Compose service keys and are excluded
from Antithesis faults. Only the two Amaru relays are fault-eligible.

## Properties

The bootstrap proof is separate from the later fault workload:

- bootstrap completes from the live Cardano lineage;
- both Amaru relays advance beyond their bootstrap tips;
- both cross an epoch boundary without panic or fatal failure;
- DWARF serves mutated BlockFetch bodies and target Amaru records decoder
  rejection, proving mutation-path coverage;
- target Amaru continues advancing while mutations are present;
- the isolated Cardano consumer advances beyond its seed and eventually matches
  a producer tip/hash.

Transient equal-height differences between honest relay paths are not classified
as DWARF acceptance because ordinary Cardano forks and propagation jitter can
produce them before rollback.

## Verification and Release Gates

Unit tests cover analyser parsing, epoch-point selection, bootstrap completeness,
both known Amaru adoption log formats, decoder rejection, and panic detection.
Compose contract tests enforce topology, fault exclusions, literal images,
runtime names, and sensitive-file hygiene.

The full local proof runs from empty project volumes and must cross an epoch
after Amaru begins following the chain. Only then are new lifecycle/fault
properties added. Publication requires a public image, anonymous pull check, and
immutable digest in Compose. Antithesis submission remains separately gated on
explicit user approval.

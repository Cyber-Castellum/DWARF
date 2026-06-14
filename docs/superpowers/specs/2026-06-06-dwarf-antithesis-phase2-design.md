# Dwarf Antithesis Integration — Phase 2 Design Spec

**Date:** 2026-06-06
**Status:** Approved (workbench Phase 2 design review v1 — all recommended options)
**Workbench review:** `https://bench.gainpalfam.com/wb/moog/o/obj_385a463a7d81486896e21928`
**Builds on:** Phase 1 (`2026-06-06-dwarf-antithesis-integration-design.md`, shipped & deployed)

## Goal

Extend the `antithesis` backend to render a **mixed Haskell + Amaru closed devnet** from one
profile (`profile-c`), and add a **cross-implementation differential assertion**, verified to the
same **ready-to-submit** boundary as Phase 1. The local execution path and the Phase 1
single-Amaru bundle are unchanged.

## Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| Q0 | Overall | Approve — proceed to implementation plan |
| Q1 | Mixed profile | Reuse/adapt the closed `profile-c` (1 Haskell + 1 Amaru) |
| Q2 | Haskell hermetic boot | Bake a pre-generated devnet env (genesis/config/topology) into a purpose-built mixed `cardano-node` image at build time |
| Q3 | Differential assertion | Send the **same fuzzed CBOR** to both nodes; assert **decode agreement** (both accept or both cleanly reject; neither panics) |
| Q4 | Assertion depth | Phase 1's 3 per-node assertions (no-crash, liveness, clean-reject) on each node **+** the 1 differential |
| Q5 | Images | Add `cardano-node` to the image build+push commands (same configured registry) |

Carried from Phase 1: additive (local unchanged); reuse (one definition, two backends); end state
ready-to-submit (validate + `docker compose config`); no live submit, no registration, no secrets.

## Architecture — generalize the emitter to two node types

The Phase 1 emitter (`render_compose`) emits only Amaru services + the workload. Phase 2
generalizes it to emit, from one profile: **N Haskell `cardano-node` services + M Amaru services
+ the workload**, all hermetic registry images on one internal network.

```
profile-c (node_count=1 Haskell, amaru_node_count=1, closed, magic 42)
        │  render(profile)  [antithesis backend]
        ▼
config/docker-compose.yaml
  ├─ cardano-node-1   (Haskell; registry image with baked devnet env; healthcheck)
  ├─ amaru-1          (Amaru; registry image; healthcheck)
  └─ workload         (peer to BOTH; drives same CBOR; asserts per-node + differential)
```

- Service naming: `cardano-node-<i>` (1..node_count), `amaru-<i>` (1..amaru_node_count),
  `workload`. Hyphenated (DNS-safe), `container_name == hostname`, `platform: linux/amd64`,
  `init: true`, per-service healthcheck — same Antithesis compliance rules as Phase 1.
- Bundle layout unchanged: `config/docker-compose.yaml`, `setup-complete.sh`, `test/`, README.
- Same CLI: `cardano-profile antithesis build profile-c-mixed-haskell-amaru-minimal`.

## The crux — making the Haskell node hermetic (Q2)

`cardano-node` needs genesis/config/topology to boot; the local devnet generates these at deploy
time via `cardano-testnet` and host-mounts them. Antithesis runs sealed (no host mounts, no
internet), so the env is **baked into a purpose-built image** at build time:

1. Generate a single-node devnet env once with `cardano-testnet` (genesis, config, topology,
   keys) for network magic 42.
2. Capture that env directory and bake it into a `cardano-node-devnet` image (a thin layer over
   the existing `dwarf/cardano-node` image that COPYs the env to a fixed in-image path and sets
   the node command to read from it — no host mount).
3. The emitted compose references `<registry>/cardano-node-devnet:<tag>`; the node boots
   standalone from its baked env.

The env is generated and baked by a build script under `dwarf/antithesis_workload/` (or a sibling
`dwarf/antithesis_devnet/`); the generated env is **not** committed to the repo (it lives in the
image). This keeps the bundle hermetic and the repo clean (Q2 = bake-into-image).

## The differential assertion (Q3) and workload

The workload becomes **multi-target**. It reads a list of node targets (host:port per
implementation) from the environment, e.g. `WORKLOAD_TARGETS="cardano-node-1:3001,amaru-1:3001"`
with implementation labels. For each fuzzed CBOR frame:

- Send the same bytes to every target; collect per-target `{accepted, panic, alive}`.
- **Per-node (Phase 1 assertions, each target):** `always(no panic)`, `always(alive)`,
  `sometimes(clean reject)`.
- **Differential (new, across targets):** `always(all targets agree on accept/reject)` — every
  implementation either accepted the frame or all cleanly rejected it; a divergence (one accepts,
  another rejects/parses differently) fails the assertion. `reachable("differential frame
  driven")`.

The workload stays SDK-optional (no-ops without the `antithesis` package) and unit-testable with a
`NullTransport` per target. Phase 1's single-target `drive_once` is generalized to
`drive_once(targets=[...])`; the Phase 1 closed-Amaru bundle keeps working with a single target.

## Image push (Q5)

`image_push_commands` gains the `cardano-node-devnet` build+push (build the baked-env image, tag
to the configured registry, push) alongside `amaru` and `dwarf-antithesis-workload`. Registry auth
remains environment-supplied; nothing embedded.

## Components (files)

- `dwarf/profile_manager/antithesis.py` — generalize `render_compose` to emit cardano-node + amaru
  services from the profile; multi-target workload env; extend `image_push_commands`.
- `dwarf/antithesis_workload/workload.py` — multi-target `drive_once`; per-node + differential
  assertions.
- `dwarf/antithesis_devnet/` (new) — Dockerfile + build script that bakes a `cardano-testnet`
  devnet env into `cardano-node-devnet`.
- `dwarf/profiles/profile-c-mixed-haskell-amaru-minimal/profile.yaml` — confirm/adjust closed
  (no public network/upstream); add a `testbed` marker if needed. (Reuse; minimal change.)
- Tests: `tests/test_antithesis_mixed_emitter.py`, extend `tests/test_antithesis_workload.py`.

## Verification

- **Golden-file emitter test** for the mixed compose: services are exactly
  `{cardano-node-1, amaru-1, workload}`, all Antithesis-compliant, images are registry refs
  (`cardano-node-devnet`, `amaru`, `dwarf-antithesis-workload`), workload depends on both nodes
  healthy and carries both targets.
- **Multi-target workload test**: `drive_once` with two `NullTransport` targets emits the per-node
  assertions for each plus one differential assertion; agreement true when both agree, flagged
  when they diverge.
- **`moog asset validate`** on the generated mixed bundle → `ready`; **`docker compose config`**
  resolves.
- **Non-regression**: Phase 1 single-Amaru bundle + all existing tests still pass; local devnet
  path unchanged.

## Out of scope

- Live `moog requester create-test`; registration; secrets; any change to local execution.
- Chain convergence/sync between the nodes (Q3 chose decode-agreement, not convergence).
- Broader peer/keepalive/timeout invariants (Q4 chose the minimal set).

## Open risks / to confirm during planning

- **Devnet env generation reproducibility**: `cardano-testnet` output (genesis hashes, keys) must
  be captured deterministically for the baked image; pin the generation command and the captured
  env. If `cardano-testnet` is unavailable in the build environment, fall back to a committed,
  reviewed minimal devnet fixture.
- **Amaru standalone boot**: in decode-agreement mode the workload is the peer, so Amaru need not
  sync from the Haskell node; confirm Amaru boots and accepts node-to-node connections with only
  network-magic config (no upstream). If it requires an upstream to stay up, point it at the
  Haskell node service (still in-sim, still hermetic).
- **Node-to-node handshake**: confirm the fuzzed-CBOR send path reaches the decoder on each
  implementation (handshake vs raw frame); if a minimal handshake is required before the fuzz
  frame, the workload performs it per target.

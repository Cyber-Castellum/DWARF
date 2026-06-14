# Dwarf Antithesis Phase 3b — CBOR-Fuzz Adversary Design Spec

**Date:** 2026-06-08
**Status:** Drafted from brainstorming (decisions locked below)
**Builds on:** Phase 3a (pipeline proven) + `codebases/cardano-node-antithesis/components/adversary`

## Goal

Build `dwarf-adversary` — a Haskell component (forked from CF's `adversary`) that acts as a **chain-sync upstream the cardano-node syncs *from***, serving **structurally-mutated header CBOR** so the node's **block/header decoder** actually decodes adversarial input. Seeded entirely by `antithesis_random` so Antithesis can **run, stretch (search), capture, and recreate** every run. This makes the dashboard show *Dwarf's* serdes-fuzzing assertions, not just CF's stock properties. Haskell / cardano-node only (Amaru unsupported by Antithesis).

## Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| Fork | Component | Vendor CF's `adversary` into `components/dwarf-adversary/`; add fuzz there (PR upstream later optional) |
| Direction | Role | **Upstream chain-sync *server*** — the node syncs *from* us; we serve mutated headers (fuzzes the rich header decoder) |
| Mutation | Strategy | **Structured CBOR mutation** over `Codec.CBOR.Term` (lengths, major types, nesting, collection bounds) |
| Scope | Protocol | **chain-sync only** (header decoder). blockfetch bodies / txsubmission deferred |
| Target | Impl | **cardano-node (Haskell) only** — Amaru deferred until Antithesis supports it |

## Hard requirements — it must *actually* fuzz

1. **Reach the decoder.** Complete the real Ouroboros N2N handshake + mux as a **responder**, run the chain-sync **server**, and serve `MsgRollForward` whose **header CBOR is structurally mutated** — so the node *attempts to decode* it. Mutate the chain-sync *payload* (header), never the mux/frame envelope (that would be dropped before the decoder).
2. **Realistic-but-hostile input.** Mutations operate on a **decodable base header** (see Header source) at the `Term` level — bad length headers, swapped major types, truncated/over-long arrays/maps, nesting abuse — so the decoder engages deeply rather than rejecting trivial garbage.
3. **Validate it bites.** Acceptance gate: evidence the node **actually requests headers from `dwarf-adversary` and runs its header decoder** on the served frames (node tracer shows chain-sync activity / decode errors / resets) — not a connection that never reaches decode.

## Hard requirements — Antithesis-native (run / stretch / capture / recreate)

1. **Recreate (determinism):** the seed from `antithesis_random` is the **sole** source of randomness in the fuzz path — `mkStdGen seed` drives every mutation choice. **No `/dev/urandom`, no wall-clock, no system entropy** (CF's adversary has a urandom fallback; the fuzz path must NOT use it). Given the seed, the served-frame sequence is a pure function → Antithesis reproduces any finding exactly.
2. **Stretch (search):** the seed is supplied by `antithesis_random`; the mutation sequence + Antithesis's infra-fault injection + scheduling form the explorable space. Emit `Sometimes` signals so the search has gradient toward interesting states.
3. **Capture:** `Sometimes`/`Reachable` carry structured details (mutation kind, header field/type touched, served-frame count, seed) so the report quantifies the perturbation; node decoder panics/crashes are captured by Antithesis + the node-side system properties.
4. **Run:** packaged as a proper component — public image + `/opt/antithesis/test/v1/` composer + `com.antithesis.exclude_from_faults` label + topology wiring so a node peers with it.

## Architecture

```
node (chain-sync CLIENT) ──dials──► dwarf-adversary (chain-sync SERVER, :3001)
        ▲ decodes header CBOR                 │ serves MsgRollForward(header)
        │                                     │   header bytes = mutate(base_header_Term, seed)
   header decoder exercised  ◄────────────────┘   (structured CBOR mutation, seeded)
```

`dwarf-adversary` is a **long-lived daemon** (like `tx-generator`): seeded once at startup from `antithesis_random`, it accepts the node's inbound connection, completes the handshake as responder, runs the chain-sync **server**, and on each `MsgRequestNext` serves a `rollForward` whose header CBOR is a seeded structural mutation of a base header. Determinism: a single startup seed fully determines the served sequence.

## Components / files (`components/dwarf-adversary/`, forked from `adversary`)

- **`Fuzz.hs`** (new) — structured mutation over `Codec.CBOR.Term` (add `cborg` to `build-depends`): `mutateTerm :: StdGen -> Term -> Term` with ops (alter array/map length header, swap major type, truncate/extend collection, perturb int/bytes width, add/remove nesting). Deterministic per `StdGen`.
- **`ChainSync/Server.hs`** (new) — chain-sync **server** (`chainSyncServerPeer`) serving a base chain; the rollForward header is encoded then **fuzzed** via the mutating codec.
- **`MutatingCodec.hs`** (new) — wraps `codecChainSync`'s encode: on each outgoing message, `decodeTerm` the header CBOR → `mutateTerm gen` → `encodeTerm` → send. (decode side normal.)
- **`ChainSync/Connection.hs`** (modify) — add **responder/server** mode (accept inbound; handshake as responder; `ResponderProtocolOnly`/`InitiatorAndResponder` with `chainSyncServerPeer`), alongside the existing initiator path.
- **Header source** — a **decodable base header** to mutate. **Hermetic constraint:** Antithesis has no network *during the run*, so the base header must come from inside the sealed environment — either (a) capture from a node **that is part of this bundle's compose** (in-environment chain-sync at startup), or (b) a header **baked into the image** as a fixture. Reaching an external/public node at runtime is NOT allowed. (Decision deferred to plan; in-environment capture preferred for realism, baked fixture as the simpler fallback.)
- **`app/Main.hs`** (modify) — add `--serve`/`--fuzz` mode, `--listen-port` (3001), `--mutation-rate`, `--upstream <host:port>` (for **in-environment** header capture only — a node inside this bundle); `--seed` (sole RNG, from `antithesis_random`).
- **`SDK.hs`** (reuse) — `Sometimes`/`Reachable` perturbation metrics (mutation kind, served-frame count, seed); **no `Always`** from the attacker.
- **`Dockerfile`** (reuse adversary's multi-stage nix/cabal) → image **`ghcr.io/cyber-castellum/dwarf-adversary`** (public).
- **`composer/cbor-fuzz/`** — `finally_fuzz_summary.sh` (+ any `eventually_`), since the fuzzer is a daemon (continuous), not a per-tick exec; assertions emitted from the daemon.
- **`antithesis/cardano_node_dwarf/`** (modify) — add the `dwarf-adversary` **service** (public image, fault-exclusion label, hostname) and **topology-wire** one relay to treat `dwarf-adversary.example:3001` as a local-root/upstream peer so it chain-syncs from us.

## Assertions

- **Attacker (`dwarf-adversary`):** `Sometimes("served a structurally-mutated header", {kind, seed})`, `Sometimes("node requested ≥N headers")`, `Reachable("fuzz server accepted a node connection")`. No `Always`.
- **Node-crash / decoder panic:** Antithesis ("No Antithesis errors") + existing node-side system properties — unchanged.

## Testing

- **Haskell unit/property tests** (`Fuzz`): same seed → identical mutation (determinism); `mutateTerm` changes the Term (non-identity at rate>0); structural ops produce the intended `Term` shapes; round-trips through `encodeTerm` to bytes. (Hspec/QuickCheck, mirroring `AdversarySpec`.)
- **Integration (local, pre-Antithesis):** stand up a single cardano-node configured with `dwarf-adversary` as upstream; confirm the node **connects, requests headers, and runs its decoder** on served frames (tracer shows chain-sync + decode activity), and the *harness* (dwarf-adversary) stays up.
- **Build:** image builds via the nix/cabal Dockerfile; compose validates (service + fault label + public image + topology wiring).

## Acceptance criteria (gates)

1. Fuzz path is **deterministic from `--seed`** (no nondeterministic entropy) — verified by a same-seed-same-output test.
2. A node **actually syncs from `dwarf-adversary` and decodes the served (mutated) headers** — verified locally before the Antithesis run.
3. `dwarf-adversary` image is **public on ghcr**; the testnet wires it in with the fault-exclusion label.
4. A Moog `create-test` run shows **Dwarf perturbation assertions** in the dashboard, and any node decoder panic is captured + recreatable.

## Out of scope (→ later)

blockfetch bodies / txsubmission fuzzing; byte-level mutation; client-direction request fuzzing; Amaru; PR upstream to CF.

## Prerequisites

- **`write:packages` ghcr token** to publish `dwarf-adversary` public (still pending). Antithesis is hermetic during runs and pulls public images **only at launch/setup**, so the image must be **built, pushed, and made public *before* the create-test launch** — not buildable or pullable mid-run.
- The adversary's **Haskell build toolchain** (blinklabs Haskell builder via its Dockerfile; builds on `cardano-box`).
- A **decodable base header** source that is available **inside the sealed environment** (in-environment capture or baked fixture) — never fetched from an external node at runtime.

## Open risks (this is the most exploratory phase)

- **Node accepting an unknown upstream.** The node's P2P/topology + handshake version negotiation must let it chain-sync from `dwarf-adversary`. Risk it won't peer without correct version/magic — the implementation plan should begin with a **spike**: prove a stock (unmutated) `dwarf-adversary` server gets a node to sync from it, *then* add mutation.
- **Sourcing a decodable base header** that reaches the decoder realistically (capture vs fixture).
- **Server-role Ouroboros wiring** (responder handshake + `chainSyncServerPeer`) is more involved than the initiator path.
- **Build/image** complexity (Haskell + nix/cabal) and **ghcr packages auth** are gating but known.

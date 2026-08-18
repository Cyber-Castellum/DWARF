# Amaru-on-Antithesis audit (task #42, offline prep)

**Date:** 2026-07-20 · **Goal:** before spending any (paid) Antithesis run, audit how DWARF's
Antithesis bundle handles Amaru vs the client's working `cardano_amaru` setup, and reconcile.

## Finding: DWARF's Antithesis Amaru is scaffolding-only (cannot bootstrap)

`antithesis build` for an Amaru-inclusive profile (`profile-c-mixed-haskell-amaru-minimal`) emits a
compose with **three** services — `cardano-node-1`, `amaru-1`, `workload` — where `amaru-1` is:

```yaml
amaru-1:
  image: .../cardano-repository/amaru:latest
  environment: { AMARU_NETWORK_MAGIC: "42" }
  healthcheck: [ tcp 3001 ]
  # no command, no volumes, no bootstrap
```

There is **no bootstrap mechanism**: no `bootstrap-producer` service, no snapshot bundle, no
ledger/chain store volumes. But Amaru **cannot run without pre-populated, tip-aligned stores**
(see the bootstrap trust-source / state-lifecycle findings — `amaru node run` fails with
"Have you bootstrapped your node?" / "ledger tip header not found"). So this bundle's Amaru would
fail immediately. The README's "scaffolding only" admission is accurate.

## The client's `cardano_amaru` IS the working Amaru-on-Antithesis environment

The client pointed us to
`github.com/cardano-foundation/cardano-node-antithesis/tree/main/testnets/cardano_amaru`. That
compose has the whole missing half — a `bootstrap-producer` (one-shot: snapshots p1's immutable →
builds the Amaru bundle), the `amaru-bundle` + `a{1,2}-state` volumes, and the Amaru relays that
copy the bundle and `exec /bin/amaru run`. It is explicitly Antithesis-aware (the producer script
notes "Transient under Antithesis faults"). It is the environment I've been operating and debugging.

## Image reconciliation

| | image | contents |
|---|---|---|
| DWARF bundle | `.../cardano-repository/amaru:latest` (from `dwarf/amaru:0.1.2`) | a bare Amaru node — no bootstrap |
| Client cardano_amaru | `ghcr.io/lambdasistemi/amaru-bootstrap-producer:03d2727b…` | Amaru node **+** bootstrap-producer (works) |

The client's image is the one with a working bootstrap. DWARF should adopt the client's
image + bootstrap flow rather than its bare `amaru:latest`.

## Bootstrap correctness prerequisites (proven locally, must hold under Antithesis)

Two failure modes I root-caused on the local `cardano_amaru` devnet — both would burn a paid
Antithesis run if not handled up front:
1. **Dormant-epoch crash** (`RewardsSummaryNotReady`): fast-bootstrap into a block-sparse early
   epoch kills Amaru. Fix: bootstrap from a **deep** bundle in the dense steady-state region.
   (`finding-amaru-dormant-epoch-rewards-crash.md`.)
2. **VRF/nonce sync** (`Invalid VRF proof` + "Unknown peer"): a node deep-bootstrapped right at an
   epoch boundary rejects the peer's headers on VRF verification — a leader-election nonce /
   bootstrap-consistency issue (ties to the #27 bootstrap trust-source finding). **Open** — must be
   resolved so Amaru actually *syncs*, not just stays up, before a paid run.

## Recommended path for #42

1. **Use the client's `cardano_amaru` Antithesis setup** (its image + bootstrap-producer + bundle)
   as the Amaru-on-Antithesis base — do **not** rely on DWARF's empty `amaru:latest` scaffold.
2. **Resolve the VRF/nonce sync locally first** (free) so a fresh Amaru deep-bootstraps *and* syncs.
3. **Inject DWARF's workload/scenarios** into that environment (the `workload` container already
   targets `amaru-1`), building the bundle **offline** and validating it end-to-end locally.
4. **Only then trigger one paid Antithesis run**, once a clean local bootstrap+sync is demonstrated.

Everything up to step 4 is offline and free.

## Offline validation done (2026-07-20)

1. **Amaru bootstraps + syncs** — the #42 prerequisite. A shallow bundle + forward sync boots a
   fully-synced Amaru that tracks the cardano tip block-for-block (see
   `finding-amaru-bootstrap-nonce-vrf.md` correction). So a fresh Amaru is run-ready.
2. **The DWARF workload ↔ dual-client integration works** — ran `workload.py drive-differential`
   against the **live** nodes (cardano `relay1` 172.31.0.2 + `amaru-relay-1` 172.31.0.12):
   `{'assertions': 7, 'targets': 2, 'agree': True, 'panic': False}`. The workload dials both,
   sends mutated CBOR, and emits the differential + no-panic assertions. Plumbing proven.

## Two gaps remain before a MEANINGFUL paid run

1. **The workload's observations are a stub.** `TcpTransport.send()` hardcodes
   `accepted=True, panic=False`, so `agree`/`panic` are trivially true — the assertions don't yet
   *mean* anything. Real observation logic is needed: parse the node's actual response (accept vs
   reject), detect a crash/panic, distinguish error classes. **This overlaps task #43 (rich
   rejection oracles)** — the same semantic-safety work.
2. **Bundle assembly:** produce the Antithesis composer = client's `cardano_amaru` (cardano + amaru
   + bootstrap-producer, all working) **+** the DWARF `workload` service **+** the Antithesis config,
   and validate the whole thing runs locally with real observations before spending a run.

## Net

Both #42 prerequisites are cleared (Amaru run-ready; workload dials the dual-client mesh). The
remaining work is (a) real workload observations = #43, then (b) assemble + local-validate the
composer, then (c) one paid run. #42 is now gated on #43, not on the (solved) bootstrap.

# Antithesis run design — Amaru consensus safety under fault × adversarial input

**Status: prepared, NOT submitted.** Submission is gated on explicit approval (it is a paid run)
and on publishing three images (see Prerequisites).

## The question this run answers

> **Does Amaru keep consensus safety when adversarial block-fetch input coincides with
> infrastructure faults — and does it recover correctly afterwards?**

This is a quadrant nobody has tested:

| | no faults | **+ Antithesis faults** |
|---|---|---|
| honest input only | trivial | what the upstream `cardano_amaru` runs already do (liveness / crash-safety) |
| **adversarial input** | proven locally, free (see below) | ← **this run** |

Deliberately *not* re-testing what local validation already established at zero cost.

## Topology

```
p1,p2,p3 (producers) + relay1,relay2        cardano-node 10.7.1, k=20, epochLength=400, f=0.2
      │                         │
 relay1.example            relay2.example
      │       └──────────────┐        │
 dwarf-adversary             │        │
 (mutates block-fetch CBOR)  │        │
      │                      │        │
      └──────► amaru-relay-1 ◄┘   amaru-relay-2
               TARGET               CONTROL
        honest peer + adversary   honest peer only
```

**The target relay holds an honest peer *and* the adversary at once.** This is the realistic
attack model — a node with one good and one malicious peer — and it is what makes the run
meaningful. An earlier wiring gave the target *only* the adversary; the relay then stalled at its
bootstrap tip (block 172 while the chain reached 1052), the safety assertion was near-vacuous, and
the restart path was unreachable. With a dual peer set the target syncs the real chain (514
adoptions, block 1683) while forged blocks are continuously offered.

Both amaru relays run **v10.11.20260807** — the release that fixes the epoch-transition rewards
crash, so the honest control survives past epoch 4 (the previous image crash-looped there, which is
what made a sustained differential impossible).

## Properties (emitted by `dwarf-oracle`, Antithesis SDK)

| kind | property | why it is non-trivial here |
|---|---|---|
| `always` | target never adopts a forged fork (its tip equals the honest tip at equal height) | target is actively adopting, so this is a real comparison, not vacuous |
| `always` | target never climbs above the honest tip height | climbing requires adopting forged blocks |
| `always` | neither relay panics on forged input | robustness under attack |
| `sometimes` | target rejected a forged block at decode | proves the adversary is actually exercising the decoder |
| `sometimes` | honest relay advances during the attack | liveness holds alongside the attack |

The upstream bundle's own properties (no fatal consensus logs, no unexpected exits, fork depth < k)
run alongside, so their liveness signal comes for free.

> **A stale oracle regex nearly made this worthless.** amaru 807 renamed the adoption trace
> (`adopted tip tip.slot=…` → `tip.adopt slot=… header_hash=…`). With the old pattern both `always`
> safety properties *and* the honest-advance property silently evaluated over **zero** samples — the
> run would have reported green while testing nothing. Fixed in `oracle/oracle.py`, which now accepts
> both formats.

## Mutations × faults (why the seed is Antithesis-derived)

`dwarf-adversary` takes a numeric mutation seed. Hard-coding it would give
*many fault schedules × one fixed attack*. Instead the adversary entrypoint
(`adversary-image/seed-entrypoint.sh`) sources the seed from `antithesis.random.get_random()`, so
Antithesis explores **mutations × faults** — and because the entropy is Antithesis's own, any
failure it finds still replays deterministically. `DWARF_ADV_SEED` pins the seed for reproducible
local runs.

## What local validation already proved (free — do not pay to repeat)

- Amaru **rejects** forged block CBOR at decode; no panic (`failed to decode message` ×N, 0 panics).
- Target relay tracks the **same honest chain** as the control (identical tip hash at equal height)
  while under attack.
- 807 **epoch-transition fix holds**: ran past epoch 4 to epoch 20, `RestartCount=0`, zero
  rewards-discrepancy signatures.
- Oracle properties fire correctly (0 `ALWAYS-FAIL`).
- **The hypothesised high-yield bug did NOT reproduce**: restarting the synced, under-attack target
  recovered cleanly under **both** SIGTERM and an unclean SIGKILL — no `RollbackPointInFuture`, no
  `Consensus died`. (Evidence: `reports/amaru-adversarial-807-evidence/`.)

## Honest expectation

**A green run is the likely outcome.** Amaru rejects malformed CBOR very early, and the best
bug hypothesis (restart/recovery) already failed to reproduce by hand. The case for spending the run
is that manual testing sampled **two** interleavings; Antithesis samples thousands — killing the
target mid-block-fetch, mid-epoch-transition, and mid-store-reconciliation. If a rare recovery or
chain-selection bug exists, systematic timing exploration is the only practical way to reach it.
Green is still a reportable result: *"Amaru held consensus safety under fault × adversarial input."*

## Recommended parameters

- **`--try 1 -t 1` first, then `--try 2 -t 3`**, faults ON throughout. A first-try 3-hour request
  is not adjudicated by the oracle — see `SUBMIT-RUNBOOK.md` §3; the 2026-08-20 attempt hung in
  `pending` indefinitely for exactly this reason. The extra hours at `try 2` are worth it
  *specifically because* the seed is randomised — at a fixed seed they buy much less.
- Adversary: `--protocol blockfetch --cbor-shape block --mutation-rate 0.5`.

## Prerequisites before submitting

1. ~~Publish three images~~ **DONE (2026-08-20)** — `amaru-adv:807-k20`,
   `dwarf-adversary-anti:0.10.0` and `dwarf-adversarial-oracle:0.1.1` are pushed to
   `ghcr.io/j-gainsec/*` and **public**, verified with an anonymous registry token. The compose
   pins all three **by digest**, so the run uses exactly the validated bytes.
2. Commit this bundle (compose + both image contexts + fixed oracle) and note the commit SHA.
3. Submit via moog, faults ON — **`--try 1 -t 1` first**, then `--try 2 -t 3` at the same commit.
   **Gated on explicit approval.**
4. Keep every `image:` reference **literal** — no `${VAR:-default}`. Both bundles that failed to
   launch had one; all that ran had none. See `SUBMIT-RUNBOOK.md` §3.

## Files

- `docker-compose.yaml` — the validated topology. **The filename is load-bearing**: Antithesis and
  moog look for `docker-compose.yaml`/`.yml` and nothing else, so a topology parked under any other
  name is silently not the one that runs. The superseded original is kept as
  `docker-compose.upstream-original.yaml` for provenance only.
- `relay-image/` — baked 807 relay (Dockerfile, entrypoint, `make-store.sh` store recipe)
- `adversary-image/` — Antithesis-seeded adversary wrapper
- `oracle/oracle.py` — properties (with the 807 trace-format fix)
- `reports/amaru-adversarial-807-evidence/` — local validation evidence

# cardano_amaru_adversarial — DWARF adversarial mixed-net differential

A **byzantine / adversarial** mixed Haskell+Amaru testnet for Antithesis. It takes the
client's *working* `cardano_amaru` topology (which only exercises crash/network faults)
and adds the dimension they don't have: **an adversary that feeds one Amaru relay forged
protocol messages**, plus a **differential oracle** that asserts Amaru rejects them.

This is a **separate** bundle — it does not modify `cardano_amaru_dwarf` or
`amaru_baked_dwarf`.

> **Read `RUN-DESIGN.md` first — it is authoritative.** This file records the *original* design.
> The shipped `docker-compose.yaml` is the later, locally-validated **807 dual-peer** topology and
> differs from the diagram below in two ways that matter:
>
> 1. **The Amaru relays run a baked `amaru-adv:807-k20` image**, not `bootstrap-producer` +
>    `amaru-consumer`. Amaru **v10.11.20260807** fixes the epoch-transition rewards crash, so the
>    control relay now survives past epoch 4; the older image crash-looped there, which made a
>    sustained differential impossible.
> 2. **`amaru-relay-1` peers the honest relay *and* the adversary**, not the adversary alone. With
>    the adversary as sole peer the relay stalled at its bootstrap tip (block 172 while the chain
>    reached 1052): the safety assertions were near-vacuous and the restart path unreachable. The
>    dual peer set is also the realistic attack model — one good peer, one malicious.
>
> The previous topology is kept as `docker-compose.upstream-original.yaml` for provenance. It is
> **not** what runs: Antithesis and moog consume `docker-compose.yaml` only.

## Why it exists

The client's own Antithesis runs (`cardano-foundation/cardano-node-antithesis`,
`testnets/cardano_amaru`) inject only **Antithesis-native faults** — container kill/pause,
network latency/partition. Their properties are pure **liveness + crash-safety**
("no fatal consensus logs", "no unexpected container exits", "fork depth < k",
"sometimes consumer reached tip"). **Nothing tests malicious input** — no forged blocks,
no mutated CBOR, no differential agreement with cardano-node under attack.

DWARF fills exactly that gap.

## Topology

```
cardano cluster: p1,p2,p3 (producers) + relay1,relay2 (relays)   [k=20, from PR #186]
        │                                   │
   relay1.example                      relay2.example
        │                                   │
   dwarf-adversary  ──mutated blocks──▶ amaru-relay-1        amaru-relay-2 ◀──honest── relay2
   (upstreams relay1,                  (ADVERSARIAL input)    (HONEST control)
    mutates block-fetch CBOR)                │                     │
                                             └──────▶ amaru-consumer ◀──────┘
                                            (cardano-node syncing FROM Amaru)
        dwarf-oracle  ──tails both relays' logs──▶ Antithesis SDK properties
```

- **`dwarf-adversary`** (`ghcr.io/j-gainsec/dwarf-adversary`): a byzantine relay. Upstreams
  the honest cardano `relay1`, re-serves the chain to `amaru-relay-1` over the block-fetch
  mini-protocol with per-message CBOR block mutation (`--mutation-rate`, `--cbor-shape block`).
- **`amaru-relay-1`** peers the adversary → consumes forged blocks.
- **`amaru-relay-2`** peers the honest `relay2` → control.
- **`bootstrap-producer`** (client's design) snapshots the running cardano-node into an Amaru
  store continuously, so the Amaru relays boot **near-tip** (sidesteps the peer-sync bug
  `pragma-org/amaru#736` via bootstrap, not forward-sync).
- **`amaru-consumer`** is a cardano-node syncing *from* the Amaru relays (tests Amaru as a server).

## The oracle (`oracle/oracle.py`, service `dwarf-oracle`)

Tails both relays' logs (shared `amaru-logs` volume) and emits Antithesis SDK properties:

| Property | Kind | Meaning |
|---|---|---|
| adversarial relay never adopts a forged fork | `always` | at equal height, relay-1's tip hash must equal relay-2's — else it adopted something forged |
| adversarial relay never advances past the honest tip | `always` | its only peer is the adversary, so climbing above honest = adopting forged blocks |
| neither relay panics on forged input | `always` | robustness under attack |
| adversarial relay rejected a forged block at decode | `sometimes` | proves the adversary is really exercising Amaru's decoder |
| honest relay advances during the attack | `sometimes` | liveness holds alongside the attack |

**A failing `always` is a finding:** Amaru accepted/adopted something forged that the honest
node rejected.

## Verified locally (2026-08-02, cardano-box)

Full deployment came up end-to-end. `amaru-relay-1` fed the adversary's mutated blocks:
```
handshake completed peer=dwarf-adversary.example
ERROR failed to decode message from network err=unexpected type array at position 2: expected tag
connection child died child=BlockFetch peer=dwarf-adversary.example
outbound connection died; three strikes within window, suppressing retries peer=dwarf-adversary.example
```
→ **rejected the forged CBOR at decode, killed block-fetch, banned the adversary — no crash, no
bad-chain adoption.** Meanwhile `amaru-relay-2` (honest) adopted tips block-height 126→131
normally. The differential works.

## Launch (moog / Antithesis)

Full procedure, including the pre-flight checklist: **`SUBMIT-RUNBOOK.md`**.

1. **Images — already published and public** under `ghcr.io/j-gainsec/*`:
   `amaru-adv:807-k20`, `dwarf-adversary-anti:0.10.0`, `dwarf-adversarial-oracle:0.1.1`.
   `docker-compose.yaml` pins all three **by digest**, so a run uses exactly the validated bytes.
2. Commit this dir to `Cyber-Castellum/DWARF`.
3. `moog requester create-test -r Cyber-Castellum/DWARF -d antithesis/cardano_amaru_adversarial …`
   (`-t <hours>`, faults ON — with faults off this is just the local validation at a price).
   Antithesis runs **`docker-compose.yaml`** and nothing else.

### Seeding the adversary

There is **no `$(antithesis_random)` substitution** — earlier notes here claimed one, and it is
wrong: `dwarf-adversary` 0.10.0 rejects `--seed random` outright (it wants a `uint64`). Instead
`dwarf-adversary-anti` wraps the binary with an entrypoint that reads the seed from
**`antithesis.random.get_random()`** at startup. Antithesis therefore explores *mutations × faults*
rather than many fault schedules against one fixed attack, and any failure it finds still replays
deterministically. Set `DWARF_ADV_SEED` to pin the seed for a local run.

## Local run

```bash
# 0.1.1 is the compose default; the override is only needed to test a different oracle build
INTERNAL_NETWORK=false docker compose -p caadv up -d
docker logs -f d807-dwarf-oracle     # watch the differential properties
docker compose -p caadv down -v      # tear down
```

Note the containers and networks carry a `d807-` prefix so the bundle can run alongside other
deployments on the same host without colliding. Antithesis does not care either way.

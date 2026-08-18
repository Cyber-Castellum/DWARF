# cardano_amaru_dwarf — DWARF mixed Haskell+Amaru Antithesis bundle

DWARF vendoring of the Cardano Foundation `cardano-node-antithesis` **`cardano_amaru`** testnet (see
`README.md` for the upstream description), placed here so the moog requester (J-GainSec) can pull it
from `Cyber-Castellum/DWARF` at a commit and fire the **first Amaru-inclusive Antithesis run** (all 29
prior runs were cardano-node only).

## Why this is the mixed campaign
`cardano_amaru` is already a working, Antithesis-aware **mixed Haskell+Amaru** environment:
- **3 Haskell producers** (`p1/p2/p3`) + Haskell relays; **Amaru relays + consumer** bootstrapped by
  `bootstrap-producer` and syncing the producers' chain.
- **sidecar** + **tracer-sidecar** emit Antithesis assertions incl. **chain convergence** between the
  Haskell and Amaru nodes — that convergence check *is* the mixed differential.
- All images are **public/anon-pullable** (intersectmbo cardano-node/tracer; cardano-foundation
  configurator/sidecar/tracer-sidecar/log-tailer; lambdasistemi amaru-bootstrap-producer). **No image
  builds required.**

## Validated (offline)
`INTERNAL_NETWORK=true docker compose -f docker-compose.yaml config` → clean. Services: 3 Haskell
producers + 4 Amaru services + configurator + sidecars. `INTERNAL_NETWORK` is supplied by the
moog/Antithesis harness (same pattern as the live `cardano_node_dwarf` bundle).

## Amaru bootstrap
Amaru boots via `bootstrap-producer` (shallow bundle + forward-sync), verified live block-for-block
this session — `dwarf/docs/finding-amaru-bootstrap-nonce-vrf.md`. The dormant-epoch / deep-bootstrap
crash hazards (`finding-amaru-dormant-epoch-rewards-crash.md`) only bite when bootstrapping the tip
directly onto a bad point; the default flow avoids them.

## ⚠️ `--seed` — local test vs Antithesis run (do not forget)
The `dwarf-adversary` command commits `--seed random`. The **Antithesis harness substitutes
`$(antithesis_random)`** for the literal `random` at run time (same convention as
`cardano_node_dwarf`), giving per-run determinism. The adversary **binary rejects `random`** on its
own (`option --seed: not a uint64`), so for a **local `docker compose up` test you must override it**
with a concrete uint64, e.g. `--seed 1234567`. **Never commit a numeric seed** — keep `random` in the
repo. (`amaru-relay-2` peers the adversary and gets its block decoder fuzzed; `amaru-relay-1` stays
honest as the control.)

## Launch
Per moog "How to Start an Antithesis Run (Requester)": commit this dir to `Cyber-Castellum/DWARF`,
then `moog create-test` with the requester secrets file (token id + wallet passphrase + GitHub PAT).
No secrets are stored in this bundle.

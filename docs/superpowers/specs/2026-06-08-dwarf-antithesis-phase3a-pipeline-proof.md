# Dwarf Antithesis Phase 3a — Pipeline-Proof Design Spec

**Date:** 2026-06-08
**Status:** Drafted from brainstorming (decisions locked below)
**Builds on:** Phases 1–2 (Moog integration) + CF guidance + `codebases/cardano-node-antithesis`

## Goal

Achieve one **green 1-hour Moog→Antithesis run** for `Cyber-Castellum/DWARF`, proving the full live pipeline end-to-end: commit a known-good cardano-node testnet → `moog requester create-test` (as `J-GainSec`) → CF agent → Antithesis → results visible in the `amaru-cardano` SSO dashboard. No Dwarf-specific fuzzing yet — this de-risks the plumbing before Phase 3b builds the real CBOR-fuzz adversary.

## Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| Scope | Staging | **C→A**: pipeline-proof now (3a), Haskell CBOR-fuzz adversary later (3b) |
| Base | Testnet | Copy CF's **`cardano_node_adversary`** testnet (public image digests intact) — it includes the adversary that 3b extends |
| Faults | First run | **`--no-faults` smoke first**, then a faults-on run |
| Images | Build | **None** — reuse CF's already-public images (`ghcr.io/intersectmbo/cardano-node` + `ghcr.io/cardano-foundation/cardano-node-antithesis/*`). Bucket-A (publishing a Dwarf image) defers to 3b. |

Carried forward: the Moog integration (registration ✅, repo whitelist ✅, requester `J-GainSec`, wallet funded) all stand. Superseded for cardano-node: the Phase 1–2 Antithesis *bundle emitter* + Python raw-socket workload (they can't speak Ouroboros N2N; CF's Haskell adversary is the real mechanism — addressed in 3b).

## Image / hermeticity model (why reusing CF images works)

Antithesis fetches the **public** images referenced in the compose at **launch/setup** (it has registry access then), assembles the system, then runs the simulation **hermetically** (no internet during the run; the SUT can't build or fetch mid-test). Reusing CF's exact published image **digests** guarantees availability, since CF already runs with them.

## Components / files

**A. Testnet dir → committed to `Cyber-Castellum/DWARF`** at `testnets/cardano_node_dwarf/` — a copy of CF's `testnets/cardano_node_adversary/`, image digests unchanged:
- `docker-compose.yaml` (nodes p1–p4 + relays + tracer/tracer-sidecar/sidecar/log-tailer + configurator + adversary; fault-exclusion labels intact)
- `testnet.yaml` (configurator input: poolCount 3, networkMagic 42, …)
- `relay-topology.json`
- `tracer-config.yaml`
- `README.md` (note it's a Dwarf copy of CF's adversary testnet for pipeline validation)

Minimal edits: only what's required for our repo (e.g. README provenance). The compose otherwise stays byte-faithful to CF's so it's known-good.

**B. Dwarf-side Moog `create-test` runner** (`dwarf-v4`): align Dwarf's Moog helper with the **real `moog requester create-test` flags** observed in CF's workflow and add a guarded live runner + a result wait:
- Flags: `-d <testnet-dir> -c <commit> -r <org/repo> --try <N> -t <hours> [--no-faults]`.
- **`--try` auto-count:** query `moog facts test-runs --whose <requester>`, count entries matching `(commitId, directory, platform=github, repository, requester)`, `+1` (mirrors CF's workflow).
- **Wait/status:** poll `moog facts test-runs --test-run-id <id>` for `.value.phase` (`accepted` → … → terminal), mirroring CF's `scripts/wait-for-test.sh`.
- Surface as `cardano-profile moog create-test` with `--dry-run` (extends existing `create-test-plan`) and `--approve` (guarded live submit). Secrets (PAT, wallet passphrase) sourced from env/0600 files, never printed.

## Data flow

```
[you]   PAT Contents:write approved  ──► [Claude] commit testnets/cardano_node_dwarf/ to Cyber-Castellum/DWARF @ <sha>
[Claude] moog preflight (read-only)  ──► state ready
[Claude] moog requester create-test -d testnets/cardano_node_dwarf -c <sha> -r Cyber-Castellum/DWARF --try 1 -t 1 --no-faults   (your explicit go)
[Moog]  records test-run on Preprod  ──► [CF agent] pulls dir @ <sha>, fetches public images, launches on Antithesis
[Antith.] 1h run                      ──► result/triage in amaru-cardano SSO dashboard; phase via `moog facts test-runs --test-run-id`
[Claude] then a faults-on run         (repeat without --no-faults)
```

## Verification / success criteria

- Testnet dir committed; `moog preflight … -d testnets/cardano_node_dwarf` no longer `blocked`.
- `create-test --no-faults` returns a `testRunId` + `txHash`; phase progresses `accepted` → terminal.
- A completed 1-hour run shows in the dashboard (a clean/0-findings no-faults run = pipeline proven).
- Then a faults-on 1-hour run completes and is viewable.
- Dwarf-side: unit tests for the `create-test` command construction + `--try` count logic (no live calls); existing suite stays green.

## Out of scope (→ 3b / later)

- The Haskell CBOR-fuzz adversary (extending `components/adversary`) — Phase 3b.
- Building/publishing any Dwarf-owned image.
- Amaru target / cross-implementation differential (Antithesis Amaru support pending).

## Prerequisites / blockers

- **PAT `Contents: Read and write` + org approval** — to commit the testnet dir (last probe: 403). Alternatively commit the 5 files via the GitHub UI.
- `MOOG_GITHUB_PAT` (read-capable, present) for create-test's GitHub validation; requester wallet + passphrase (present on the box).
- Repo whitelist ✅ (done).

## Open risks / to confirm during planning

- **Exact `create-test` flag set / validation** — mirror CF's workflow precisely (`-d/-c/-r/--try/-t/--no-faults`); confirm `-t` accepts hours and `--no-faults` exists in the installed `moog` version (`moog requester create-test --help`).
- **`--try` semantics** — confirm the facts query/filter matches CF's jq exactly so the count is right (a wrong `try` may collide or be rejected).
- **Result polling** — `moog facts test-runs --test-run-id` phase vocabulary; reuse CF's `wait-for-test.sh` as reference.
- **Image availability** — confirm the copied compose's CF image digests still resolve at launch (they're public + CF-used; low risk).

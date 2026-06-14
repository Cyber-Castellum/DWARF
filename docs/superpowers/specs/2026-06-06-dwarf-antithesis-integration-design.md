# Dwarf Antithesis Integration — Design Spec

**Date:** 2026-06-06
**Status:** Approved (workbench design review v6 — all recommended options)
**Workbench review:** `https://bench.gainpalfam.com/wb/moog/o/obj_92605d2688f5451c8441d74b`

## Goal

Add a full Antithesis path to Dwarf **alongside** (not replacing) its existing local
execution. Dwarf gains the ability to package a profile + scenario into an Antithesis test
bundle, push the required images, and wire the submit path — verified to a **ready-to-submit**
state, stopping just before the live `moog requester create-test`.

## Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| Q0 | Overall | Approve — proceed to implementation plan |
| Q1 | Phase-1 profile | Create/adapt a **closed (self-contained) single-Amaru devnet** profile |
| Q2 | Assertion depth | **Liveness + no-crash + one domain invariant** wired to the Antithesis SDK |
| Q3 | Dashboard result read-back (flow step 10) | **Defer** to a later effort |
| Q4 | Repo layout | Generated bundle under `antithesis/<target>/`; workload source under `dwarf/` |
| Q5 | Image registry | Use the registry already in Moog config (`us-central1-docker.pkg.dev/molten-verve-216720/cardano-repository`) |

Earlier framing decisions (pre-review): **additive** (local path unchanged); **reuse** (one
definition, two backends); **phasing** (P1 single Amaru → P2 mixed Haskell+Amaru); **end state**
ready-to-submit (preflight-green + local compose smoke test).

## Architecture — the backend seam

One seam both execution paths sit behind. A profile (topology) + scenario (test logic) is
*rendered* to a backend:

```
profile + scenario
        │
   render(profile, scenario)            one definition, two backends
   ┌────┴─────┐
   ▼          ▼
 local      antithesis   ◄── NEW
 devnet     backend
 (today,    (emits a bundle dir:
  wrapped    compose + config +
  behind     workload + assertions)
  seam)
```

- **No behavior change to local.** Existing devnet generation/deploy is wrapped behind a
  `Backend` interface as the `local` backend — same outputs, same commands, covered by existing
  tests as a non-regression gate.
- **New `antithesis` backend** (`dwarf/profile_manager/antithesis.py`) takes the same
  profile+scenario and emits a bundle directory instead of deploying a live devnet.
- The emitted directory **is** the `asset-dir` that the existing `moog asset validate` /
  `create-test-plan` / `preflight` already consume.
- New CLI: `cardano-profile antithesis build <profile> --scenario <s> [--out <dir>]`. Local
  commands untouched.

## Components

The Antithesis backend emits a self-contained bundle (committed into the DWARF repo):

1. **`docker-compose.yaml`** — images referenced by *registry* (not local), no host ports,
   internal DNS only, required `antithesis.*` labels and config layout. Topology read from the
   profile.
2. **Workload container** — Dwarf's load/fault logic ported to run *inside* the simulation
   (today it runs host-side via SSH). Source in `dwarf/antithesis_workload/`, built into an
   image.
3. **Antithesis SDK assertions** — Phase 1 wires three: liveness (node stays up), safety (no
   panic/crash), and one domain invariant (e.g. clean CBOR parse / no decoder panic), emitted as
   SDK `always`/`sometimes`/`reachable` properties from the workload.
4. **config + README** — non-secret only; no wallets/PATs/credentials committed.

Exact compose conventions validated against Antithesis's documented format (antithesis-* skills
installed locally under `~/.codex/skills`).

## Data flow (confirmed 10-step flow)

Dwarf is the two **bookends**; steps 3–9 are Moog/agent/Antithesis runtime that Dwarf only
observes via on-chain facts.

```
[Dwarf]   1.  build / validate assets                              ◄ this effort
[Dwarf]   1b. commit + push bundle to GitHub at a commit           (precondition)
[Dwarf]   1c. push node + workload IMAGES to Antithesis registry   (precondition)
[Dwarf]   2.  submit Moog create-test (repo, dir, commit)          ◄ wired, GATED
[Moog]    3.  record request on Cardano Preprod (MPFS)
[agent]   4.  agent sees request
[agent]   5.  agent pulls assets from GitHub
[agent]   6.  agent launches run in Antithesis
[Antith.] 7.  runs the security tests
[agent]   8.  collects result/report URL
[Moog]    9.  records result (status + report pointer, not full report)
[Dwarf]   10. read result back, show in /operate                   ◄ deferred (Q3)
```

**One-time registration gate (deferred):** real GitHub identity, PAT, vkey publish, user/repo
registration, agent whitelist. Until these exist, step 2 is refused — which is why this effort
stops at ready-to-submit.

## Phasing

**Phase 1 — single closed Amaru node, end-to-end ready-to-submit**
- Introduce the `Backend` seam; wrap existing local path as `local` backend (non-regression).
- Create/adapt a closed self-contained single-Amaru devnet profile.
- Implement `antithesis` backend emitter (compose + config + README) reading topology from the
  profile.
- Build the `antithesis_workload` container: drive the Amaru node; emit the three SDK
  assertions.
- Wire image build + push to the configured registry (node + workload images).
- New CLI `cardano-profile antithesis build`.
- Verify: golden-file emitter tests; local `docker compose up` smoke test; `moog asset
  validate` + `preflight` green; existing local-path tests still pass.

**Phase 2 — mixed Haskell+Amaru devnet (the destination)**
- Feed the mixed profile (profile-c/-h shape, closed devnet) through the same emitter.
- Add the second image push; add a differential/peer-interaction assertion.
- Mostly config + the differential layer; the hard pipeline plumbing is already proven.

## Verification

- **Golden-file unit tests** for the emitter (profile+scenario → expected compose/config).
- **Local compose smoke test** — `docker compose up` the emitted bundle locally to confirm it
  stands up and the workload connects.
- **`moog asset validate` + `preflight` green** on the emitted bundle.
- **Non-regression** — existing local-devnet path and its tests still pass.

## Out of scope

- Live `moog requester create-test` submission.
- Choosing GitHub identity / PAT / vkey / user+repo registration / agent whitelisting.
- Storing any secrets in the repo.
- Any change to local execution behavior.
- Dashboard result read-back (deferred per Q3).

## Open risks / to confirm during planning

- Exact Antithesis compose conventions (config path, required labels, workload entrypoint) — pin
  against the installed antithesis-* skills / docs before authoring the emitter.
- Amaru in-simulation networking: confirm a single closed Amaru node produces useful liveness
  without external peers; if it needs a peer, that nudges Phase 1 toward a 2-node closed Amaru
  set.
- Registry push credentials are environment-supplied (not committed); confirm the push path
  reads them from env/Docker config, not repo config.

# Stage 1 — Local Foundation Design Spec

**Date:** 2026-06-10
**Status:** Drafted from brainstorming (decisions locked below)
**Part of:** the pipeline-verification strategy (Stage 1 local → Stage 2 generate → Stage 3 Antithesis). This spec covers **Stage 1 only**.

## Background

DWARF's goal is to find security/robustness defects in Cardano node implementations by feeding them adversarial input. SP1 restored 195 cardano-node scenarios, all of which **semantic-validate but cannot actually run** — the fuzz **targets are `UNBUILT` skeletons**, so `scenario run` errors with "no such file" instead of decoding anything. Phase 3b also showed that "passes readiness / clean result" can silently mean "never actually exercised the target." Stage 1 closes both gaps so scenarios *genuinely run locally and we can prove they ran properly* — the foundation the generate (SP2) and Antithesis (Stage 3) verification build on.

## Goal

Make the restored cardano-node scenarios truly executable locally and gated on real behavior: **(A) build the cardano-node decode-shim binaries** and install them where the target manifests expect, and **(B) add a reusable local behavioral gate** that runs a scenario and passes only if it executes cleanly and its declared assertions pass.

## Findings that shape the design

- `dwarf/targets/cardano-node/` is a real Haskell cabal project: `dwarf-cardano-shims.cabal` (**14 executables**), `cabal.project` pinning CHaP (index-state hackage `2026-03-26`, CHaP `2026-04-01`), and complete `Decode*.hs` harnesses (each reads stdin bytes, attempts a typed CBOR decode, exits `0`/`1` with `OK`/`ERR`). The harnesses are written — this is **dep-wiring + compilation**, the same shape as the dwarf-adversary build that already works on cardano-box.
- v4's 14 shim executables cover **every target the restored + original cardano-node scenarios reference** (the 6 `mini-protocol-decode-*` plus the `cbor-decode-*` set). The fuller may shims (the `Ledger*` rule fuzzers) are amaru/differential → SP3, out of scope.
- Manifests declare a per-target `binary` path (some `bin/<name>`, some `dist-newstyle/...`). Install each built exe to the path its own manifest declares — no manifest edits (additive).
- Scenarios run on **cardano-box** (the main exec host with the Haskell toolchain). Binaries are platform-specific → build on cardano-box (x86_64-linux), where the scenarios run.

## Component A — Build the cardano-node decode shims

- Build `dwarf-cardano-shims` on cardano-box: `cabal build all` against CHaP at the pinned index-state, using a GHC that satisfies the CHaP `2026-04-01` snapshot (verify which ghcup GHC works — likely a 9.10.x; install via ghcup if the existing 9.6.7 is too old). First dep build is slow (CHaP from source) then cached — known cost.
- For each of the 14 executables, read its manifest's `binary` field and copy the built binary there (`dwarf/targets/cardano-node/<manifest-binary-path>`). Make them executable.
- The built binaries are git-ignored build output conceptually, but the manifest `binary` paths are where the executor looks — install (copy) there; commit a note/script, not the multi-MB binaries (gitignore `bin/` and `dist-newstyle/`).

## Component B — Local behavioral gate

A reusable verify step that turns "it ran" into "it ran *properly*":

- Input: a scenario path (+ runs/state dirs).
- Action: invoke the existing executor (`scenario run`) against the built targets.
- Gate (green) iff: the run completed with a clean `exit_status` **and** the run's assertion tally is `fail == 0` and `pass > 0`. A scenario that produces 0 assertions, or any failing assertion, or an executor error, is **red**.
- Surface: a `cardano-profile scenario verify <path>` subcommand (thin wrapper over `scenario run` + assertion-tally check) and a batch script `tools/stage1_verify.sh` that runs it across a list and reports `OK=/FAIL=`.
- This gate is generic; Stage 1 proves it on the restored `cardano-node-cbor-*-fuzz` scenarios (runtime `library` — no devnet needed, just the built shim binary).

## Definition of done

- `cabal build all` of the shims succeeds on cardano-box; the 14 binaries exist at their manifest-declared paths.
- A representative `cardano-node-cbor-*-fuzz` scenario **runs and its assertions pass** (`roundtrip_equals_original`, `parse_succeeds_or_clean_error`, `parser_exit_status` → `fail=0, pass>0`) — i.e. the shim actually decoded fuzzed input.
- `scenario verify` reports **green** for the cbor-fuzz scenarios; a deliberately-corrupt input produces a *clean error* (not a crash), and a genuine shim crash (if any) surfaces as a real finding (red).
- The verify gate + batch script are committed; binaries/dist-newstyle gitignored.

## Out of scope

- Amaru cargo-fuzz / Ledger-rule targets (SP3).
- `runtime-substrate-*` scenarios that need a full local **devnet** (docker compose bring-up) — Stage 1 targets the `library`-runtime cbor/mini-protocol fuzz scenarios first; devnet-runtime behavioral gating is a follow-on.
- The SP2 generator and Stage 2/3 verification (separate specs).
- Committing built binaries to git.

## Risks & mitigations

- **GHC/CHaP version**: CHaP `2026-04-01` may need ghc 9.10.x (may built with 9.10.3); cardano-box has 9.6.7. *Mitigation:* try 9.6.7 first; if cabal rejects the plan, `ghcup install ghc 9.10.x` and build with it (known, mechanical).
- **Long first build**: the cardano dep tree from CHaP is slow first time. *Mitigation:* background it (as with dwarf-adversary); cached after.
- **Manifest binary-path mismatch**: install reads each manifest's declared `binary` path rather than assuming one location.
- **Library-runtime input source**: the cbor-fuzz scenarios generate fuzzed inputs from a base corpus/manifest; confirm the executor supplies stdin bytes the shim expects (it does — `input_format: stdin_bytes`).

## Prerequisites

- cardano-box Haskell toolchain (ghcup; same as dwarf-adversary) + network for the first CHaP fetch.
- SP1 already restored the scenarios + manifests + primitives (done).

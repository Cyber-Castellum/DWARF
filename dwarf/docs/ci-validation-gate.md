# DWARF CI (validation gate + library fuzz)

DWARF's GitHub Actions run in **two stages** on every push / PR:

1. **Validation gate** (`dwarf-validate.yml`) — wallet-free, infra-free. Proves the scenario corpus
   and Antithesis bundles are well-formed **before** anyone spends a real run on a broken one.
2. **Library fuzz** (`dwarf-fuzz.yml`) — actually **executes** the decoders. Builds the Amaru
   decoder harnesses and fuzzes every registered decode target, failing the build on any crash.

The remaining tier — the docker multi-node **devnet** scenarios — needs a self-hosted runner and is
the planned next step (see the end of this doc).

## Stage 1 — Validation gate

Validates the DWARF scenario corpus and the Antithesis bundles. It **does not run** scenarios,
fuzzers, a devnet, or any Antithesis job. This gate just proves the definitions are well-formed.

## What it checks

| Check | What it proves | Cost |
|---|---|---|
| **Schema** | every `dwarf/scenarios/*.yaml` validates against `dwarf/spec/v1/schema.json` | ms |
| **Semantic** | every scenario's referenced primitives exist in `dwarf/primitives/registry.json` (`scenario validate --semantic`) | seconds |
| **Antithesis** | every profile renders into a well-formed Antithesis bundle **offline** — no docker daemon, no registry push | seconds |
| **MOOG assets** | every documented submission-ready bundle uses only MOOG's supported `com.antithesis.exclude_from_faults` tokens: `network`, `kill`, `pause`, `stop` | ms |

Current corpus status (2026-08-21): **239 scenarios — 0 schema failures, 0 semantic failures, 77
warnings; 13 Antithesis profiles render cleanly; 2 documented MOOG assets pass.** The gate passes today; it exists to catch the next
regression (e.g. a scenario referencing an unregistered primitive — exactly the class of bug that
slipped in during an earlier reclassification).

The documented MOOG asset set is explicit in `validate_scenarios.py`:

- `antithesis/cardano_node_dwarf`
- `antithesis/cardano_amaru_adversarial`

Experimental/reference Compose files are not silently treated as submission-ready. Add a bundle
to `MOOG_ASSET_DIRS` when its runbook documents a MOOG launch path.

### Fault-exclusion incident and prevention

The mixed Amaru adversarial bundle once contained:

```yaml
com.antithesis.exclude_from_faults: "true"
```

MOOG parses this value as a comma-separated fault-class list, not as a Boolean, so release MOOG
rejected it with `unknown fault class "true"` before calling Antithesis. The request remained
`pending` and no tenant run existed. The native cardano-node generator already emitted the correct
value, but the mixed bundle was hand-built; the old Stage-2 verifier checked only label presence,
and the workflow did not trigger on `antithesis/**`.

The prevention path is now shared:

1. `profile_manager.antithesis_validation` owns the MOOG-compatible parser.
2. `verify_generated_bundle` applies it to generated bundles.
3. MOOG asset validation and create-test planning apply it before submission.
4. The CI gate validates the documented checked-in MOOG assets.
5. The workflow triggers on `antithesis/**`, `tests/**`, and CI dependency changes.

## Files

- `.github/workflows/dwarf-validate.yml` — the workflow (push / pull_request / manual).
- `dwarf/scripts/validate_scenarios.py` — the gate logic (also runnable locally).

## Run it locally

```bash
python3 -m pip install -r dwarf/scripts/requirements-ci.txt   # jsonschema + jinja2 + PyYAML
python3 dwarf/scripts/validate_scenarios.py            # non-strict: warnings allowed
python3 dwarf/scripts/validate_scenarios.py --strict   # warnings are failures
python3 dwarf/scripts/validate_scenarios.py --json      # machine-readable summary
python3 dwarf/scripts/validate_scenarios.py --report out.json   # write summary to a file
```

Exit code `0` = pass, `1` = fail. In `--strict` mode, semantic warnings also fail.

## Stage 2 — Library fuzz (real decoder execution)

`dwarf-fuzz.yml` builds the Amaru decoder harnesses (`dwarf/targets/amaru`, package
`dwarf-amaru-shims`, 15 binaries) and runs `dwarf/scripts/ci_fuzz_library.py` — a seeded,
deterministic stream of mutated CBOR / mini-protocol inputs against every registered decode target.

It enforces the harness contract and **fails the build on any crash**:

| Harness result | Meaning |
|---|---|
| exit 0, stdout `OK` | input parsed cleanly |
| exit 1, stdout `ERR …` | input rejected with a clean decode error |
| **anything else** | **CRASH** (panic / abort / signal / hang) → build fails, crashing bytes uploaded |

This is the `runtime: library` scenario tier executed for real — no devnet, no wallet, no Antithesis
service. The harness sources are pinned path-deps on Amaru `v10.11.20260807` (commit `493bffba`) and
pallas (`3951639d`), built with the nightly toolchain pinned in `dwarf/targets/amaru/rust-toolchain.toml`.

- **Budget:** 500 inputs/target on push/PR (fast); 20,000/target on the nightly `schedule`; overridable
  via `workflow_dispatch`.
- **Artifacts:** the JSON report always; the exact crashing inputs on failure.

Run it locally (after building the harnesses):

```bash
# harnesses need Amaru + pallas checked out at codebases/{amaru,pallas} (see the workflow for pins)
(cd dwarf/targets/amaru && cargo build --release)
python3 dwarf/scripts/ci_fuzz_library.py --iterations 500 --out dwarf-fuzz-report.json
```

Exit code `0` = no crash, `1` = at least one crash (with crashers saved), `2` = harnesses not built.

## Stage 3 — Devnet smoke (self-hosted runner)

`dwarf-devnet-smoke.yml` runs a curated subset of self-provisioning cardano-node **devnet**
scenarios through the composer (`dwarf/scripts/ci_devnet_smoke.sh`), standing up real Docker
multi-node meshes and asserting liveness. Because that needs Docker + epoch warmup — too heavy for a
stock hosted runner (2 vCPU, ~7 GB RAM) — it runs on a **self-hosted runner**. Setup, host
requirements, and the self-hosted-runner **security model** are in
[`ci-devnet-smoke-runner.md`](ci-devnet-smoke-runner.md).

- **Curated, deterministic:** the subset is liveness-only 2-node `testnet_42` scenarios that pass
  reliably, so a red result means a real regression. Fault-injection / differential / soak scenarios
  are deliberately excluded from the gate (their oracles are timing-sensitive).
- **Isolated teardown:** the wrapper removes only the substrate projects **this run** created and
  never touches other containers, so long-running devnets on the same host are safe.
- **Triggers:** `push` to `main`, nightly `schedule`, manual `workflow_dispatch` — **never**
  `pull_request` (a fork PR must not execute on the self-hosted host).

## Not yet in CI

Antithesis runs go through **moog**, which is **approved-wallet-only** — a CI runner can't
authenticate, so those stay on-demand (see the operator runbook's Moog section), not in CI.

## Note: CLI entrypoint

`dwarf/profile_manager/cli.py` gained a standard `if __name__ == "__main__": raise SystemExit(main())`
guard so `python -m profile_manager.cli …` actually runs (previously it imported and silently
no-op'd, exit 0). The gate relies on this.

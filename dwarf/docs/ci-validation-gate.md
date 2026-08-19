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

Current corpus status: **229 scenarios — 0 schema failures, 0 semantic failures, 76 warnings; 13
Antithesis profiles render cleanly.** The gate passes today; it exists to catch the next
regression (e.g. a scenario referencing an unregistered primitive — exactly the class of bug that
slipped in during an earlier reclassification).

## Files

- `.github/workflows/dwarf-validate.yml` — the workflow (push / pull_request / manual).
- `dwarf/scripts/validate_scenarios.py` — the gate logic (also runnable locally).

## Run it locally

```bash
python3 -m pip install -r dwarf/scripts/requirements-ci.txt   # jsonschema + jinja2
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

## Why the devnet tier isn't in hosted CI yet

- Antithesis runs go through **moog**, which is **approved-wallet-only** — a CI runner can't
  authenticate, so it can't trigger a real Antithesis run.
- The **devnet** differential scenarios need **docker multi-node meshes** and minutes of epoch
  warmup per run — too heavy for a stock GitHub-hosted runner (2 vCPU, ~7 GB RAM).

Running a curated **smoke subset** of devnet scenarios on a **self-hosted runner** (built on top of
these two stages) is the planned next step.

## Note: CLI entrypoint

`dwarf/profile_manager/cli.py` gained a standard `if __name__ == "__main__": raise SystemExit(main())`
guard so `python -m profile_manager.cli …` actually runs (previously it imported and silently
no-op'd, exit 0). The gate relies on this.

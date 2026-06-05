# Dwarf Scenario Spec v1

A scenario is a YAML document describing one test, security test, or fuzz run against a Cardano implementation. Scenarios are data; primitives are code. The runner instantiates primitives by name from `dwarf/primitives/registry.json` and refuses scenarios that reference unknown primitives or that declare a runtime tier a referenced primitive does not support.

## Files in this directory

- `schema.json` — JSON Schema (Draft 2020-12) used by the runner to validate scenarios. Authoritative.
- `schema.yaml` — human-readable mirror with comments and two complete worked examples. For authoring reference only; not consumed by the runner.
- `README.md` — this file.

## Authoring a scenario

1. Copy one of the worked examples from `schema.yaml`.
2. Pick a globally unique `id` (lowercase kebab-case).
3. Pick the lowest `runtime` tier that does the job:
   - `library` — parser, decoder, ledger property tests; no node, no devnet.
   - `single-node` — mini-protocol behavior, handshake, single-node resource probes.
   - `devnet` — consensus, peer selection, diffusion, fork behavior, cross-node discrepancy.
4. List the primitives you need under `setup`, `load`, `probes`, `assertions`, and (optionally) `faults` and `teardown`.
5. For M2 delivery scenarios, add `related_milestones`, `m1_trace`, `evidence_intent`, and `promotion_blockers` so every run can be traced back to the applicable threat model, architecture map, and risk register. These fields are metadata only; they do not promote a candidate into a finding.
6. When modeling protocol transitions, use a library-tier state-machine load primitive only for declared transition traces. Treat that as harness evidence, not as proof of live node session state, until a single-node or devnet harness exists.
7. Drop the file in `dwarf/scenarios/pending/<id>.yaml`.
8. Validate with `cardano-profile scenario validate <id>` (Slice 8).
9. Promote with `cardano-profile scenario promote <id>` (Slice 10) once it passes.

## M1 traceability metadata

Use these optional fields for attack scenario library work:

```yaml
related_milestones: [M2]
m1_trace:
  threat_ids: [TS-001]
  gap_ids: [GAP-013, GAP-019]
  architecture_ids: [B-006, IF-008]
  risk_candidate_ids: [CR-M1-001]
evidence_intent: candidate
promotion_blockers:
  - single-node harness required before peer-manager claims
```

Allowed `evidence_intent` values are `candidate`, `regression`, `finding-validation`, and `risk-support`.

## FUZZ slots and trial-set runs

Any primitive parameter value may be a literal, the bare keyword `FUZZ`, or the long-form `{ fuzz: { type: ..., ... } }` object. When any FUZZ slot exists, the scenario becomes a **trial set**: it runs `iterations` times (default 100), each iteration with concrete drawn values. Each iteration produces its own child evidence bundle linked to a parent run, and is independently verifiable and replayable. Failing iterations are automatically shrunk (`shrink: true` by default) toward smaller failing inputs so reproducers stay tiny.

Bare keyword example: `peergroup: FUZZ` — the runner generates values from the primitive's declared parameter type.
Long-form example: `payload_length: { fuzz: { type: int, min: 1, max: 65536 } }` — explicit type, range, and (optionally) distribution.

The FUZZ engine ships in Slice 8.5; the spec reserves the syntax in v1 from day one so no scenario needs to be rewritten when the engine lands.

## Versioning

Spec evolves additively within v1: new optional fields can land in v1 minor revisions without breaking existing scenarios. Breaking changes go to v2 in a sibling `dwarf/spec/v2/` directory; v1 scenarios continue to validate against `dwarf/spec/v1/schema.json`.

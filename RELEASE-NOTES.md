# Release Notes

## Dwarf V3 June Milestone 2 (M2) Delivery

Image tag:

```text
dwarf/framework:june-20260604-m2
```

## Summary

This is a Dwarf Version 3 (V3) Milestone 2 delivery package. It starts from the latest local Dwarf framework state and ships the M2-relevant serialization/deserialization and first-execution material.

## Included

- Dwarf framework source, command-line interface (CLI), dashboard, scripts, tests, and target metadata needed for the framework.
- Concise Binary Object Representation (CBOR) random fuzz and structured-CBOR scenarios for Amaru and cardano-node.
- CBOR tx-body edge-case scenarios for Amaru and cardano-node.
- Authored serdes substrate scenarios, including the three remotely proven 2026-04-28 runs.
- Original M2 resource first-execution scenarios and evidence bundles.
- Test-output index for the retained M2 bundles in `TEST-OUTPUTS.md`.
- Six chain-verified example runs under `dwarf/runs/` for `/operate/runs` and `/operate/runs/<run-id>`.
- M2 serialization/deserialization analysis and resource-abuse testing plan.
- Docker lifecycle scripts and framework-only Dockerfile.

## Evidence Material

Evidence bundles are under:

```text
dwarf/evidence/m2-first-executions/
```

They are grouped as:

- `random-fuzz/`
- `edge-cases/`
- `resource-first-executions/`
- `serdes-substrate/`

The delivery-facing output index is:

```text
TEST-OUTPUTS.md
```

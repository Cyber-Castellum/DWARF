# Milestone 2 (M2) First Execution Evidence

This directory contains selected Dwarf run bundles copied from `build-host`.

## Groups

- `random-fuzz/`: first random-byte Concise Binary Object Representation (CBOR) fuzz executions for Amaru and cardano-node surfaces.
- `edge-cases/`: CBOR tx-body edge-case executions and compare bundle.
- `resource-first-executions/`: original M2 resource baseline, disk-fill smoke, and RSS time-series executions.
- `serdes-substrate/`: remotely proven composed-substrate serdes executions from 2026-04-28.

Each bundle preserves the Dwarf forensic shape, including `manifest.json`, logs, outputs, and chain data where present. The delivery-level index is `TEST-OUTPUTS.md` at the package root.

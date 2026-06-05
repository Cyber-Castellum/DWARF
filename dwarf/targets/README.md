# Dwarf Target Catalog

`dwarf/targets/` is the source tree for Dwarf's M2 decoder targets. The
manifests under `dwarf/targets/manifests/` are the canonical catalog consumed by
the CLI, the dashboard, and scenario validation.

## Layout

```text
dwarf/targets/
  amaru/                                   Rust CBOR and mini-protocol decoder binaries
  cardano-node/                            Haskell CBOR and mini-protocol decoder binaries
  manifests/                               one manifest per M2 registered target
  target/                                  gitignored build output
```

The V3 delivery catalog is intentionally scoped to the M2 registered targets:
Amaru and cardano-node Concise Binary Object Representation (CBOR) decoders plus
mini-protocol decoders.

## Manifest Contract

Each manifest records:

- `id`
- `binary`
- `decoder_type`
- `implementation`
- `language`
- `input_format`
- `upstream_commit`
- `expected_outcomes`
- `invariants`

`decoder_type` is the dashboard-facing taxonomy used by `/operate/targets`.
Current M2 values are:

- `CBOR codec`
- `Mini-protocol decoder`

`invariants` are the audit-facing properties the target is expected to
preserve. They vary by family:

- CBOR codecs: bounded decode, structured error behavior, round-trip or
  classifier stability where applicable
- Mini-protocol decoders: state-machine discipline, malformed-input rejection,
  spec-conformant acceptance

## Adding a Target

1. Create or update the implementation source under `dwarf/targets/amaru/` or
   `dwarf/targets/cardano-node/`.
2. Add a same-name manifest under `dwarf/targets/manifests/`.
3. Pick the correct `decoder_type`.
4. Write invariants that describe the target's expected behavior, not generic
   filler text.
5. Run the delivery contract from the package root:

```bash
bash delivery/tests/test_delivery_contract.sh
```

## Operational Use

- Scenario validation resolves `target_id` against this manifest catalog.
- `/operate/targets` renders its rows from these manifests.
- `/operate/coverage` and the serialization/deserialization audit use this
  catalog as one of their source anchors.

## Notes

- `target/` is build output and should stay unversioned.
- Some catalog entries point at crates that produce multiple binaries. Those
  manifests remain valid catalog entries, but the `status` field explains when
  the manifest is inventory-oriented rather than a single executable shim.

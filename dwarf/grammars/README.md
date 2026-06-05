# Dwarf Grammar Dictionaries

This tree holds grammar-aware mutation hints retained for the M2
serialization/deserialization seed material.

## Layout

- `dwarf/grammars/<target>/dict.txt`
  - libFuzzer dictionary entries for one fuzz target
  - entries are quoted byte strings using libFuzzer dictionary syntax
- `dwarf/grammars/<target>/structure.json`
  - lightweight structural notes about the target input shape
  - useful context for how the retained seed inputs are shaped

## Current Plumbing

The retained dictionaries use the same layout expected by
`dwarf/scripts/cargo_fuzz_campaign.py`:

- `dwarf/grammars/<fuzz-dir-name>/dict.txt`

If a future campaign runner uses one of these retained targets, the wrapper can
append:

- `-dict=<path>`

to the `cargo fuzz run ... -- ...` invocation.

## Scope

These grammars do not change decoder behavior or replay semantics. They only
bias mutation toward shapes that stay closer to valid CBOR or mini-protocol
structure.

## Coverage

Retained M2 grammar coverage includes:

- single-implementation parser and mini-protocol seed families
  - block
  - handshake
  - chainsync
  - blockfetch
  - txsubmission

Each `structure.json` is intentionally lightweight. It records the outer case shape, common nested field forms, and decoder entrypoint so future custom-mutator work has a stable starting point without changing the current harnesses.

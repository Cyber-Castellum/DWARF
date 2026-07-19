# cuddle-generated seed corpora

Fuzz seed corpora generated from the **official Cardano CDDL** by
[`cuddle`](https://github.com/input-output-hk/cuddle) — the Phase-1 ("shallow") cuddle integration
(see `../../docs/cuddle-relationship.md`). These are the valid CBOR seeds DWARF's structured
fuzzers mutate, and the valid scaffolding a campaign's setup/load needs.

## Provenance

- **Generator:** `cuddle 1.8.1.1` (`cuddle gen --no-twiddle -f binary -s <seed> <rule> <cddl>`).
- **Schema:** cardano-ledger **Conway** era CDDL (`conway.cddl`).
- **Encoding:** `--no-twiddle` = definite-length (canonical-ish) CBOR, which Cardano decoders expect.
- **Reproducible:** each file is generated at a fixed seed (`-s`), so the corpus regenerates
  byte-identically. Regenerate with `dwarf/scripts/gen_cuddle_seeds.py`.
- See `manifest.json` for the exact shape→rule map, counts, and cuddle version.

## Contents

| shape (dir) | CDDL rule | valid seeds | decodes to |
|---|---|---|---|
| `block-header/` | `header` | 16 | `array[2]` — `[header_body(array[10]), kes_sig(bytes[448])]` |
| `block/` | `block` | 16 | `array[5]` — header, tx-bodies, witness-sets, aux, invalid-txs |
| `tx-body/` | `transaction_body` | 16 | `map{…}` |
| `certificate/` | `certificate` | 16 | `array[10]` |
| `auxiliary-data/` | `auxiliary_data` | 16 | `map{…}` |

Each shape dir also holds 4 **negative** examples (`*-neg-s*.cbor`) — cuddle's "zapped"
(deliberately schema-violating) terms, useful as known-bad inputs.

## Scope (important)

cuddle produces **structurally** valid CBOR — it matches the CDDL shape and decodes cleanly. It does
**not** produce **consensus-valid** data: the VRF proofs, KES/vkey signatures, and body hashes are
random bytes of the right size, so these seeds will pass a decoder but fail consensus/signature
validation. That is by design — DWARF layers semantic validity (real signing via db-synthesizer,
etc.) on top where a test needs it. See the relationship doc's "structural vs semantic" section.

## Wired into the coverage-guided fuzz corpora (task #40)

These seeds are the canonical source; valid ones are also **copied into the AFL++/cargo-fuzz seed
corpora** the coverage-guided fuzzers actually consume (non-destructively — existing hand seeds are
kept, cuddle copies carry a `cuddle-` prefix):

| shape | corpora populated | seeds before → after |
|---|---|---|
| `block-header` | `afl/package-a/block-header-stage1/seeds`, `cargo-fuzz/package-a/block-header-stage1/seeds` | 3→19, 1→17 |
| `tx-body` | `afl/package-a/tx-body-stage1/seeds`, `cargo-fuzz/package-a/tx-body-stage1/seeds` | 3→19, 1→17 |
| `block` | `amaru-cargo-fuzz-block/seeds` | 4→20 |

Re-run the wiring (no cuddle needed) with:
```bash
python3 dwarf/scripts/gen_cuddle_seeds.py --wire-only \
    --out-root dwarf/corpora/cuddle-generated --repo-root .
```
`certificate` and `auxiliary-data` have no coverage-guided corpus yet, so they stay here until one
exists. `cbor_fuzz_target` (the lightweight scenario primitive) feeds *random* bytes and does not
read a seed corpus — seeding it is a separate primitive change (not done here).

**Mini-protocol messages (chainsync/blockfetch/txsubmission) are NOT yet covered:** the
ouroboros-network CDDL is multi-module and uses generics/namespaces (`base.ns8<…>`, `shelley.header`)
that cuddle cannot consume standalone. The pragmatic path is to wrap the already-generated ledger
seeds (header/block/tx) in the thin message envelope — a follow-on.

**Per-era:** proven era-agnostic — point `--cddl` at any era's ledger CDDL (verified with Conway and
Babbage; both emit valid `block`/`header`/`transaction_body`).

## Regenerating / extending

```bash
# build cuddle once (GHC 9.6/9.8/9.10/9.12):  cabal build exe:cuddle
python3 dwarf/scripts/gen_cuddle_seeds.py \
    --cuddle-bin /path/to/cuddle \
    --cddl /path/to/conway.cddl \
    --out-root dwarf/corpora/cuddle-generated \
    --count 16 --negative 4
```

To track a new era, point `--cddl` at that era's ledger CDDL (babbage/alonzo/…). To add the
CDDL-defined **mini-protocol** messages (chainsync/blockfetch/txsubmission), extend the shape→rule
map in the script with the ouroboros-network CDDL — the natural next expansion.

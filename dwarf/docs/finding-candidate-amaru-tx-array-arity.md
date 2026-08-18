# Divergence: Amaru accepts 3-element transaction arrays that cardano-node rejects at decode

**Status:** CONFIRMED at decode level (root cause identified in Amaru source +
empirically verified). Severity OPEN — the mempool-ACCEPT (202) impact is not yet
demonstrated (needs a valid funded tx; see below).
**Date:** 2026-07-22 · **Found by:** DWARF submit-api differential
(`antithesis/amaru_baked_dwarf/`, `docker-compose.differential.yaml`), first pass.

## Root cause (Amaru source)

`crates/amaru-kernel/src/cardano/transaction.rs`:

```rust
#[derive(cbor::Encode, cbor::Decode, ...)]
pub struct Transaction {
    #[n(0)] pub body: TransactionBody,
    #[n(1)] pub witnesses: WitnessSet,
    #[n(2)] pub is_expected_valid: bool,
    #[n(3)] pub auxiliary_data: Option<AuxiliaryData>,   // trailing Option
}
```

`crates/amaru/src/submit_api.rs` decodes the request body with
`minicbor::decode::<Transaction>(&body)`. minicbor's derive encodes this struct as a
CBOR **array** and treats a **missing trailing array element whose field is `Option`
as `None`**. So a 3-element array `[body, witnesses, is_expected_valid]` decodes
successfully with `auxiliary_data = None`. The Conway CDDL mandates 4 elements
(`transaction = [transaction_body, transaction_witness_set, bool, auxiliary_data / null]`),
and cardano-node rejects the 3-element form at deserialization.

### Empirical confirmation of the mechanism (against a live Amaru)
Same base tx, only the top-level array header changed:

| header | Amaru response |
|---|---|
| `0x82` (2 elements) | `Invalid CBOR transaction: missing value at index 2 (Transaction::is_expected_valid)` — **rejected**: the required (non-Option) bool is missing |
| `0x83` (3 elements) | decodes → `failed to prepare transaction 9aa53384… for validation` (aux = None) |
| `0x84` (4 elements) | decodes → **same tx id** `9aa53384…` |

array(2) fails on the required field 2 while array(3) succeeds → proves it is
specifically the trailing `Option<AuxiliaryData>` being omittable, not a blanket
length-agnostic decoder. array(3) and array(4) yield the **same transaction id**
(the id hashes only the body), so the same logical tx has two valid wire encodings
in Amaru but only one in cardano-node — a **non-canonical-encoding / malleability**
divergence.

## What was observed

The Conway transaction wire format is a **4-element** array:
`transaction = [transaction_body, transaction_witness_set, bool, auxiliary_data / null]`.

Starting from a real serialized Conway transaction (built with
`cardano-cli conway transaction build-raw` + `sign`), flipping the **top-level
CBOR array header from `0x84` (array-of-4) to `0x83` (array-of-3)** and POSTing the
same bytes to both nodes' `POST /api/submit/tx`:

| Node | HTTP | Response | Interpretation |
|---|---|---|---|
| cardano-node (Haskell, via cardano-submit-api) | 400 | `DecoderErrorDeserialiseFailure "Shelley Tx" (DeserialiseFailure … "Size mismatch when decoding Record RecD. Expected 3, but found 4" / "expected list len or indef")` → `TxCmdTxReadError` | **rejects at CBOR decode**; never computes a tx id |
| Amaru (baked store, submit-api) | 400 | `failed to prepare transaction <64-hex tx id> for validation` | **decodes the 3-element array**, computes the tx id, proceeds to ledger validation |

**Reproducible across 4 distinct base transactions** (tx2/tx3/tx5/tx7): Haskell
always decode-rejects; Amaru always computes a *distinct, per-input* tx id and
advances to validation. This rules out a one-off / raw-byte-hash coincidence — Amaru
parsed each transaction's content.

Control: for OTHER malformations (e.g. corrupting an inner field), Amaru emits a
clean decode error — `Invalid CBOR transaction: unexpected type … at position N` —
so the array(3) case advancing to validation is genuine decoder acceptance, not a
generic catch-all message.

Both nodes ultimately return 400 (Amaru fails later, at validation-prep), so no
transaction is *accepted* by one and *rejected* by the other at the final outcome —
this is a **decoder-conformance** divergence, not (yet) a demonstrated mempool split.

## Why it matters

- It is exactly the class of finding the differential is built to catch: the two
  implementations disagree on whether a byte string is a well-formed Conway
  transaction. Amaru (a from-scratch reimplementation) is more lenient than the
  reference decoder — a real conformance gap, rooted in a minicbor-derive footgun
  (trailing `Option` field ⇒ omittable array element).
- Same-tx-id malleability: a null-`auxiliary_data` transaction has two encodings
  Amaru accepts (3- and 4-element) but cardano-node accepts only one. Divergent
  acceptance of transaction encodings is the seed of mempool / block-relay splits.

## Remaining severity questions (accept-path blocked here)

Today both nodes return 400 (Amaru's array(3) tx still fails later at
validation-prep — because the *synthetic* seed tx has no real inputs, NOT because of
the arity). To grade severity, on a network where a **valid, funded** transaction can
be built:

1. Does Amaru's mempool **accept** (HTTP 202) the array(3) form of an otherwise-valid
   tx while cardano-node rejects it? → confirmed mempool-divergence.
2. When Amaru (as producer) includes such a tx in a block, does it re-serialize
   canonically or preserve the 3-element bytes? If preserved, cardano-node would
   reject the block → chain-split potential.

This bundle can't reach step 1 (the local devnet's configurator doesn't expose the
genesis UTxO key). A network that funds a spendable key, or a captured valid tx,
closes it.

## Disclosure

If reported upstream (pragma-org/amaru): the fix is to make the `Transaction` decoder
require the 4-element array (reject a missing `auxiliary_data` slot rather than
defaulting it to `None`). Minimal repro: any real Conway tx with its leading `0x84`
array header changed to `0x83`.

## Repro

```
# real Conway tx, then flip byte 0 to 0x83:
python3 -c 'b=bytearray(open("tx.cbor","rb").read()); b[0]=0x83; open("m.cbor","wb").write(bytes(b))'
curl -s -X POST http://<haskell-submit-api>:8090/api/submit/tx -H 'Content-Type: application/cbor' --data-binary @m.cbor
curl -s -X POST http://<amaru>:3011/api/submit/tx        -H 'Content-Type: application/cbor' --data-binary @m.cbor
```

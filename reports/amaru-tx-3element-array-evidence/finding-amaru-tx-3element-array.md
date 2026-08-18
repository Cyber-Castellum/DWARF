# Amaru accepts 3‑element (non‑canonical) Conway transactions that cardano‑node rejects at decode

**Component:** `amaru` transaction CBOR decoder (submit‑API / mempool ingress)
**Type:** CBOR decode conformance divergence vs. the Haskell reference node (non‑canonical encoding / transaction malleability)
**Status:** Confirmed (root cause in source; decode *and* encode behaviour verified against minicbor 0.25.1). Amaru both **accepts** and **emits** the non‑canonical 3‑element transaction encoding that cardano‑node rejects. Scoped to the **tx‑submission / submit‑API** path (not block consensus). The only item not directly observed is a live `HTTP 202` accept (blocked: no funded tx on the local devnet) — supported by source. See *Severity*.
**Found by:** DWARF differential fuzzing — same mutated transaction submitted to Amaru and cardano‑node, comparing decode/accept behaviour.
**Date:** 2026‑07‑22

---

## Summary

A Conway transaction is a **4‑element** CBOR array:

```
transaction = [ transaction_body, transaction_witness_set, bool, auxiliary_data / null ]
```

Amaru’s transaction decoder accepts a **3‑element** array — one that omits the trailing
`auxiliary_data` slot entirely — decoding it as a valid transaction with
`auxiliary_data = None`. The Haskell node (`cardano-node`, via `cardano-submit-api`)
rejects the same bytes at CBOR deserialization.

Because a transaction’s id is the hash of its **body** (element 0) only, the 3‑element and
canonical 4‑element encodings of a null‑auxiliary‑data transaction produce the **same
transaction id**. The same logical transaction therefore has two valid wire encodings in
Amaru but only one in cardano‑node — a non‑canonical‑encoding / malleability divergence.

## Environment

| | |
|---|---|
| Amaru image | `ghcr.io/lambdasistemi/amaru-bootstrap-producer:03d2727b…` (baked testnet_42 store, serve‑only) |
| Reference | `cardano-node` 10.7.1 via `ghcr.io/intersectmbo/cardano-submit-api:10.7.1` |
| Network | testnet_42 (k=5) |
| Endpoint | `POST /api/submit/tx`, `Content-Type: application/cbor` |

## Observed behaviour

Starting from a real, serialized Conway transaction and changing **only** the top‑level CBOR
array header from `0x84` (array‑of‑4) to `0x83` (array‑of‑3):

| Input | cardano‑node | Amaru |
|---|---|---|
| `0x84 …` (canonical, 4 elem) | decodes, rejects at validation | decodes, rejects at validation (same tx id) |
| `0x83 …` (3 elem) | **HTTP 400 — decode error**: `DecoderErrorDeserialiseFailure "Shelley Tx" (DeserialiseFailure … "Size mismatch when decoding Record RecD. Expected 3, but found 4" / "expected list len or indef")` → `TxCmdTxReadError` (no tx id computed) | **decodes** — computes tx id, advances to validation: `failed to prepare transaction <tx_id> for validation` |

Reproducible across multiple distinct base transactions: cardano‑node always fails at decode;
Amaru always decodes and produces a (distinct, per‑input) transaction id.

## Root cause

`crates/amaru-kernel/src/cardano/transaction.rs`:

```rust
#[derive(Debug, Clone, PartialEq, Eq, cbor::Encode, cbor::Decode, serde::Serialize, serde::Deserialize)]
pub struct Transaction {
    #[n(0)] pub body: TransactionBody,
    #[n(1)] pub witnesses: WitnessSet,
    #[n(2)] pub is_expected_valid: bool,
    #[n(3)] pub auxiliary_data: Option<AuxiliaryData>,   // trailing Option
}
```

`crates/amaru/src/submit_api.rs` decodes the request body with
`minicbor::decode::<Transaction>(&body)`.

`minicbor`’s derive encodes this struct as a CBOR **array** and treats a **missing trailing
array element whose field type is `Option<_>` as `None`**. A 3‑element array
`[body, witnesses, is_expected_valid]` therefore decodes successfully with
`auxiliary_data = None`, rather than being rejected for having the wrong arity. The Conway CDDL
requires the 4‑element form (the `auxiliary_data` slot is mandatory and may be `null`), which
is what cardano‑node enforces.

### Mechanism verified empirically

Same base transaction, only the array header varied, submitted to a live Amaru:

| Array header | Amaru response |
|---|---|
| `0x82` (2 elem) | `Invalid CBOR transaction: missing value at index 2 (Transaction::is_expected_valid)` — **rejected** (the required, non‑`Option` `bool` field is enforced) |
| `0x83` (3 elem) | decodes → `failed to prepare transaction 9aa53384…464c83 for validation` |
| `0x84` (4 elem) | decodes → **same** tx id `9aa53384…464c83` |

array(2) failing on the required field while array(3) succeeds confirms the leniency is
specifically the trailing `Option<AuxiliaryData>` being omittable — not a length‑agnostic
decoder — and array(3)/array(4) sharing a tx id confirms the malleability.

## Severity (graded from source)

**Confirmed — decode‑conformance divergence.** Amaru accepts a transaction encoding the reference
node rejects (empirical + source root cause above).

**Confirmed by source — mempool‑admission divergence.** Amaru’s validation pipeline only inspects
the *outer* array structure at decode; everything downstream operates on the decoded fields
(`amaru-ledger/src/rules.rs::prepare_transaction` takes `&transaction.body`, and validation loads the
body’s inputs from the UTxO store). In the test both nodes returned `400`, but Amaru’s failure was
`failed to prepare transaction … for validation` — i.e. it failed loading the **synthetic seed’s
non‑existent inputs**, *not* because of the arity. It follows that an *otherwise‑valid* transaction
submitted in 3‑element form would pass decode + preparation and be **accepted (HTTP 202)** by Amaru’s
mempool, while cardano‑node rejects the same bytes at deserialization. (Not yet shown with a live
`202`: the local devnet’s configurator does not expose the genesis UTxO key, so a valid funded tx
could not be built here.)

**Confirmed — Amaru also EMITS the non‑canonical form.** `Transaction` carries **no original‑bytes
field** (unlike `Block`), and Amaru re‑encodes it via the derived encoder (`to_cbor`, used by the
mempool and the tx‑submission relay — `amaru-protocols/src/tx_submission/messages.rs` does
`e.encode(tx)`). minicbor’s derive **omits a trailing `None` `Option`**, so
`encode(Transaction { auxiliary_data: None, .. })` produces a **3‑element array (`0x83…`)** — verified
directly against minicbor 0.25.1:

```
encode(None):  header=0x83  bytes=[83, 01, 02, f5]        # Amaru's canonical output for a no-aux tx
encode(Some):  header=0x84  bytes=[84, 01, 02, f5, 09]
decode(0x83…): OK  -> auxiliary_data: None                # accepts 3-element
decode(0x84…f6): OK -> auxiliary_data: None               # also accepts canonical 4-element
```

So for **every transaction with no auxiliary data** (a very common case), Amaru’s own serialization is
the 3‑element form that cardano‑node rejects at decode. In a mixed network, a no‑metadata transaction
relayed **by** Amaru over node‑to‑node tx‑submission (or its submit‑API) would be **rejected by
cardano‑node peers as malformed CBOR** → a transaction‑propagation asymmetry.

**Scope — tx‑submission path, NOT block consensus.** The 4‑tuple `[body, witnesses, is_valid, aux]`
encoding is used only for a *standalone* transaction (the submit‑API and the node‑to‑node
tx‑submission mini‑protocol). In a **block**, transactions are decomposed into parallel arrays
(`transaction_bodies`, `transaction_witnesses`, `auxiliary_data` map — confirmed in Amaru’s own
`Block` type), so this encoding is not used there and the finding does **not** by itself cause a
block‑validation / chain split. Impact is transaction relay + mempool admission + malleability.

## Scope (bug‑class audit)

The root cause is a minicbor‑derive footgun (a trailing `Option` field makes an array element
omittable). An audit of `amaru-kernel` shows it is **contained to `Transaction`**, not a broad class:

- **`Transaction`** — `#[derive(cbor::Decode)]`, array‑encoded, trailing `auxiliary_data: Option<_>` → **vulnerable** (this finding).
- **`Block`** — hand‑written `Decode` using `heterogeneous_array(…, assert_len(CBOR_FIELD_COUNT))` → **enforces arity** (safe), even though its last field is also `Option`.
- **`WitnessSet`** — `#[derive(cbor::Decode)]` but `#[cbor(map)]` → optional keys are legal CBOR‑map behaviour (safe).
- Other array‑encoded ledger types use hand‑written decoders with explicit length assertions.

So the reference node’s stricter decoders and Amaru’s own *manual* decoders agree; only the *derived*
`Transaction` decoder is lenient.

## Reproduction

```bash
# 1. Obtain any real serialized Conway transaction (e.g. cardano-cli conway transaction
#    build-raw + sign, then take the envelope's cborHex), as tx.cbor.

# 2. Flip the leading CBOR array header 0x84 -> 0x83:
python3 -c 'b=bytearray(open("tx.cbor","rb").read()); assert b[0]==0x84; b[0]=0x83; open("tx3.cbor","wb").write(bytes(b))'

# 3. Submit the 3-element form to each node:
curl -s -X POST http://<cardano-submit-api>:8090/api/submit/tx \
     -H 'Content-Type: application/cbor' --data-binary @tx3.cbor
# -> 400, DecoderErrorDeserialiseFailure (decode rejected)

curl -s -X POST http://<amaru>:3011/api/submit/tx \
     -H 'Content-Type: application/cbor' --data-binary @tx3.cbor
# -> "failed to prepare transaction <tx_id> for validation" (decoded)
```

## Suggested remediation

Make `Transaction` decoding require the full 4‑element array — reject a transaction whose
top‑level array omits the `auxiliary_data` slot rather than defaulting it to `None`. (The
canonical encoding of a transaction with no auxiliary data uses `null` as element 3, not a
2‑ or 3‑element array.) More broadly, audit other array‑encoded ledger types with trailing
`Option` fields for the same `minicbor` derive behaviour.

## How this was found

DWARF ran a differential: the same fuzzed transaction CBOR is POSTed to both Amaru’s submit‑API
and cardano‑node’s `cardano-submit-api`, and the responses are compared. Because the submit‑API
returns `400` for both “failed to decode” and “decoded but invalid”, the oracle parses the
response body to distinguish **decode failure** from **validation failure**, and asserts that the
two implementations agree on whether the bytes *decode as a transaction*. This divergence was
surfaced by a single‑byte mutation (`0x84`→`0x83`) of a real transaction on the first pass.

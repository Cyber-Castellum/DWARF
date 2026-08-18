# Evidence bundle — Amaru accepts 3‑element Conway transactions (decode divergence)

Supporting logs, seeds, source, and reproduction for the finding
`finding-amaru-tx-3element-array.md` (included). Generated 2026‑07‑22 against a live
Amaru node and a live cardano‑node reference, testnet_42.

## Contents

- **`finding-amaru-tx-3element-array.md`** — the write‑up (summary, root cause, severity, fix).
- **`environment.txt`** — images, versions (Amaru 10.10.0, cardano‑node 10.7.1), network, endpoint.

- **`seeds/`** — the exact transaction bytes submitted (real serialized Conway tx, one byte apart):
  - `tx-canonical-0x84.cbor` — the real tx (top‑level CBOR `array(4)`), canonical.
  - `tx-3element-0x83.cbor` — same bytes, array header changed `0x84`→`0x83` (`array(3)`). **The divergence input.**
  - `tx-2element-0x82.cbor` — same bytes, `0x82` (`array(2)`) — the control (required field dropped).
  - `*.hex` — `xxd` dumps of each.

- **`responses/`** — raw HTTP responses (headers + body + status) from each node for each seed:
  - `cardano-node_3element-0x83.txt` — **HTTP 400, `DecoderErrorDeserialiseFailure` (decode rejected)**.
  - `amaru_3element-0x83.txt` — **decodes**: `failed to prepare transaction 9aa53384… for validation`.
  - `amaru_2element-0x82.txt` — control: `Invalid CBOR transaction: missing value at index 2 (Transaction::is_expected_valid)` (Amaru *does* enforce the required field).
  - `amaru_canonical-0x84.txt` / `cardano-node_canonical-0x84.txt` — the canonical tx on both (same tx id from Amaru as the 3‑element form).
  - `cardano-node_2element-0x82.txt` — control on the reference.

- **`differential/drive_submit_60seeds.txt`** — the DWARF differential run: same mutated CBOR to both
  nodes over 60 seeds, classifying decode vs validation failure. `seed=0` is flagged
  `decode_agree=False` (`<<< DECODE DIVERGENCE`); `1/60`.

- **`source/`** — the exact Amaru source establishing root cause + scope:
  - `transaction.rs` — the `Transaction` struct; field `#[n(3)] auxiliary_data: Option<AuxiliaryData>` (derived decoder → lenient).
  - `submit_api.rs` — decodes the body with `minicbor::decode::<Transaction>()`.
  - `block.rs` — shows blocks use **parallel arrays** (`transaction_bodies`/`transaction_witnesses`/`auxiliary_data`) and a manual `assert_len(5)` decoder → scopes the finding to tx‑submission, not blocks.
  - `AMARU-COMMIT.txt` — the pragma‑org/amaru commit the source is from.

- **`minicbor-proof/`** — a standalone minicbor 0.25.1 program (same array + trailing‑`Option` shape as
  `Transaction`) proving the derive behaviour, with `OUTPUT.txt`: **`encode(None)` → `0x83` (3‑element)**
  and both `0x83`/`0x84` decode to `auxiliary_data: None`. This is why Amaru both accepts and emits the
  non‑canonical form. Run: `cd minicbor-proof && cargo run`.

- **`workload/`** — the DWARF tooling that found it:
  - `workload.py` — the submit‑api fuzz workload, incl. `classify_decode` (decode‑vs‑validation) and `drive_submit` (the differential + `decode_agree` assertion).
  - `diffcap.py` — the driver used to produce `differential/drive_submit_60seeds.txt`.

## One‑line reproduction

```bash
python3 -c 'b=bytearray(open("seeds/tx-canonical-0x84.cbor","rb").read()); b[0]=0x83; open("tx3.cbor","wb").write(bytes(b))'
curl -s -X POST http://<cardano-submit-api>:8090/api/submit/tx -H 'Content-Type: application/cbor' --data-binary @tx3.cbor   # 400, DecoderError
curl -s -X POST http://<amaru>:3011/api/submit/tx            -H 'Content-Type: application/cbor' --data-binary @tx3.cbor   # decodes -> "failed to prepare transaction <id> for validation"
```

## What is proven vs. open
- **Proven (empirical):** Amaru decodes a 3‑element transaction array (`auxiliary_data` omitted →
  `None`); cardano‑node rejects it at deserialization. `array(2)` is correctly rejected → the leniency
  is specifically the optional trailing slot. `array(3)`/`array(4)` yield the same tx id → malleability.
- **Proven (minicbor + source):** Amaru also **emits** `0x83` (3‑element) for any no‑auxiliary‑data
  transaction (`minicbor-proof/`), and re‑encodes on relay (`source/transaction.rs` has no raw‑bytes
  field; tx‑submission does `e.encode(tx)`). So in a mixed network Amaru would relay no‑metadata txs in
  a form cardano‑node peers reject at decode.
- **Scoped (source):** the 4‑tuple encoding is used only for standalone txs (submit‑API + tx‑submission);
  blocks use parallel arrays (`source/block.rs`), so this is **not** a block/consensus split.
- **Open (not in this bundle):** a live `HTTP 202` accept of an otherwise‑valid, funded 3‑element tx —
  supported by source (validation ignores outer arity) but not shown, because a valid funded tx couldn’t
  be built on the local devnet (genesis UTxO key not exposed).

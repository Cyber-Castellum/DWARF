# Governance feasibility spike — findings (2026-06-30)

**Verdict: Approach 1 viable. The Conway devnet is governance-ready. No governance bootstrap needed.**

## Confirmed
- **Devnet gov state:** committee **seated** (`committee=True`), `govActionDeposit = 100,000,000,000` lovelace (100,000 ADA). Era Conway.
- **Funded proposer:** `payment.1` + `stake.1` base address
  `addr_test1qz75g6s5f0t5he4qh9x8p9xxhjkyxm7ajkp67klqgdq8dedsxus3vre9cqa243j8v69wurxjncl55dts6g4tjzxwm7ds2ja0n3`
  holds **~199,999,851 ADA** (1 utxo) — ample for the 100k deposit. (payment.2/3, genesis.2/3 = 0 funds.)
- **Valid path WORKS:** `create-info → build-raw (--proposal-file) → sign → submit` → **"Transaction successfully submitted"**, txhash `784c7b05f9c33c60ed3c49110d341d2ab125439bb7e392a3e862c05050c66ff8`. The node **accepted** a valid gov action.
- **Invalid path REJECTS:** the wrong-deposit variant was rejected by the ledger (`ApplyTxError` / `ConwayMempoolFailure`).

## cli flag facts (10.15 in relay1; 10.11 in p1 — use relay1's)
- `conway governance action create-info` takes **`--testnet`** (a switch), NOT `--testnet-magic 42`.
- `address build`, `transaction build-raw/sign/submit`, `query utxo/gov-state` take `--testnet-magic 42`.
- `create-info` flags: `--governance-action-deposit NATURAL`, `--deposit-return-stake-verification-key-file`, `--anchor-url`, `--anchor-data-hash`, `--testnet`, `--out-file`.
- cli runs as root in the relay1 image via `sudo -n docker run` (passwordless sudo works); output files are root-owned — read them via a container or just keep CBOR handling inside the container chain (submit doesn't need host reads).

## Two real constraints for the battery (→ plan adjustment, NOT a governance bootstrap)
1. **Ledger check ordering:** input-validity precedes the GOV deposit check. The wrong-deposit
   tx, reusing the input the pending valid tx already reserved, was rejected with
   `ConwayMempoolFailure "All inputs are spent…"` — the **input check fired first**. (Same shape
   as the witness run's inputs-spent-before-witness honest-limit.) To isolate a *specific* GOV
   predicate failure (e.g. `ProposalDepositIncorrect`), each variant needs **valid unspent inputs**.
2. **Single funded address + slow-ish devnet confirmation:** only `payment.1` is funded, and the
   devnet doesn't confirm a new block instantly, so serial txs starve each other of inputs.

## Plan adjustment: fan-out the funded utxo
Before building the violation battery, **fan out `payment.1`'s single utxo into N independent
outputs** (one `build-raw` with N `--tx-out`s back to the same address), giving N fresh inputs —
one per violation variant — so each variant has valid unspent inputs and isolates its own GOV
rule. This is a small builder step (`gov-corpus-build.sh` Step 0), not a governance bootstrap.

## Bootstrap decision
**No governance bootstrap required** — committee seated, deposit param set, proposer funded,
valid submission accepted. Votes (DRep-authored) and lineage actions (needing a prev-`GovActionId`)
can use the just-submitted InfoAction's id once it confirms; the committed L2 baseline
(`info`/`constitution`/`treasury` proposals + the 5 violation variants) needs only the fan-out.

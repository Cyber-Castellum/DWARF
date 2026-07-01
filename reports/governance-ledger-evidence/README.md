# Conway governance ledger coverage — evidence

Closes the governance gap: the Conway governance ledger code was untested by every prior DWARF
engine (the corpus never held a governance tx). This cycle built a gov corpus and ran it through
the proven engines on both routes. Node-safe everywhere; correct per-rule rejection proven; no
defect. Full campaign report: `../campaign-reports/dwarf-governance-ledger-coverage.html`.

## Result

- **L2 per-rule battery (local):** one-rule-invalid gov txs rejected at the exact
  `ConwayGovPredFailure`, 0 adopted — 3/3 rules (`ProposalDepositIncorrect`,
  `ZeroTreasuryWithdrawals`, `InvalidPrevGovActionId`), 0 validation-bypass.
- **L1 in-process campaign (local, 8h):** 1,316,880,384 gov decodes (255,249,030 fully decoded),
  0 uncaught exceptions, 0 timeouts. `gov-8h-campaign.log`.
- **Antithesis:** 1h node-safe (0 real findings) + 3h faults `outcome:success` (80/2, node-safe
  under adversary + fault injection). `governance-antithesis-triage-evidence.md`.

## Files

| File | What |
|---|---|
| `dwarf-gov-spike.md` | feasibility spike (devnet gov-ready; cli flags; ledger check ordering) |
| `gov-corpus-build.sh` | build valid gov wire GenTxs + one-rule-invalid variants (cardano-cli conway governance; utxo fan-out) |
| `wrap_gentx.py` | wrap a signed tx into the node-to-node wire GenTx `[6, tag24(tx)]` (no deps) |
| `gov-violations.json` | the violation matrix (variant → expected `ConwayGovPredFailure`) |
| `gov-l2-battery.sh` | submit each variant to a forging gov devnet; assert reject at the expected rule, 0 adopted |
| `gov-8h-campaign.log` | the 8h in-process campaign, hourly chunks (level rotation), 0 exc/0 timeout |

## Reproduce

Requires a forging Conway devnet with a seated committee + funded proposer (an isolated copy of
the `cardano_node_dwarf` bundle forges immediately and is governance-active from the shared
genesis). `bash gov-corpus-build.sh <project> <outdir>` → `bash gov-l2-battery.sh <project> <outdir>`.
In-process volume: `dwarf-decoder-fuzz --target tx --shape governance --corpus <outdir> --level {struct|semantic|both} --seed random --seconds N`.

## Net-new adversary code

`--cbor-shape governance` (`Target.hs`, proposal_procedures = tx_body key 20), commit `a4ddc25`.
`--shape governance` (`dwarf-decoder-fuzz`, land mutations in the gov decoder), commit `7e13743`.

## Limits / next

L2 covers 3 rules; network-id / bad-account / unregistered-voter / malformed are incremental
breadth. L3 ratification/enactment (vote an action through, probe enactment) is the deferred
stretch (needs DReps/CC/voting rounds). Next corpus cycles: Plutus, multi-asset.

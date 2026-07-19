# Bootstrap trust-source differential evidence

Backs `../campaign-reports/dwarf-consensus-bootstrap-trust-source-differential.html`,
`../../dwarf/docs/consensus-report-bootstrap-trust-source.md`, and the finding note
`../../dwarf/docs/finding-amaru-bootstrap-trust-source.md`.

## The gap

The strongest true gap in the differential suite: **"where did this node's truth come from?"**
Two nodes can converge on the same tip while having reached their *initial trusted state* by
different means (genesis validation / checkpoint / Mithril snapshot / synthetic bundle). DWARF's
oracles watch tip convergence but do not model trust provenance. This is a **source analysis** —
deterministic, no flaky runtime — mapping each implementation's bootstrap trust root.

## Result (from source)

- **cardano-node** can bootstrap **trustlessly from genesis** (validate every block, derive its
  epoch nonces from validated history); it also ships **checkpoints** (`npcCheckpointsFile`) as a
  history-rewrite defence.
- **Amaru cannot validate from genesis** — `amaru node run` requires pre-populated ledger/chain
  stores (`build_node.rs`: "ledger tip header not found"); every bootstrap route imports a
  **snapshot**. Amaru carries **compiled-into-the-binary trust anchors** per network
  (`config/bootstrap/{mainnet,preprod,preview}/`): initial **nonces** (leader-election seed),
  bootstrap **headers**, a snapshot point. Well-known-net ledger snapshots come from a **Mithril
  aggregator** (hardcoded endpoint + verification key, `mithril.rs`); custom-net snapshots are
  imported locally and trusted as-is. Amaru has **no checkpoint** (trusted-block-hash) mechanism.

So the two clients **do not share a root of trust for their initial state** — including the
consensus-critical leader-election nonces (cardano-node derives them; Amaru pins them at build
time). This is not an exploit — it is a trust-provenance model difference a tip-convergence oracle
cannot see, plus a new test dimension DWARF should add.

## `source-citations.txt`

The concrete source references: the compiled-in `config/bootstrap/` tree, the mainnet `nonces.json`
(the leader-election seed), the Mithril `aggregator_details` hardcoded endpoints/keys, and the
`build_node.rs` line that makes a populated store mandatory.

## Next step (adversarial)

Extend the harness with a **trust-source dimension**: bootstrap each node from each supported
source, record provenance, and **tamper an anchor** (a nonce in `nonces.json`, or an imported
snapshot) to test whether the node detects it — the natural follow-up to this source map.

No credentials present (only public source references + hardcoded public trust anchors).

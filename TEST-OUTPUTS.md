# Dwarf V3 Milestone 2 Test Outputs

This delivery includes retained Milestone 2 (M2) execution output from prior Dwarf runs under:

```text
dwarf/evidence/m2-first-executions/
```

Each bundle preserves the Dwarf forensic run shape where present: `manifest.json`, `scenario.yaml`, `log.ndjson`, `assertions.json`, `chain.json`, environment capture, and output/probe directories. Resource-run command output is embedded in `log.ndjson` payloads.

Six bundles are also copied into `dwarf/runs/` so the running dashboard can show clean examples in `/operate/runs` and `/operate/runs/<run-id>`. Those six verify successfully with `cardano-profile verify --runs-dir dwarf/runs <run-id>`.

The same six verified examples are exported as `dwarf/bundles/*.tar.gz` archives. Deployment seeds those archives into the runtime `bundles/` directory so `/operate/bundles` and the Operate overview show preserved bundle examples.

## Summary

| Group | Bundles | Material |
|---|---:|---|
| `random-fuzz/` | 10 | Concise Binary Object Representation (CBOR) random fuzz executions for Amaru and cardano-node parser surfaces. |
| `edge-cases/` | 3 | CBOR transaction-body edge-case executions and compare bundle. |
| `resource-first-executions/` | 3 | Resource baseline, disk-fill smoke, and resident set size (RSS) time-series executions. |
| `serdes-substrate/` | 3 | Composed-substrate serialization/deserialization executions from the Linux target host used for first execution. |

## Dashboard-Ready Verified Examples

These runs are present in `dwarf/runs/` and pass the framework tamper-chain check inside this package:

| Run id | Scenario id | Verification |
|---|---|---|
| `20260419T020533Z-aa19a2d4` | `amaru-cbor-block-header-fuzz` | `cardano-profile verify` passes |
| `20260419T020548Z-b5b12931` | `amaru-cbor-certificate-fuzz` | `cardano-profile verify` passes |
| `20260419T020604Z-32d0b67b` | `amaru-cbor-tx-body-fuzz` | `cardano-profile verify` passes |
| `20260419T020620Z-e2ea4364` | `cardano-node-cbor-block-header-fuzz` | `cardano-profile verify` passes |
| `20260419T020837Z-145dea84` | `cardano-node-cbor-certificate-fuzz` | `cardano-profile verify` passes |
| `20260419T021055Z-064a887c` | `cardano-node-cbor-tx-body-fuzz` | `cardano-profile verify` passes |

## Preserved Bundle Archives

These archive files are present under `dwarf/bundles/` and are seeded into the deployed runtime:

| Archive |
|---|
| `20260419T020533Z-aa19a2d4.tar.gz` |
| `20260419T020548Z-b5b12931.tar.gz` |
| `20260419T020604Z-32d0b67b.tar.gz` |
| `20260419T020620Z-e2ea4364.tar.gz` |
| `20260419T020837Z-145dea84.tar.gz` |
| `20260419T021055Z-064a887c.tar.gz` |

## Cross-Implementation Comparison Evidence

One retained edge-case comparison is included under `dwarf/evidence/m2-first-executions/edge-cases/20260419T100302Z-1e14f218/cross-impl-comparison.md`. It records an `AGREED` comparison for `edge-cases-cbor-tx-body-amaru` with seed `0xEDCA0001`, pairing Amaru run `20260419T100302Z-47d24518` and cardano-node run `20260419T100302Z-1e14f218`.

The Operate comparison view indexes this retained markdown evidence as source material. It is shown as retained comparison evidence rather than as another standalone tamper-verified dashboard run.

## Viewing In The Dwarf Web Interface

After deployment, open:

```text
http://<host>:<port>/operate/runs
```

The six dashboard-ready examples appear in the Runs table. Click a run id to open:

```text
http://<host>:<port>/operate/runs/<run-id>
```

The exported examples appear at:

```text
http://<host>:<port>/operate/bundles
```

The retained comparison evidence appears at:

```text
http://<host>:<port>/operate/compare
```

The run inspector explains and displays the bundle manifest, assertion summary, tamper-chain verdict, event log, resource snapshot fields, and export/replay/verify commands. The Learn tab also includes bundle guidance at:

```text
http://<host>:<port>/learn/walkthroughs
http://<host>:<port>/learn/operator-runbook
http://<host>:<port>/learn/glossary
```

## Chain Note

The remaining retained evidence bundles are legitimate prior run records with valid JSON/NDJSON, preserved manifests, scenarios, logs, chain files, and pass-status run metadata. Their `chain.json` entries point to predecessor hashes outside this curated V3 package, so they should be shown as retained test-output examples rather than standalone tamper-verified dashboard examples.

## Random Fuzz Outputs

| Run id | Scenario id | Assertion results |
|---|---|---|
| `20260419T020533Z-aa19a2d4` | `amaru-cbor-block-header-fuzz` | `parse_succeeds_or_clean_error=pass`; `roundtrip_equals_original=pass` |
| `20260419T020548Z-b5b12931` | `amaru-cbor-certificate-fuzz` | `parse_succeeds_or_clean_error=pass`; `roundtrip_equals_original=pass` |
| `20260419T020604Z-32d0b67b` | `amaru-cbor-tx-body-fuzz` | `parse_succeeds_or_clean_error=pass`; `roundtrip_equals_original=pass` |
| `20260419T020620Z-e2ea4364` | `cardano-node-cbor-block-header-fuzz` | `parse_succeeds_or_clean_error=pass`; `roundtrip_equals_original=pass` |
| `20260419T020837Z-145dea84` | `cardano-node-cbor-certificate-fuzz` | `parse_succeeds_or_clean_error=pass`; `roundtrip_equals_original=pass` |
| `20260419T021055Z-064a887c` | `cardano-node-cbor-tx-body-fuzz` | `parse_succeeds_or_clean_error=pass`; `roundtrip_equals_original=pass` |
| `20260419T033720Z-c4d7766b` | `amaru-cbor-auxiliary-data-fuzz` | `parse_succeeds_or_clean_error=pass`; `roundtrip_equals_original=pass` |
| `20260419T033736Z-2c8be84e` | `amaru-cbor-block-fuzz` | `parse_succeeds_or_clean_error=pass`; `roundtrip_equals_original=pass` |
| `20260419T033751Z-7390cedb` | `cardano-node-cbor-auxiliary-data-fuzz` | `parse_succeeds_or_clean_error=pass`; `roundtrip_equals_original=pass` |
| `20260419T034010Z-b6f485a1` | `cardano-node-cbor-block-fuzz` | `parse_succeeds_or_clean_error=pass`; `roundtrip_equals_original=pass` |

## Edge-Case Outputs

| Run id | Scenario id | Assertion results |
|---|---|---|
| `20260419T100230Z-2a675d55` | `edge-cases-cbor-tx-body-cardano-node` | `parse_succeeds_or_clean_error=pass` |
| `20260419T100230Z-84244a77` | `edge-cases-cbor-tx-body-amaru` | `parse_succeeds_or_clean_error=pass` |
| `20260419T100302Z-1e14f218` | `edge-cases-cbor-tx-body-amaru-cardano-node` | `parse_succeeds_or_clean_error=pass` |

## Resource First-Execution Outputs

| Run id | Scenario id | Recorded output |
|---|---|---|
| `20260419T085613Z-1efa2000` | `resource-baseline-cardano-nodes` | `log.ndjson` records `load_shell_command` completion with process memory, `free -m`, disk, and profile disk-usage output. |
| `20260419T085614Z-7bd3b356` | `resource-disk-fill-host-tmpdir` | `log.ndjson` records `load_shell_command` completion for a controlled 256 MiB disk-fill smoke and cleanup. |
| `20260419T085615Z-15fd6dd2` | `resource-rss-time-series-cardano-nodes` | `log.ndjson` records `load_shell_command` completion for 60 RSS samples across running cardano-node process identifiers (PIDs). |

## Serdes Substrate Outputs

| Run id | Scenario id | Assertion results |
|---|---|---|
| `20260428T064357Z-223bf4d2` | `runtime-substrate-serdes-blockfetch-invalid-block-cbor-example-smoke` | `all_nodes_responsive=pass`; `blockfetch_invalid_block_rejected=pass` |
| `20260428T064422Z-af3bdbf1` | `runtime-substrate-serdes-txsubmission-unexpected-body-example-smoke` | `all_nodes_responsive=pass`; `txsubmission_unexpected_body_rejected=pass` |
| `20260428T064446Z-efbb7670` | `runtime-substrate-serdes-malformed-input-differential-example-smoke` | `all_nodes_responsive=pass`; `malformed_input_parity_preserved=pass` |

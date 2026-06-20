# Dwarf V3 M2 Directory Audit

This is the working tasklist for cleaning `dwarf/` for public V3/M2 delivery.
The goal is to keep what is useful or required for M2, especially Dwarf
framework operation, first execution evidence, and serialization/deserialization
material, while removing unrelated future, internal, generated, or confusing
material.

## Scope Rules

Keep material when it is one of:

- Required for the deployed Dwarf dashboard or `cardano-profile` CLI to run.
- Required for M2 serialization/deserialization scenarios, targets, primitives,
  profiles, first execution outputs, preserved runs, or bundles.
- A small, clearly scoped M2 example corpus or fixture that supports the
  serialization/deserialization work.
- Public-facing documentation needed to install, operate, or understand the V3
  delivery.

Remove or quarantine material when it is one of:

- Future milestone work, especially M3+ protocol-sequence catalogs.
- Broad fuzzing campaigns not included in M2 delivery.
- Internal notes, old host-specific helpers, old hosted-dashboard scripts, or
  generated cache files.
- Unscoped examples that make the package appear to claim work outside M2.
- Files with private/local host assumptions unless they are preserved evidence
  and explicitly documented as historical execution output.

## Current Checklist

- [x] Remove non-M2 `dwarf/fuzz-tests/` catalog from public V3 delivery.
- [x] Verify deployed dashboard reports `Fuzz tests: 0`.
- [x] Restore `dwarf/corpora/` after recognizing that corpus material can be
      relevant to serialization/deserialization fuzzing.
- [x] Split `dwarf/corpora/` into keep/remove decisions instead of deleting it
      wholesale.
- [x] Audit every top-level directory under `dwarf/`.
- [x] Patch public dashboard text so it only describes retained M2 material.
- [x] Update delivery contract so removed directories cannot come back.
- [x] Rebuild and redeploy on `build-host`.
- [x] Verify live `/operate` and key subpages after cleanup.
- [ ] Commit and push only after verification passes.

## Directory Decisions

| Directory | Files | Initial finding | Decision | Notes / next action |
| --- | ---: | --- | --- | --- |
| `dwarf/bundles/` | 6 | Preserved M2 example bundles. | Keep | Required for `/operate/bundles` and client-visible examples. |
| `dwarf/corpora/` | 43 retained | Mixed: useful ser/des seeds plus non-M2 campaign material. | Keep scoped | Retained package-A CBOR seeds and Amaru CBOR/mini-protocol seed examples; removed M3, Plutus, crash fixture, differential, and ledger campaign seeds. |
| `dwarf/dashboard/` | 76 | Dashboard templates/static assets. | Keep, audit text | Required for web app. Remove or update stale public copy. |
| `dwarf/devnet-build/` | 1 | Cardano-node Dockerfile with old host-specific comment. | Review | Keep only if deploy/runtime path needs it; otherwise remove. |
| `dwarf/docs/` | 8 retained | Mixed operator/framework docs; includes M2 ser/des docs. | Keep scoped | Removed broad production/future/stale web UI docs; patched retained docs for V3/M2 language. |
| `dwarf/evidence/` | 213 | First execution evidence, likely M2-relevant but contains historical paths. | Keep, document | Historical execution output may contain host paths; make scope clear. |
| `dwarf/evidence-packages/` | 4 | Package summaries used by dashboard. | Keep, audit | Ensure no old fuzz-test path or non-M2 claims remain. |
| `dwarf/extractors/` | 5 | Helper extraction scripts. | Review | Keep only if dashboard/scenarios need them. |
| `dwarf/grammars/` | 11 retained | Fuzz/mutator dictionaries. | Keep scoped | Retained CBOR/mini-protocol dictionaries; removed differential and ledger campaign dictionaries. |
| `dwarf/primitives/` | 20 | Primitive registry and schemas. | Keep scoped | Required for scenario definitions. |
| `dwarf/profile_manager/` | 114 | CLI/dashboard framework code. | Keep, audit defaults | Required for app. Audit host-specific defaults and public strings. |
| `dwarf/profiles/` | 19 | Runtime profiles. | Keep scoped | User asked profiles be restored; verify all included profiles are acceptable. |
| `dwarf/runs/` | 48 | Preserved run examples for dashboard. | Keep | Required for client-visible examples. |
| `dwarf/scenarios/` | 29 | M2 scenarios plus README. | Keep scoped | Verify count and titles match M2. |
| `dwarf/scripts/` | 110 | Runtime/helper scripts. | Keep, audit | Required by primitives; remove only clearly orphaned/internal scripts. |
| `dwarf/smoke-tests/` | 5 | Smoke test metadata. | Keep scoped | Confirm no host-specific paths or old package references. |
| `dwarf/spec/` | 4 | Scenario schema/spec docs. | Keep, audit | Update examples if they point to M3 as default. |
| `dwarf/targets/` | 29 manifests | Target manifests and harness code; original tree was too broad. | Keep scoped | Retained Amaru/cardano-node decoder shim trees and 29 M2 manifests; removed non-M2 harness trees and stale shim files. |
| `dwarf/tests/` | 0 retained | Internal development pytest suite. | Remove | Delivery verification lives under `delivery/tests/`; internal pytest fixtures are not client delivery material. |

## Notes Log

### 2026-06-05

- `dwarf/fuzz-tests/` was removed because it exposed 121 broad fuzz-test
  catalog entries, including non-M2 candidate material and local paths.
- Live deployed dashboard was verified at `http://192.0.2.1:8879` after
  that change and reported `Fuzz tests: 0`.
- `dwarf/corpora/` was initially removed too broadly, then restored locally.
  The correct approach is a scoped M2 corpus subset, not wholesale deletion.
- Current retained M2 scenario files do not reference `dwarf/corpora/` directly:
  random and structured CBOR fuzz scenarios use seeds/shapes inline, and
  edge-case scenarios embed hex inputs inline. However, corpus seed files are
  still useful supporting material for serialization/deserialization fuzzing.
- Top-level directory scan found these public-delivery risk areas:
  - `dwarf/corpora/`: mixed M2 and non-M2 corpus material.
  - `dwarf/devnet-build/`: one Dockerfile with local/build-host assumptions; referenced by `dwarf/profile_manager/profiles.py`.
  - `dwarf/grammars/`: useful for fuzz/dictionary work, but contains ledger/future dictionaries and an old build-host sync note.
  - `dwarf/targets/`: large target tree with M2 manifests plus many non-M2 ledger, Plutus, local-protocol, and future harnesses.
  - `dwarf/tests/`: useful for verification but contains local fixtures and old route assumptions; likely not client delivery material.
  - `dwarf/profile_manager/`: required for the app, but still contains some local default paths and legacy help text that need careful review.
- Historical `dwarf/evidence/` and `dwarf/runs/` contain `build-host` and `${HOME}` strings as recorded execution output. Those should not automatically be treated like public docs/code; they may be valid preserved evidence if the surrounding docs make that clear.
- `dwarf/targets/manifests/` currently contains 98 manifests. The dashboard's
  `/operate/targets` page intentionally filters this down to the 29 M2
  `*-cbor-decode-*` and `*-mini-protocol-decode-*` targets. That means the UI
  is scoped, but the repository still carries non-M2 target manifests and
  harness source directories that need a keep/remove decision.
- Cleanup batch retained 29 M2 target manifests and made the repo match the
  dashboard scope.
- Verification after cleanup:
  - Local delivery contract passed.
  - JSON/YAML metadata parsed successfully.
  - Python framework modules compiled successfully.
  - Local dashboard returned HTTP 200 for `/operate`, key Operate subpages,
    `/learn`, and `/api/status`.
  - `build-host` delivery contract passed.
  - `build-host` rebuilt and redeployed `dwarf/framework:june-20260604-m2`
    as `dwarf-fw-june-m2` on `0.0.0.0:8879`.
  - Live dashboard returned HTTP 200 for `/operate`, `/operate/targets`,
    `/operate/scenarios`, `/operate/profiles`, `/operate/runs`,
    `/operate/bundles`, `/operate/crashes`, `/operate/coverage`,
    `/operate/runs/20260419T020533Z-aa19a2d4`, `/learn`, and `/api/status`.
  - Live `/operate/targets` browser snapshot showed `29 M2 targets`.
  - Container-internal checks showed 29 manifests, 43 corpus files, and no
    `dwarf/tests`, `dwarf/fuzz-tests`, or `dwarf/corpora/m3`.

### `dwarf/corpora/` split notes

Retained M2 corpus set:

- `dwarf/corpora/afl/package-a/block-header-stage1/seeds/`
- `dwarf/corpora/afl/package-a/tx-body-stage1/seeds/`
- `dwarf/corpora/amaru-cargo-fuzz-block/seeds/`
- `dwarf/corpora/amaru-cargo-fuzz-blockfetch/seeds/`
- `dwarf/corpora/amaru-cargo-fuzz-chainsync/seeds/`
- `dwarf/corpora/amaru-cargo-fuzz-handshake/seeds/`
- `dwarf/corpora/amaru-cargo-fuzz-keepalive/seeds/`
- `dwarf/corpora/amaru-cargo-fuzz-localstatequery/seeds/`
- `dwarf/corpora/amaru-cargo-fuzz-localtxmonitor/seeds/`
- `dwarf/corpora/amaru-cargo-fuzz-localtxsubmission/seeds/`
- `dwarf/corpora/amaru-cargo-fuzz-peersharing/seeds/`
- `dwarf/corpora/amaru-cargo-fuzz-txsubmission/seeds/`
- `dwarf/corpora/cargo-fuzz/package-a/block-header-stage1/seeds/`
- `dwarf/corpora/cargo-fuzz/package-a/submit-api-tx-stage1/seeds/`
- `dwarf/corpora/cargo-fuzz/package-a/tx-body-stage1/seeds/`

These are small CBOR or malformed-input seed sets tied to parser,
transaction-body, block-header, and submit-API serialization/deserialization
coverage.

Removed:

- `dwarf/corpora/m3/` because it is explicitly M3 protocol sequence/state-machine material.
- `dwarf/corpora/plutus-phase2/` because it is not M2 serialization/deserialization delivery.
- `dwarf/corpora/crash-triage-example/` because it is a visual fixture, not M2 delivery evidence.
- `dwarf/corpora/differential-rule-harness-example/` because it is an example fixture, not first execution evidence.
- `dwarf/corpora/amaru-cardano-differential-cargo-fuzz-*`
- `dwarf/corpora/amaru-cargo-fuzz-ledger-*`

## Verification Commands

Run these after every cleanup batch:

```bash
bash delivery/tests/test_delivery_contract.sh
python3 - <<'PY'
import json
from pathlib import Path
for root in ["dwarf/profiles", "dwarf/smoke-tests"]:
    for path in Path(root).rglob("*.json"):
        json.load(path.open())
print("json metadata ok")
PY
python3 - <<'PY'
import urllib.request
for path in ["/operate", "/operate/targets", "/operate/runs", "/operate/bundles", "/api/status"]:
    with urllib.request.urlopen("http://192.0.2.1:8879" + path, timeout=5) as response:
        print(response.status, path)
PY
```

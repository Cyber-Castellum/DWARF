# Submission runbook — mixed adversarial Antithesis run

Steps 0–2 are **DONE** (2026-08-20). Step 3 spends money and is **not executed** — run it only when
you intend to. Design rationale and expected outcome: `RUN-DESIGN.md`.

All commands run on **cardano-box**.

---

## Mandatory MOOG fault-exclusion preflight

MOOG does **not** interpret `com.antithesis.exclude_from_faults` as a Boolean.
The value is a comma-separated vocabulary, and every token must be one of:

```text
network,kill,pause,stop
```

Never use `"true"`, `"false"`, YAML booleans, or an unrecognised token. The
invalid form below fails inside `moog agent push-test` before the Antithesis
launch POST, leaving the on-chain test fact in `pending` with no tenant run:

```yaml
# WRONG
com.antithesis.exclude_from_faults: "true"

# CORRECT: exclude this harness service from all supported fault classes
com.antithesis.exclude_from_faults: network,kill,pause,stop
```

Before spending a run, execute both gates from the repository root:

```bash
PYTHONPATH=dwarf python3 -m pytest -q \
  tests/test_antithesis_validation_gate.py \
  tests/test_moog_integration.py -k 'fault_exclusion or adversarial_bundle'
python3 dwarf/scripts/validate_scenarios.py
```

For the real MOOG parser boundary without contacting GAR or Antithesis, run
release MOOG `0.5.1.3` with the registry and launch URL pointed at a closed
localhost port. The expected result is `dockerPushFailure` for
`127.0.0.1:9`; `composeFaultExclusionParseFailure` means the bundle is still
invalid. Never use `moog-head` for this check.

This incident was fixed and live-verified on 2026-08-21. Public commit
`421c618e5170f75ee2c079fa58df4d4ac40ef6d7` produced MOOG test
`be5699260277d59ec13c29af54e6f88c473574683185b9bd19e6022e9dc1d0bc`,
which advanced to `accepted`; Antithesis run
`85b603edd96c90b12f0ca3e75cf00f3e-59-13` advanced to `in_progress` on the
`amaru-cardano` tenant.

---

## 0. Registry-publish blocker — RESOLVED (2026-08-20)

`docker login ghcr.io` succeeded as `J-GainSec`, but every push was refused with
`permission_denied: The token provided does not match expected scopes.`

Diagnosis: the configured package-publishing token was a **fine-grained** PAT —
`GET /user` returns **no `x-oauth-scopes` header**, the signature of fine-grained tokens — and
**ghcr.io only accepts classic PATs**. Ruled out along the way: no
alternate package credential source was configured. Also discovered
`dwarf-adversarial-oracle` had **never actually been published**, so all three pushes had to
*create* packages — exactly what a restricted token blocks.

**Why this was new:** the *original* `cardano_amaru_dwarf` bundle needed **no image builds** — every
image was already third-party public (intersectmbo / cardano-foundation / lambdasistemi). This
bundle is the first to require **custom** images (807 relay, seeded adversary, fixed oracle).

**Resolution:** a **classic** PAT with `write:packages` + `delete:packages` + `repo`, stored outside
the repository with mode 0600 and read without echoing. `repo` is required as well because MOOG
reads the same GitHub identity for the submit flow.

## 1. Publish the three images — DONE

All three are pushed and **public**, verified with an *anonymous* registry token (no docker
credentials involved), which is the check that catches the private-by-default trap:

| image | digest |
|---|---|
| `ghcr.io/j-gainsec/amaru-adv:807-k20` | `sha256:e7d3aa4c…6934c` |
| `ghcr.io/j-gainsec/dwarf-adversary-anti:0.10.0` | `sha256:0eb58fbf…163f15` |
| `ghcr.io/j-gainsec/dwarf-adversarial-oracle:0.1.1` | `sha256:d94dc5b7…a70c41` |

Re-verify anonymously at any time:

```bash
for r in amaru-adv:807-k20 dwarf-adversary-anti:0.10.0 dwarf-adversarial-oracle:0.1.1; do
  n="${r%%:*}"; t="${r##*:}"
  tok=$(curl -sS "https://ghcr.io/token?scope=repository:j-gainsec/$n:pull&service=ghcr.io" \
        | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
  code=$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $tok" \
    -H 'Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json' \
    "https://ghcr.io/v2/j-gainsec/$n/manifests/$t")
  [ "$code" = 200 ] && echo "PUBLIC  $r" || echo "NOT-PUBLIC($code)  $r"
done
```

> GitHub has **no REST endpoint** for package visibility (`PATCH /user/packages/...` → 404). It is
> web-UI only: Packages → package → Package settings → Change visibility → Public, confirming by
> typing the package name.

## 2. Point the compose at the published images — DONE

`docker-compose.yaml` now references the published images **by tag *and* digest**, so the
submitted run is pinned to exactly the bytes that were validated locally:

| was (local-only) | now (published, digest-pinned) |
|---|---|
| `dwarf/amaru-adv:807-k20` | `ghcr.io/j-gainsec/amaru-adv:807-k20@sha256:e7d3aa4c…` |
| `dwarf/adversary-anti:0.10.0` | `ghcr.io/j-gainsec/dwarf-adversary-anti:0.10.0@sha256:0eb58fbf…` |
| oracle default `…oracle:0.1.0` | `…oracle:0.1.1@sha256:d94dc5b7…` (the 807 trace-format fix) |

Pinning the oracle default to **0.1.1** matters: 0.1.0 carries the stale `ADOPT_RE` that would make
both `always` safety properties evaluate over zero samples. Local validation only got the fix via a
`DWARF_ORACLE_IMAGE` override; the submitted bundle must not depend on that.

Provenance checks performed before committing (all passed):

- The four changed image lines are the **only** delta against the box copy that was validated.
- `relay-image/{Dockerfile,entrypoint.sh,make-store.sh}` and
  `adversary-image/{Dockerfile,seed-entrypoint.sh}` are SHA-256 identical to the sources the pushed
  images were built from on the box.
- `oracle/oracle.py` in this repo is SHA-256 identical to `/oracle.py` **inside the published
  0.1.1 image** (`0e2e0740…`) — i.e. the fix is really in the image, not only in the repo.

Commit the bundle to `Cyber-Castellum/DWARF` and note the SHA — moog submits a repo + directory +
commit, so the SHA must contain this compose, both image contexts, and the fixed `oracle/oracle.py`.

> The compose is the **`d807-` prefixed** local-validation variant (containers and networks renamed
> to avoid colliding with other deployments on the box). Those prefixes are harmless on Antithesis
> but cosmetic — optionally strip them for the submitted copy.

## 3. Submit (SPENDS A PAID RUN)

> ### Submit `try 1` at **`-t 1`**, never `try 1` at `-t 3`
>
> The first attempt at this (2026-08-20, commit `46f3089`) used `--try 1 -t 3` and the oracle
> **never adjudicated it** — it sat in `phase: pending` indefinitely while requests submitted after
> it were processed, and it was the only pending test-run out of 1505 on the token.
>
> Across all 41 DWARF requests ever submitted the pattern is absolute:
>
> | try / duration | count |
> |---|---|
> | `try 1`, 1h | 28 |
> | `try 2`, 3h | 10 |
> | `try 2`, 1h | 2 |
> | **`try 1`, 3h** | **1 — the one that hung** |
>
> Every 3-hour run in history was `try 2`, always preceded by a `try 1` 1-hour run at the same
> commit. This matches CF's own guidance (moog workbench,
> `dwarf-antithesis-live-run-checklist.html`, 2026-06-08): **"1-hour tests by default (3-hour only
> with a compelling reason)"** — a first-try 3h request appears to need manual approval rather than
> being auto-adjudicated.
>
> So: **`--try 1 -t 1` first. Then `--try 2 -t 3` at the same commit.**

```bash
# <protected-requester-secrets.yaml> is outside the repository, mode 0600,
# and supplies tokenId, mpfsHost, githubPAT, walletFile, and walletPassphrase.
moog --secrets-file <protected-requester-secrets.yaml> requester create-test \
  -p github \
  -r Cyber-Castellum/DWARF \
  -d antithesis/cardano_amaru_adversarial \
  -c <COMMIT_SHA_OF_THE_FIXED_BUNDLE_ON_THE_PUBLIC_REPO> \
  --try 1 \
  -u j-gainsec \
  -t 1                      # 1 hour; omit --no-faults so FAULTS ARE ON
```

Faults **must** be on — with faults off this is just the local validation at a price.

Once `try 1` reaches `phase: finished`, escalate to the full run at the **same commit**:

```bash
moog --secrets-file <protected-requester-secrets.yaml> requester create-test \
  -p github -r Cyber-Castellum/DWARF -d antithesis/cardano_amaru_adversarial \
  -c <SAME_COMMIT_SHA> \
  --try 2 \
  -u j-gainsec \
  -t 3
```

An hour of faults × adversarial input is already a real result — the properties are the same, only
the number of explored interleavings differs. Treat `try 1` as the experiment, not as a formality.

### Image references must be literal

Do **not** use `${VAR:-default}` in an `image:` field. Both DWARF bundles that failed to launch
carried one, and every bundle that ran had none:

| bundle | `${}` in `image:` | outcome |
|---|---|---|
| `amaru_baked_dwarf` | 2 | **rejected — `reasons: ["broken instructions"]`** |
| `cardano_amaru_adversarial` (first attempt) | 1 | **stuck `pending`** |
| `cardano_node_dwarf`, `_baked`, `_eclipse`, `cardano_amaru_dwarf` | 0 | all launched |

If the config builder does not expand environment variables the reference stays literal and cannot
resolve. The forensic run ledger records the same failure class: of 29 runs, the 4 that never
produced a result were "2 image-build failures and 2 setup-deaths on stripped bundles — both
packaging/registry issues, not node defects."

## 4. Monitor

```bash
moog antithesis runs
moog antithesis run --run-id <ID>
moog antithesis properties --run-id <ID> --limit 50 --cursor <n>   # paginate: moog #187 truncates at 50
moog antithesis logs --run-id <ID>
```

## What a result means

- **All `always` hold + both `sometimes` fire** → Amaru held consensus safety under
  fault × adversarial input. The expected outcome; reportable, not a finding.
- **A `sometimes` never fires** → the run was vacuous (adversary not exercising, or the target not
  adopting). Treat as an invalid run, not a pass — check the oracle traces first.
- **An `always` fails** → a genuine finding: Amaru adopted or advanced on something forged that the
  honest control did not. Antithesis can replay it deterministically; capture the replay immediately.

## Pre-flight checklist

- [x] Three images pushed **and public** (verified with an anonymous registry token)
- [x] Compose references the published images by digest, oracle pinned to `0.1.1`
- [x] Fault-exclusion labels contain only `network,kill,pause,stop`; no Boolean-like values
- [x] `python3 dwarf/scripts/validate_scenarios.py` reports `moog asset failures: 0`
- [x] Published to `Cyber-Castellum/DWARF` @ **`421c618e5170f75ee2c079fa58df4d4ac40ef6d7`**
- [x] `try 1`, `-t 1`, faults ON submitted through release MOOG `0.5.1.3`
- [x] MOOG fact reached `accepted`; matching `amaru-cardano` run reached `in_progress`
- [ ] Wait for `try 1` to finish and triage properties before deciding whether to submit `try 2`

# Submission runbook — mixed adversarial Antithesis run

The original image publication is complete and the corrected `-1` baseline run
has finished. The five-case corpus is published but has **not** been submitted.
Any new Step 3 request spends money; run it only with explicit approval. Design
rationale and expected outcome: `RUN-DESIGN.md`.

All commands run on **cardano-box**.

---

<<<<<<< HEAD
## 2026-08-22 five-case fee corpus — FINDING CONFIRMED, RUN NOT SUBMITTED

The corrected baseline run completed successfully at commit
`6082f0eedabe478801051a661435a5ffb3424f47`:

- Antithesis run: `0195e57647d3ee8322893e6f8af159a5-59-13`;
- MOOG test-run: `f1334b7d8425d76f219d62dd0c01f462e35c6faf9f987b11f6b86c2ad83a9bd7`;
- all original mixed `-1` properties passed;
- the only overall failure was the known Cardano `cluster fork depth < k` property.

The next bundle extends the same property to deltas `-100`, `-2`, `-1`, exact,
and `+1`. Scheduling is load-bearing:

- `first_fee_valid_boundaries.py` probes readiness with `-1`, then sends exact and
  `+1` once each before faults;
- `parallel_driver_underfee_corpus.py` selects only negative cases using
  `antithesis.random.random_choice`;
- `eventually_underfee_recovery.py` also selects only a negative case and retries
  after faults stop;
- `parallel_driver_underfee.py` remains for historical `-1` continuity.

The public workload image is:

```text
ghcr.io/j-gainsec/dwarf-mixed-phase1-workload@sha256:26d02ae22e0d802b78285a3b19bd53687a257dd77a12879831b5262a4792811d
```

It was built from the bundle root with `workload/Dockerfile`, pushed as
`fee-corpus-v2-20260822`, and anonymously fetched with HTTP 200. Compose must retain
the digest-only reference above. The safe rebuild flow reads the existing
mode-0600 token through stdin; never place the token in argv or output:

```bash
docker build -f workload/Dockerfile \
  -t ghcr.io/j-gainsec/dwarf-mixed-phase1-workload:<new-tag> .
docker login ghcr.io -u J-GainSec --password-stdin \
  < /home/nigel/moog-secrets/ghcr.token
docker push ghcr.io/j-gainsec/dwarf-mixed-phase1-workload:<new-tag>
```

Do not submit until the final test suite, Compose render, official `snouty
validate`, public-safety scan, and public commit are complete. A paid run still
requires explicit approval.

Run the signed-corpus verifier directly from the bundle root; it supplies
`cardano-cli` through a digest-pinned public image, so no host installation is
required:

```bash
antithesis/cardano_amaru_adversarial/fixture/verify-corpus-container.sh
```

Preflight already established the expected report outcome: Cardano accepts the
exact-minimum and `+1` cases while Amaru rejects them; both accept at `+44`.
The root cause is Amaru mempool validation counting the standalone transaction's
one-byte `is_valid` field, unlike its block-validation path. Do not reinterpret
an Antithesis failure of the two valid-case properties as a harness fault. The
run is useful to capture this formally and explore fault interaction, but the
base finding is deterministic and independently reproduced.

---

=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
## 2026-08-22 relay entrypoint incident — FIX REQUIRED BEFORE REPLACEMENT RUN

MOOG test-run
`e512a7b37d7d23b7b8a5dfe6e908d9c3b5794f64b861a0e2372a9862b77c3d3f`
launched commit `60ccdf4c1d9b72fcb426a5458ae89adeec82c9b1`, but is not a valid
mixed Cardano/Amaru experiment. Antithesis recorded both relay containers in a
restart loop with:

```text
exec: /usr/local/bin/dwarf-amaru-entrypoint.sh: Permission denied
```

The Cardano reference and workload ran, but every Amaru submission was
unavailable. The run emitted 157 failing classifiability assertions and zero
`both implementations returned classifiable phase-1 results` events. Do not use
this run for a differential conclusion or submit a longer follow-up at that
commit.

The repository file is mode `100755`; the unsafe assumption was that an
Antithesis bind-mounted repository script remains directly executable. Both
relay commands must invoke it through the interpreter:

```sh
exec /bin/sh /usr/local/bin/dwarf-amaru-entrypoint.sh
```

<<<<<<< HEAD
`workload/tests/test_bundle_contract.py` enforces this for both relays. The
replacement one-hour `try 1` used fixed commit `6082f0e`, completed, and proved
both Amaru startup and mixed classifiability. Retain the guardrail for every
future run.

---

## 2026-08-22 original mixed phase-1 addendum — BASELINE COMPLETED
=======
`workload/tests/test_bundle_contract.py` enforces this for both relays. A
replacement one-hour `try 1` must use a new public commit containing that
guardrail and must be checked for both Amaru process startup and a reachable
mixed classifiability event before any longer run.

---

## 2026-08-22 mixed phase-1 addendum — READY FOR PUBLIC COMMIT
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47

The bundle now includes a same-byte under-fee differential. Do not restore the old
runtime fixture builder or `utxo-keys` volume: a fresh configurator UTxO is not present
in Amaru's baked store and yields `failed to prepare transaction ... for validation`,
which is not phase-1 evidence.

New published images pinned in Compose:

| image | digest |
|---|---|
| `ghcr.io/j-gainsec/dwarf-cardano-phase1-reference` | `sha256:cadd549396712c649f6f5683fab36fa6c183b8db0f85440a82a05b59bbcb39e4` |
| `ghcr.io/j-gainsec/dwarf-mixed-phase1-workload` | `sha256:31dc030ed0cd5884fa36ce0b230609167678f44ca55d565dc4169dafb018a0a1` |
| `ghcr.io/j-gainsec/dwarf-adversary-anti` (seed sanitization) | `sha256:e99cb81ffc51465042b77ac3100d18092f69a25c36c66fff4954663b7200d2bd` |

Verification completed on `cardano-box`:

- 23 unit/contract tests pass after the relay-entrypoint regression guard;
- image added paths contain no `.skey`, key, PEM, or environment files;
- same-byte smoke gives Cardano `FeeTooSmallUTxO` and Amaru validation rejection;
- official `snouty validate` detects setup-complete, one driver, and one eventual
  command using the published digests.

Anonymous manifest checks return HTTP 200 for `dwarf-cardano-phase1-reference`,
`dwarf-mixed-phase1-workload`, and the sanitized `dwarf-adversary-anti` tag.

<<<<<<< HEAD
The original image and digest remain provenance for the completed baseline; the
five-case run must use the newer digest documented above. Before MOOG submission,
commit and push this exact directory, verify no `._*` files, and use release
`moog` 0.5.1.3—not `moog-head`. The target tenant is
=======
Before MOOG submission, commit and push this exact directory, verify no `._*` files,
and use release `moog` 0.5.1.3—not `moog-head`. The target tenant is
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
`amaru-cardano`; the requested repository/directory remain
`Cyber-Castellum/DWARF` and `antithesis/cardano_amaru_adversarial`.

---

## 0. Registry-publish blocker — RESOLVED (2026-08-20)

`docker login ghcr.io` succeeded as `J-GainSec`, but every push was refused with
`permission_denied: The token provided does not match expected scopes.`

Diagnosis: the token in `var/state/config.yaml` (`moog.github_pat`) is a **fine-grained** PAT —
`GET /user` returns **no `x-oauth-scopes` header**, the signature of fine-grained tokens — and
**ghcr.io only accepts classic PATs**. Ruled out along the way: no
`~/moog-secrets/requester/secrets.yaml` (wallet + passphrase only), no GCP Artifact Registry
credentials on the box, no other token on the moog workbench. Also discovered
`dwarf-adversarial-oracle` had **never actually been published**, so all three pushes had to
*create* packages — exactly what a restricted token blocks.

**Why this was new:** the *original* `cardano_amaru_dwarf` bundle needed **no image builds** — every
image was already third-party public (intersectmbo / cardano-foundation / lambdasistemi). This
bundle is the first to require **custom** images (807 relay, seeded adversary, fixed oracle).

**Resolution:** a **classic** PAT with `write:packages` + `delete:packages` + `repo`, written by the
token owner to `~/moog-secrets/ghcr.token` (mode 0600) and read via a pipe, never echoed. `repo` is
required as well because moog reads the same GitHub identity for the submit flow.

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
export MOOG_TOKEN_ID=$(python3 -c "import yaml;print(yaml.safe_load(open('/home/nigel/dwarf-v4/var/state/config.yaml'))['moog']['token_id'])")
export MOOG_MPFS_HOST=https://mpfs.plutimus.com
export MOOG_GITHUB_PAT=$(python3 -c "import yaml;print(yaml.safe_load(open('/home/nigel/dwarf-v4/var/state/config.yaml'))['moog']['github_pat'])")
export MOOG_WALLET_PASSPHRASE="$(cat /home/nigel/moog-secrets/requester/wallet.passphrase)"
# NB the wallet file is requester.json (holds `encryptedMnemonics`); there is no wallet.json.

/home/nigel/bin/moog requester create-test \
  -w /home/nigel/moog-secrets/requester/requester.json \
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
/home/nigel/bin/moog requester create-test \
  -w /home/nigel/moog-secrets/requester/requester.json \
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
- [x] Original corrected baseline published and completed at public commit
      `6082f0eedabe478801051a661435a5ffb3424f47`.
- [ ] Five-case corpus commit pushed to `Cyber-Castellum/DWARF`; submit the
      resulting public SHA, never a local-only commit.
- [ ] Corpus `try 1` uses `-t 1`, faults ON (no `--no-faults`).
- [ ] Explicit approval to spend the corpus run.
- [ ] Approval to spend the run

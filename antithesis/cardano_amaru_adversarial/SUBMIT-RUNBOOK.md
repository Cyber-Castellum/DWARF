# Submission runbook — mixed adversarial Antithesis run

Steps 0–2 are **DONE** (2026-08-20). Step 3 spends money and is **not executed** — run it only when
you intend to. Design rationale and expected outcome: `RUN-DESIGN.md`.

All commands run on **cardano-box**.

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

```bash
export MOOG_TOKEN_ID=$(python3 -c "import yaml;print(yaml.safe_load(open('/home/nigel/dwarf-v4/var/state/config.yaml'))['moog']['token_id'])")
export MOOG_MPFS_HOST=https://mpfs.plutimus.com
export MOOG_GITHUB_PAT=$(python3 -c "import yaml;print(yaml.safe_load(open('/home/nigel/dwarf-v4/var/state/config.yaml'))['moog']['github_pat'])")
export MOOG_WALLET_PASSPHRASE="$(cat /home/nigel/moog-secrets/requester/wallet.passphrase)"

/home/nigel/bin/moog requester create-test \
  -w /home/nigel/moog-secrets/requester/wallet.json \
  -p github \
  -r Cyber-Castellum/DWARF \
  -d antithesis/cardano_amaru_adversarial \
  -c daada6e50c704aeb938da5526684b2e1eee0363b \
  --try 1 \
  -u j-gainsec \
  -t 3                      # 3 hours; omit --no-faults so FAULTS ARE ON
```

Faults **must** be on — with faults off this is just the local validation at a price.

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
- [x] Bundle committed locally: `daada6e50c704aeb938da5526684b2e1eee0363b`
- [ ] Pushed to the public `Cyber-Castellum/DWARF` (moog fetches the repo — it must be reachable there)
- [ ] `-t 3`, faults ON (no `--no-faults`)
- [ ] Approval to spend the run

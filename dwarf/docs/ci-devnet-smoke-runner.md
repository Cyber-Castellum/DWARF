# DWARF devnet-smoke — self-hosted runner setup

Phase 2 of running the tests in CI. The `DWARF devnet smoke` workflow
(`.github/workflows/dwarf-devnet-smoke.yml`) runs a curated subset of self-provisioning
cardano-node devnet scenarios through the composer. These need Docker multi-node meshes + epoch
warmup, which stock GitHub-hosted runners can't do — so the job runs on a **self-hosted runner**.

## ⚠️ Security — self-hosted runner on a public repo

A self-hosted runner executes workflow code **on your host**. For a public repo this is dangerous
for **fork pull requests**, which could run arbitrary code on the box. Mitigations, already baked in:

- The workflow triggers **only** on `push` to `main`, `schedule`, and manual `workflow_dispatch` —
  **never** on `pull_request`. Do not add `pull_request`.
- In **Settings → Actions → General → Fork pull request workflows**, keep fork PRs from running
  workflows (require approval), and prefer a **dedicated, isolated** host that holds no secrets you
  care about beyond what the smoke needs.
- Register the runner at the **repo** level (not org-wide) so its blast radius is one repo.

## Runner host requirements

The host must have:
- Docker + Compose v2 (verified: docker 29.x, compose v2 on the current host).
- The `dwarf/cardano-node` images the curated scenarios pin — currently **`dwarf/cardano-node:10.7.1`**
  (`docker images | grep dwarf/cardano-node`).
- Host `cardano-cli` and `cardano-testnet` on `PATH` (the composer/observer shell out to them).
- The DWARF runtime Python deps (the box that runs the dashboard already has them).

The smoke wrapper (`dwarf/scripts/ci_devnet_smoke.sh`) tears down **only** the substrate projects it
creates and never touches other containers, so long-running devnets on the same host are safe.

## Register the runner (one time)

On GitHub: **Settings → Actions → Runners → New self-hosted runner** (Linux x64). Copy the token it
shows, then on the host:

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o actions-runner.tar.gz -L \
  https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64.tar.gz  # use the version GitHub shows
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/Cyber-Castellum/DWARF \
  --token <RUNNER_TOKEN_FROM_GITHUB> \
  --labels dwarf-devnet \
  --name dwarf-devnet-host --unattended
# run it as a service so it survives logout:
sudo ./svc.sh install
sudo ./svc.sh start
```

The workflow targets `runs-on: [self-hosted, dwarf-devnet]`, so the `dwarf-devnet` label is required.

## Run it

- Automatically on every push to `main` that touches the substrate scenarios / composer / this
  workflow, and nightly at 05:30.
- Manually: **Actions → DWARF devnet smoke → Run workflow** (optionally pass explicit scenario paths).

Green = every curated scenario passed its liveness assertions (`all_nodes_started_clean`,
`peer_connectivity_observed`, `all_nodes_responsive`) and the mesh was torn down. Run outputs are
uploaded as the `dwarf-devnet-smoke-runs` artifact.

## Curated subset (deterministic liveness only)

Kept intentionally to **reliably-green** scenarios so a red result means a real regression:

- `runtime-substrate-honest-baseline-docker-mode-example-smoke.yaml`
- `runtime-substrate-cardano-node-mixed-minor-phase1-smoke.yaml`

Both are 2-node cardano-node on `testnet_42` (fast fake epochs) with liveness-only assertions.
Fault-injection / differential scenarios (blockfetch faults, chainsync fork-switch, consensus
soaks) are **not** in the smoke gate — their oracles are timing-sensitive and belong in longer,
non-gating campaign runs. Add scenarios by passing paths to the wrapper or editing its `SCENARIOS`.
